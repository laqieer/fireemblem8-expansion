"""Bounded review convergence. Tool provenance belongs to the coordinator."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from types import SimpleNamespace
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
SCOPE_PATTERN = rf"(?:{NAME_PATTERN}|{CASE_PATTERN}/{NAME_PATTERN})"
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
COPILOT_REVIEWER_ID = "BOT_kgDOCnlnWA"
FAILED_REVIEW_BODY = (
    "Copilot encountered an error and was unable to review this pull request. "
    "You can try again by re-requesting a review."
)
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
    require("\0" not in value and "\\" not in value and not candidate.is_absolute() and
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


def resolve_existing_repo_root(value: Any, label: str) -> str:
    require(isinstance(value, (str, os.PathLike)) and not isinstance(value, bytes),
            f"invalid {label}")
    try:
        candidate = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise ReviewError(f"invalid {label}") from error
    require(candidate.is_absolute() and candidate.is_dir(),
            f"invalid {label}")
    return frozen_repo_root(str(candidate), label)


def frozen_repo_root(value: Any, label: str) -> str:
    require(isinstance(value, (str, os.PathLike)) and not isinstance(value, bytes),
            f"invalid {label}")
    path = os.fspath(value)
    require(isinstance(path, str) and bool(path), f"invalid {label}")
    candidate = PurePosixPath(path)
    require("\0" not in path and "\\" not in path and candidate.is_absolute() and
            "." not in candidate.parts and ".." not in candidate.parts and
            candidate.as_posix() == path, f"invalid {label}")
    return path


def unique(values, label: str) -> None:
    require(len(values) == len(set(values)), f"duplicate {label}")


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"), "UTC timestamp required")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReviewError("invalid UTC timestamp") from error


def is_review_activity(event):
    kind = event.get("type")
    return kind in {"user.message", "assistant.message", "assistant.turn_start"} or (
        isinstance(kind, str) and kind.startswith(("tool.", "subagent."))
    )


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


@dataclass(frozen=True)
class CandidateRequirements:
    resolved_root: str
    base: str
    head: str
    changes: tuple[CandidatePathChange, ...]


def validate_candidate_binding(value: Any, label: str) -> CandidateBinding:
    return CandidateBinding(
        frozen_repo_root(field_value(value, "resolved_root"), label + " root"),
        sha(field_value(value, "base")),
        sha(field_value(value, "head")),
    )


def candidate_tree_binding(reader: Any) -> CandidateBinding:
    base_tree = field_value(reader, "base_tree")
    head_tree = field_value(reader, "head_tree")
    require(base_tree is not _MISSING and head_tree is not _MISSING,
            "trusted candidate reader requires both immutable Git tree bindings")
    binding = CandidateBinding(
        frozen_repo_root(field_value(base_tree, "root"),
                         "trusted candidate reader Git tree root"),
        sha(field_value(base_tree, "revision")),
        sha(field_value(head_tree, "revision")),
    )
    require(binding.resolved_root == frozen_repo_root(
        field_value(head_tree, "root"),
        "trusted candidate reader Git tree root"),
        "trusted candidate reader Git trees disagree about root")
    return binding


def candidate_requirements(value) -> CandidateRequirements:
    require(type(value) is CandidateRequirements,
            "actual immutable candidate path requirements required")
    root = frozen_repo_root(value.resolved_root, "candidate path requirements root")
    base = sha(value.base)
    head = sha(value.head)
    changes = value.changes
    require(type(changes) is tuple and bool(changes),
            "candidate path requirements must be a nonempty immutable tuple")
    require(all(type(item) is CandidatePathChange for item in changes),
            "candidate path requirements rows must be exact candidate path changes")
    validated = tuple(validate_candidate_change(item) for item in changes)
    unique([item.path for item in validated], "candidate path requirements")
    return CandidateRequirements(root, base, head, validated)


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
    require(report.completed is True and report.logical_closed is True
            and bool(report.instruction_commitment.strip()) and report.role == "code-review",
            "review report is not a completed immutable review")
    root = report.candidate_root
    base = report.candidate_base
    reads = report.candidate_reads
    if base is None:
        require(root is None and reads == (), "review report has unexpected candidate coverage state")
        return None
    root = frozen_repo_root(root, "candidate coverage root")
    base = sha(base)
    head = sha(report.head)
    require(type(reads) is tuple, "candidate coverage must be an immutable tuple")
    require(all(type(item) is CandidateReadSummary for item in reads),
            "candidate coverage rows must be exact candidate read summaries")
    validated = tuple(validate_candidate_read_summary(item, base_sha=base, head_sha=head) for item in reads)
    unique([(item.path, item.side) for item in validated], "candidate read coverage")
    return CandidateCoverage(root, base, head, validated)


def require_candidate_path_coverage(report, requirements) -> CandidateCoverage:
    expected = candidate_requirements(requirements)
    coverage = candidate_coverage(report)
    require(coverage is not None, "review report has no trusted candidate path coverage")
    require(coverage.resolved_root == expected.resolved_root,
            "review candidate path coverage root mismatch")
    require((coverage.base, coverage.head) == (expected.base, expected.head),
            "review candidate path coverage pair mismatch")
    observed = {(item.path, item.side): item for item in coverage.reads}
    for change in expected.changes:
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
    detail: str = ""


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
        require(self.outcome in {"clean", "changes-requested", "untriaged", "dismissed", "failed"},
                "unknown coordinator triage")
        require(len(self.findings) <= MAX_FINDINGS, "review finding budget exceeded")
        sha(self.fact.head)
        timestamp(self.fact.submitted_at)
        require(isinstance(self.fact.body, str), "invalid review body")
        unique([item.id for item in self.findings], "accepted findings")
        for finding in self.findings:
            require(finding.origin == self.fact.head and finding.review_id == self.fact.id,
                    "finding has wrong review origin")
        error_response = (self.fact.actor == COPILOT_REVIEWER_ID
                          and FAILED_REVIEW_BODY in self.fact.body)
        failed_review = (error_response and self.fact.body == FAILED_REVIEW_BODY
                         and self.fact.state == "COMMENTED")
        if self.outcome == "clean":
            require(not self.findings and not self.fact.unresolved_threads
                    and self.fact.state in {"COMMENTED", "APPROVED"} and not error_response,
                    "clean triage contradicts review facts")
        if self.outcome == "dismissed":
            require(self.fact.state == "DISMISSED", "dismissed triage requires an actual dismissal")
        if self.outcome == "failed":
            require(failed_review and not self.findings and not self.fact.comments
                    and not self.fact.unresolved_threads,
                    "failed triage requires the unambiguous Copilot error without findings")


@dataclass(frozen=True)
class Disposition:
    review_id: str
    held_head: str
    coordinator: str
    action: str
    reason: str


@dataclass(frozen=True)
class _ReviewReport:
    """Validated coordinator observations, not a runtime-owned record or receipt."""
    task: str
    owner: str
    role: str
    head: str
    subjects: frozenset[str]
    completed: bool
    actions: frozenset[str]
    files: int
    findings: tuple[Finding, ...]
    started_at: str
    completed_at: str
    instruction_commitment: str
    logical_closed: bool
    content: str
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


def _cli_event_key(record):
    require(isinstance(record, (tuple, list)) and len(record) == 2,
            "invalid CLI event record")
    session, event = record
    require(isinstance(session, str) and isinstance(event, dict),
            "invalid CLI event record")
    try:
        serialized = json.dumps(
            (session, event), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("ascii")).hexdigest()
    except (TypeError, ValueError) as error:
        raise ReviewError("CLI event record is not serializable") from error


def validate_reviewer_activity(events, *, owner, dispatch, interaction,
                               started_at, completed_at):
    require(isinstance(events, (tuple, list)), "invalid CLI event snapshot")
    start_time = timestamp(started_at)
    completion_time = timestamp(completed_at)
    require(start_time <= completion_time, "review task chronology is invalid")
    activity = tuple(
        record for record in events
        if isinstance(record, (tuple, list)) and len(record) == 2
        and isinstance(record[1], dict) and record[1].get("agentId") == owner
        and is_review_activity(record[1]))
    turn_ids = {
        event.get("data", {}).get("turnId")
        for _, event in activity
        if isinstance(event.get("data"), dict)
        and event["data"].get("interactionId") == interaction
        and isinstance(event["data"].get("turnId"), str)
    }
    for record in activity:
        _, event = record
        kind = event.get("type")
        data = event.get("data")
        require(isinstance(data, dict), "reviewer event data is malformed")
        event_time = timestamp(event.get("timestamp"))
        require(start_time <= event_time <= completion_time,
                "reviewer activity is outside its assigned task")
        event_interaction = data.get("interactionId")
        if kind in {"subagent.configured", "subagent.selected"}:
            require(data.get("toolCallId") in (None, dispatch)
                    and event_interaction in (None, interaction)
                    and (kind != "subagent.selected"
                         or data.get("agentName") == "code-review"),
                    "reviewer configuration contradicts its assignment")
        elif isinstance(kind, str) and kind.startswith("subagent."):
            require(kind in {"subagent.started", "subagent.completed"}
                    and data.get("toolCallId") == dispatch,
                    "reviewer activity belongs to another dispatch")
            if kind == "subagent.started":
                require(event_interaction == interaction,
                        "reviewer dispatch has a contradictory interaction")
            elif event_interaction is not None:
                require(event_interaction == interaction,
                        "reviewer dispatch has a contradictory interaction")
        elif kind in {"user.message", "assistant.message"}:
            require(event_interaction == interaction,
                    "reviewer message has a contradictory interaction")
        elif kind == "assistant.turn_start":
            if event_interaction is not None:
                require(event_interaction == interaction,
                        "reviewer turn has a contradictory interaction")
            else:
                require(data.get("turnId") in turn_ids,
                        "reviewer turn has no verifiable interaction")
        elif isinstance(kind, str) and kind.startswith("tool."):
            require(kind in {"tool.execution_start", "tool.execution_complete"},
                    "reviewer tool event type is unsupported")
            if event_interaction is not None:
                require(event_interaction == interaction,
                        "reviewer tool event has a contradictory interaction")
            else:
                require(kind == "tool.execution_start"
                        and data.get("turnId") in turn_ids,
                        "reviewer tool event has no verifiable interaction")
    return frozenset(_cli_event_key(record) for record in activity)


@dataclass
class _OwnedReview:
    identity: tuple
    head: str
    scope: frozenset[str]
    owner: str
    namespace: str | None
    dispatch: str | None = None
    state: str = "reserved"
    interaction: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    admitted_event_keys: frozenset[str] = frozenset()
    event_sessions: frozenset[str] = frozenset()
    report: _ReviewReport | None = None
    invalidation_reason: str | None = None


class ReviewOwnership:
    """Small in-process index owned/shared by the existing coordinator."""

    @staticmethod
    def _same_owner_scope(left, right):
        return left[1] == right[1] and (
            left[0] is None or right[0] is None or left[0] == right[0])

    def __init__(self):
        self.records: dict[object, _OwnedReview] = {}

    def record_for(self, session):
        return None if session is None else self.records.get(session._ownership_key)

    def _has_closed_owner(self, owner_key):
        return any(
            record.state == "terminal"
            and self._same_owner_scope(owner_key, (record.namespace, record.owner))
            for record in self.records.values())

    def _has_closed_dispatch(self, dispatch_namespace, task):
        return dispatch_namespace is not None and any(
            record.state == "terminal" and record.namespace == dispatch_namespace
            and record.dispatch == task for record in self.records.values())

    def is_closed(self, dispatch_namespace, task, owner):
        return (self._has_closed_dispatch(dispatch_namespace, task)
                or self._has_closed_owner((dispatch_namespace, owner)))

    def reserve(self, session):
        require(session.identity is not None, "ownership requires frozen PR identity")
        owner_key = session.dispatch_namespace, session.owner
        require(not self._has_closed_owner(owner_key),
                "reviewer task owner was already logically closed")
        for record in self.records.values():
            require(not (record.state != "terminal" and
                         (record.identity[:2] == session.identity[:2] or record.head == session.head
                          or self._same_owner_scope((record.namespace, record.owner), owner_key))),
                    "duplicate/overlapping review ownership")
        self.records[session._ownership_key] = _OwnedReview(
            session.identity, session.head, session.scope, session.owner,
            session.dispatch_namespace)

    def bind_dispatch(self, session, task):
        record = self.records[session._ownership_key]
        require(record.dispatch is None or record.dispatch == task,
                "review dispatch identity changed after binding")
        require(not self.is_closed(record.namespace, task, record.owner),
                "review task was already logically closed")
        require(record.namespace is None or not any(
            other is not record and other.namespace == record.namespace
            and other.dispatch == task for other in self.records.values()),
                "review dispatch already has an owner")
        record.dispatch = task

    def release_before_dispatch(self, session):
        self.records.pop(session._ownership_key, None)

    def finish(self, session, task, owner, interaction, started_at, completed_at,
               dispatch_namespace=None, admitted_event_keys=(), admitted_event_sessions=(),
               event_source=(), report=None):
        owner_key = dispatch_namespace, owner
        record = self.records[session._ownership_key]
        require(record.state != "terminal" and record.dispatch == task
                and (record.namespace, record.owner) == owner_key,
                "review owner or dispatch changed before logical closure")
        identifier(owner, NAME_PATTERN, "reviewer identity")
        identifier(interaction, NAME_PATTERN, "review interaction")
        start = timestamp(started_at)
        end = timestamp(completed_at)
        require(start <= end, "review task chronology is invalid")
        if report is not None:
            require(not any(
                other is not record and other.state == "terminal"
                and (other.identity[:2] == record.identity[:2] or other.head == record.head)
                and start <= timestamp(other.completed_at)
                and timestamp(other.started_at) <= end
                for other in self.records.values()),
                "overlapping review task observations")
        require(isinstance(admitted_event_keys, (tuple, list, set, frozenset))
                and all(isinstance(item, str) for item in admitted_event_keys),
                "invalid admitted CLI event keys")
        require(isinstance(admitted_event_sessions, (tuple, list, set, frozenset))
                and all(isinstance(item, str) and bool(item.strip())
                        for item in admitted_event_sessions),
                "invalid admitted CLI event sessions")
        for event_session in admitted_event_sessions:
            identifier(event_session, NAME_PATTERN, "review event session")
        sessions = frozenset(admitted_event_sessions)
        require(not any(
            other is not record and other.state == "terminal" and other.owner == owner
            and (dispatch_namespace is None or other.namespace is None
                 or bool(sessions & other.event_sessions))
            for other in self.records.values()),
            "review event session was already logically closed")
        record.state = "terminal"
        record.interaction = interaction
        record.started_at = started_at
        record.completed_at = completed_at
        record.admitted_event_keys = frozenset(admitted_event_keys)
        record.event_sessions = sessions
        session.event_sources.append(event_source)
        record.report = report

    def validate_closed_events(self, events, *, snapshot_namespace=None, snapshot_owner=None):
        require(isinstance(events, (tuple, list)), "invalid CLI event snapshot")
        require(snapshot_namespace is None
                or isinstance(snapshot_namespace, str) and bool(snapshot_namespace.strip()),
                "invalid CLI event snapshot namespace")
        if snapshot_owner is not None:
            snapshot_keys = {}
            for record in events:
                if (not isinstance(record, (tuple, list)) or len(record) != 2
                        or not isinstance(record[0], str) or not isinstance(record[1], dict)):
                    continue
                try:
                    event_key = _cli_event_key(record)
                except ReviewError:
                    continue
                snapshot_keys.setdefault(record[0], set()).add(event_key)
            for closed in self.records.values():
                if (closed.state != "terminal" or closed.namespace != snapshot_namespace
                        or closed is not self.record_for(snapshot_owner)
                        or not closed.admitted_event_keys
                        or closed.invalidation_reason is not None):
                    continue
                present = set()
                for session in closed.event_sessions:
                    present.update(snapshot_keys.get(session, set()))
                if not closed.admitted_event_keys <= present:
                    closed.invalidation_reason = (
                        "an admitted review event was changed or removed after logical closure")
        for record in events:
            if not isinstance(record, (tuple, list)) or len(record) != 2:
                continue
            event_session, event = record
            if not isinstance(event, dict):
                continue
            if not is_review_activity(event):
                continue
            agent = event.get("agentId")
            data = event.get("data")
            for closed in self.records.values():
                dispatch_event = (
                    closed.namespace is not None and event_session == closed.namespace
                    and isinstance(data, dict) and data.get("toolCallId") == closed.dispatch
                    and event.get("type") in {"tool.execution_start", "tool.execution_complete",
                                               "subagent.started", "subagent.completed"})
                if (closed.state != "terminal" or (closed.owner != agent and not dispatch_event)
                        or (isinstance(event_session, str) and closed.namespace is not None
                            and event_session not in closed.event_sessions)):
                    continue
                try:
                    event_key = _cli_event_key(record)
                except ReviewError:
                    message = "review task emitted an unrecognized event record"
                else:
                    if event_key in closed.admitted_event_keys:
                        continue
                    try:
                        event_time = timestamp(event.get("timestamp"))
                    except ReviewError:
                        message = "review task emitted an event with invalid chronology"
                    else:
                        message = None
                    data = event.get("data")
                    if message is not None:
                        pass
                    elif not isinstance(data, dict):
                        message = "review task emitted an event with invalid data"
                    elif event_time > timestamp(closed.completed_at):
                        message = "review task emitted an event after logical closure"
                    elif data.get("interactionId") not in (None, closed.interaction):
                        message = "review task emitted a contradictory interaction"
                    else:
                        message = "new review activity was observed after logical closure"
                if closed.invalidation_reason is None:
                    closed.invalidation_reason = message

    def validate_session(self, session):
        record = self.record_for(session)
        if record is not None:
            for source in session.event_sources:
                self.validate_closed_events(source)
            require(record.invalidation_reason is None,
                    f"review task evidence was invalidated: {record.invalidation_reason}")

    def owns_completed_report(self, session, report):
        self.validate_session(session)
        record = self.record_for(session)
        return bool(record is not None and record.state == "terminal"
                    and record.report is report and record.identity == session.identity
                    and record.head == report.head and record.scope == report.subjects
                    and record.owner == report.owner and record.dispatch == report.task)


def _review_instructions(candidate, subjects, assigned_reads, *, historical=False):
    encoding = ("" if historical else
                " and encoding. Base64 materializations must be decoded before review")
    return (
        "Review only the bound candidate and supplied evidence. Do not edit files, "
        "run commands, push, comment, request review, dispatch CI, merge, or perform "
        "other mutations. Before reading, state exactly: "
        f"{CliReviewRuntime.COMMITMENT} Candidate SHA: {candidate}. "
        f"Review scope: {', '.join(sorted(subjects))}. "
        f"Assigned candidate reads (JSON): {assigned_reads}. "
        "Each entry maps a candidate path and side to its exact supplied file path"
        f"{encoding}. Use complete direct file reads of every mapped file; for a file too large for "
        "the default view limit, request a full read with forceReadLargeFiles and do not "
        "use ranges. Return only JSON with keys "
        "candidate_sha, scope, findings. Bind candidate_sha to the assigned SHA, "
        "scope to the exact assigned scope array, and findings to an array of objects "
        "with keys id, subject, family, member, source_path, detail. Use an empty "
        "findings array when no findings are present."
    )


class CliReviewRuntime:
    """Read-only adapter over actual CLI event records for one completed task."""

    COMMITMENT = "I will follow the read-only restrictions and only inspect the assigned files."

    def __init__(self, events, *, dispatch_id, root_session_id, candidate_reader,
                 required_reads):
        self.events = tuple(events)
        self.dispatch_id = identifier(dispatch_id, NAME_PATTERN, "task dispatch identity")
        self.root_session_id = identifier(root_session_id, NAME_PATTERN, "coordinator session")
        self.candidate_reader = candidate_reader
        require(isinstance(required_reads, dict) and bool(required_reads),
                "exact candidate read requirements are missing")
        supplied_reads = dict(required_reads)
        self.required_reads = {}
        base_entries = candidate_reader.base_tree.entries
        head_entries = candidate_reader.head_tree.entries
        changed_paths = {
            path for path in set(base_entries) | set(head_entries)
            if base_entries.get(path) != head_entries.get(path)
        }
        required_keys = set()
        for path in changed_paths:
            base_entry = base_entries.get(path)
            head_entry = head_entries.get(path)
            change = CandidatePathChange(
                path,
                base_entry is not None,
                None if base_entry is None else base_entry[0],
                None if base_entry is None else base_entry[1],
                None if base_entry is None else base_entry[2],
                head_entry is not None,
                None if head_entry is None else head_entry[0],
                None if head_entry is None else head_entry[1],
                None if head_entry is None else head_entry[2],
            )
            required_keys.update((path, side) for side in change.required_reads())
        require(required_keys <= set(supplied_reads),
                "review reads do not cover the complete candidate diff")
        for key, target in supplied_reads.items():
            require(isinstance(key, tuple) and len(key) == 2,
                    "invalid candidate read identity")
            path = repo_path(key[0], "candidate read path")
            side = review_side(key[1])
            entries = base_entries if side == "base" else head_entries
            entry = entries.get(path)
            require(entry is not None and entry[0] in REVIEW_BLOB_MODES and entry[1] == "blob",
                    "review read is not an available regular Git blob")
            keys(target, ("tool_path", "content", "summary", "encoding"),
                 "candidate read target")
            require(isinstance(target["tool_path"], str) and target["tool_path"]
                    and isinstance(target["content"], bytes)
                    and isinstance(target["encoding"], str)
                    and target["encoding"] in {"utf-8", "base64"},
                    "invalid candidate read materialization")
            try:
                target["content"].decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                encoding = "base64"
            require(target["encoding"] == encoding,
                    "candidate read encoding does not match its Git blob")
            summary = validate_candidate_read_summary(
                target["summary"], base_sha=candidate_reader.base,
                head_sha=candidate_reader.head)
            require((summary.path, summary.side) == (path, side),
                    "candidate read target is bound to another path or side")
            described = validate_candidate_read_summary(
                candidate_reader.describe(path, side), base_sha=candidate_reader.base,
                head_sha=candidate_reader.head)
            observed = validate_candidate_read(
                candidate_reader(path, side), base_sha=candidate_reader.base,
                head_sha=candidate_reader.head)
            require(summary == described == observed.summary()
                    and target["content"] == observed.data,
                    "candidate read materialization differs from the immutable Git object")
            self.required_reads[(path, side)] = {
                "tool_path": target["tool_path"], "content": target["content"],
                "summary": summary, "encoding": encoding,
            }
        unique([target["tool_path"] for target in self.required_reads.values()],
               "candidate materialization paths")
        self.reviewer = None
        self.interaction = None
        self.prompt = None
        self.dispatch_started_at = None
        self.started_at = None
        self.completed_at = None
        self.requested_duration = None
        self.assigned_candidate = None
        self.assigned_subjects = None
        self.reviewer_session_id = None
        self.received_prompt = None
        self.received_at = None
        self.result = None
        self.validated_event_keys = frozenset()
        self.validated_event_sessions = frozenset()
        self.assignment_event_keys = None

    def start(self, **expected):
        self.assigned_candidate = expected.get("candidate")
        self.assigned_subjects = tuple(sorted(expected.get("subjects", ())))
        self.requested_duration = expected.get("duration")
        self.assigned_owner = expected.get("owner")
        dispatches = [(session, event) for session, event in self.events
                      if session == self.root_session_id
                      and event.get("type") == "tool.execution_start"
                      and event.get("data", {}).get("toolCallId") == self.dispatch_id]
        require(len(dispatches) == 1, "review task dispatch event is missing or duplicated")
        dispatch = dispatches[0][1]
        data = dispatch["data"]
        self.dispatch_started_at = dispatch["timestamp"]
        require(data.get("toolName") == "task", "review dispatch is not a task operation")
        arguments = data.get("arguments")
        require(isinstance(arguments, dict), "review task arguments are missing")
        prompt = arguments.get("prompt")
        require(isinstance(prompt, str) and prompt.strip(), "review task prompt is missing")
        self.prompt = prompt
        starts = [(session, event) for session, event in self.events
                  if session == self.root_session_id
                  and event.get("type") == "subagent.started"
                  and event.get("data", {}).get("toolCallId") == self.dispatch_id]
        require(len(starts) == 1, "review task identity is missing or duplicated")
        started = starts[0][1]
        self.started_at = started["timestamp"]
        require(timestamp(dispatch["timestamp"]) <= timestamp(started["timestamp"]),
                "reviewer started before the assigned task dispatch")
        self.reviewer = identifier(started.get("agentId"), NAME_PATTERN, "reviewer identity")
        self.interaction = identifier(started["data"].get("interactionId"),
                                      NAME_PATTERN, "review interaction")
        require(started["data"].get("agentType") == "code-review",
                "review task role is unsupported")
        completions = [(session, event) for session, event in self.events
                       if session == self.root_session_id
                       and event.get("type") == "subagent.completed"
                       and event.get("agentId") == self.reviewer
                       and event.get("data", {}).get("toolCallId") == self.dispatch_id]
        require(len(completions) <= 1, "review task completion is duplicated")
        if completions:
            self.completed_at = timestamp(completions[0][1].get("timestamp"))
            require(timestamp(self.started_at) <= self.completed_at,
                    "review task completion predates its assigned start")
        received = [(session, event) for session, event in self.events
                    if event.get("agentId") == self.reviewer
                    and event.get("type") == "user.message"
                    and event.get("data", {}).get("source") == "agent-" + self.root_session_id
                    and event.get("data", {}).get("interactionId") == self.interaction
                    and isinstance(event.get("data", {}).get("transformedContent"), str)
                    and prompt in event["data"]["transformedContent"]
                    and timestamp(self.started_at) <= timestamp(event.get("timestamp"))
                    and (self.completed_at is None
                         or timestamp(event.get("timestamp")) <= self.completed_at)]
        require(len(received) == 1, "original reviewer instructions are missing or repeated")
        self.reviewer_session_id = identifier(
            received[0][0], NAME_PATTERN, "reviewer session")
        require(self.reviewer_session_id == self.root_session_id,
                "reviewer receipt must be in the root session; child session provenance is unsupported")
        received_prompt = received[0][1]["data"].get("transformedContent")
        require(isinstance(received_prompt, str) and prompt in received_prompt,
                "received review instructions differ from the dispatched prompt")
        self.received_prompt = received_prompt
        self.received_at = timestamp(received[0][1].get("timestamp"))
        require(timestamp(started["timestamp"]) <= timestamp(received[0][1]["timestamp"]),
                "reviewer instructions predate the assigned task")
        self._validate_prompt(prompt)
        require(expected.get("owner") == self.reviewer,
                "observed reviewer identity differs from the coordinator assignment")
        self.assignment_event_keys = frozenset(
            _cli_event_key(record) for record in (dispatches[0], starts[0], received[0]))
        return self.dispatch_id

    def _validate_prompt(self, prompt):
        marker = "Assigned candidate reads (JSON): "
        require(prompt.count(marker) == 1, "assigned candidate reads are missing or ambiguous")
        tail = prompt.split(marker, 1)[1]
        try:
            entries, end = json.JSONDecoder().raw_decode(tail)
        except (ValueError, TypeError) as error:
            raise ReviewError("assigned candidate reads are malformed") from error
        raw = tail[:end]
        require(isinstance(entries, list) and bool(entries)
                and all(isinstance(item, list) and len(item) in (3, 4)
                        and all(isinstance(value, str) for value in item)
                        for item in entries),
                "assigned candidate reads have an unsupported format")
        widths = {len(item) for item in entries}
        require(len(widths) == 1, "assigned candidate reads mix instruction formats")
        historical = widths == {3}
        require(prompt == _review_instructions(
            self.assigned_candidate, self.assigned_subjects, raw, historical=historical),
            "dispatched prompt omits or contradicts accepted reviewer instructions")
        assigned = {}
        for item in entries:
            path, side, tool_path = item[:3]
            key = repo_path(path, "candidate read path"), review_side(side)
            require(key not in assigned, "duplicate assigned candidate read")
            assigned[key] = (tool_path, "utf-8" if historical else item[3])
        require(assigned == {
            key: (target["tool_path"], target["encoding"])
            for key, target in self.required_reads.items()
        }, "dispatched candidate reads differ from the exact assignment")

    def _review_activity_records(self):
        return [(session, event) for session, event in self.events
                if session == self.root_session_id and event.get("agentId") == self.reviewer]

    def read_terminal(self, task):
        require(task == self.dispatch_id, "review task identity differs from its dispatch")
        dispatch = [event for session, event in self.events
                    if session == self.root_session_id
                    and event.get("type") == "tool.execution_start"
                    and event.get("data", {}).get("toolCallId") == task]
        starts = [event for session, event in self.events
                  if session == self.root_session_id
                  and event.get("type") == "subagent.started"
                  and event.get("data", {}).get("toolCallId") == task]
        require(len(dispatch) == len(starts) == 1
                and dispatch[0].get("data", {}).get("toolName") == "task"
                and starts[0].get("data", {}).get("agentType") == "code-review"
                and starts[0].get("agentId") == self.assigned_owner,
                "review dispatch and reviewer start are missing or contradictory")
        reviewer = identifier(starts[0].get("agentId"), NAME_PATTERN, "reviewer identity")
        interaction = identifier(starts[0]["data"].get("interactionId"),
                                 NAME_PATTERN, "review interaction")
        started_at = starts[0].get("timestamp")
        completion = [event for session, event in self.events
                      if session == self.root_session_id
                      and event.get("agentId") == reviewer
                      and event.get("type") == "subagent.completed"
                      and event.get("data", {}).get("toolCallId") == self.dispatch_id]
        require(len(completion) == 1, "reviewer completion event is missing")
        completed_at = completion[0].get("timestamp")
        require(timestamp(dispatch[0].get("timestamp")) <= timestamp(started_at)
                <= timestamp(completed_at), "review task chronology is invalid")
        return SimpleNamespace(
            task=self.dispatch_id, owner=reviewer, role="code-review",
            head=self.assigned_candidate, subjects=self.assigned_subjects,
            completed=True, started_at=started_at, completed_at=completed_at,
            interaction_id=interaction, cli_events=self.events,
        )

    def _validate_dispatch_receipt(self):
        root_completions = [event for session, event in self.events
                            if session == self.root_session_id
                            and event.get("type") == "tool.execution_complete"
                            and event.get("data", {}).get("toolCallId") == self.dispatch_id]
        require(len(root_completions) == 1
                and root_completions[0]["data"].get("success") is True
                and timestamp(self.dispatch_started_at)
                <= timestamp(root_completions[0]["timestamp"]),
                "task dispatch completion is missing or failed")
        return self.root_session_id, root_completions[0]

    def read(self, task):
        terminal = self.read_terminal(task)
        require(self.assignment_event_keys is not None,
                "review assignment was not validated")
        assignment_keys = frozenset(
            _cli_event_key((session, event)) for session, event in self.events
            if session == self.root_session_id and (
                (event.get("type") in {"tool.execution_start", "subagent.started"}
                 and event.get("data", {}).get("toolCallId") == self.dispatch_id)
                or (event.get("type") == "user.message" and event.get("agentId") == self.reviewer
                    and event.get("data", {}).get("interactionId") == self.interaction)))
        require(self.assignment_event_keys <= assignment_keys,
                "review assignment records changed after validation")
        dispatch_receipt = self._validate_dispatch_receipt()
        completed_at = terminal.completed_at
        child = self._review_activity_records()
        self.validated_event_keys = validate_reviewer_activity(
            child, owner=self.reviewer, dispatch=self.dispatch_id,
            interaction=self.interaction, started_at=self.started_at,
            completed_at=completed_at) | self.assignment_event_keys | {_cli_event_key(dispatch_receipt)}
        self.validated_event_sessions = frozenset(
            session for session, event in child if is_review_activity(event))
        turn_ids = {
            event.get("data", {}).get("turnId")
            for _, event in child
            if event.get("data", {}).get("interactionId") == self.interaction
            and isinstance(event.get("data", {}).get("turnId"), str)
        }
        turn_event_records = [
            (session, event) for session, event in child
            if (event.get("data", {}).get("interactionId") == self.interaction
                or (event.get("type") == "tool.execution_start"
                    and event.get("data", {}).get("interactionId") is None
                    and event.get("data", {}).get("turnId") in turn_ids))
        ]
        turn_events = [event for _, event in turn_event_records]
        received = [event for event in turn_events if event.get("type") == "user.message"]
        require(len(received) == 1
                and received[0].get("data", {}).get("transformedContent")
                == self.received_prompt,
                "original reviewer instructions differ from the bound receipt")
        messages = [event for event in turn_events if event.get("type") == "assistant.message"]
        require(all(timestamp(received[0]["timestamp"]) <= timestamp(event["timestamp"])
                    for event in messages),
                "reviewer message predates the assigned instructions")
        commitments = [event for event in messages
                       if event.get("data", {}).get("phase") == "commentary"
                       and event.get("data", {}).get("content", "").strip()
                       == self.COMMITMENT]
        tool_starts = [event for event in turn_events if event.get("type") == "tool.execution_start"]
        skill_starts = [event for event in tool_starts
                        if event.get("data", {}).get("toolName") == "skill"]
        require(len(skill_starts) <= 1, "reviewer skill load is duplicated")
        skill_id = None
        if skill_starts:
            skill = skill_starts[0]
            skill_id = identifier(skill["data"].get("toolCallId"), NAME_PATTERN,
                                  "review skill identity")
            require(skill["data"].get("arguments") == {"skill": "code-review"},
                    "reviewer skill load is unsupported")
            skill_results = [event for event in turn_events
                             if event.get("type") == "tool.execution_complete"
                             and event.get("data", {}).get("toolCallId") == skill_id]
            require(len(skill_results) == 1
                    and skill_results[0]["data"].get("success") is True
                    and timestamp(received[0]["timestamp"]) <= timestamp(skill["timestamp"])
                    <= timestamp(skill_results[0]["timestamp"])
                    and len(commitments) == 1
                    and timestamp(skill_results[0]["timestamp"])
                    < timestamp(commitments[0]["timestamp"]),
                    "reviewer skill load is incomplete or outside its allowed interval")
        tool_starts = [event for event in tool_starts if event not in skill_starts]
        require(len(commitments) == 1
                and timestamp(commitments[0]["timestamp"])
                < min((timestamp(event["timestamp"]) for event in tool_starts),
                      default=timestamp(completed_at)),
                "explicit read-only commitment must precede reviewer reads")
        starts = {}
        reads = {}
        seen_reads = set()
        for event in tool_starts:
            data = event["data"]
            require(data.get("toolName") == "view", "reviewer used a prohibited or unverified operation")
            call_id = identifier(data.get("toolCallId"), NAME_PATTERN, "review tool identity")
            require(call_id not in starts and call_id != skill_id, "duplicate reviewer tool call")
            args = data.get("arguments")
            require(isinstance(args, dict) and "view_range" not in args,
                    "partial or malformed candidate view")
            path = args.get("path")
            match = next((key for key, target in self.required_reads.items()
                          if target["tool_path"] == path), None)
            require(match is not None and match not in seen_reads,
                    "reviewer read an unassigned or duplicate candidate path")
            seen_reads.add(match)
            starts[call_id] = match, event
        results = {}
        for event in turn_events:
            if event.get("type") != "tool.execution_complete":
                continue
            data = event["data"]
            call_id = data.get("toolCallId")
            if skill_id is not None and call_id == skill_id:
                continue
            require(call_id in starts, "reviewer tool completion has no matching operation")
            if call_id in starts:
                require(call_id not in results and data.get("success") is True,
                        "reviewer file read failed or has contradictory results")
                result = data.get("result")
                require(isinstance(result, dict) and isinstance(result.get("content"), str),
                        "reviewer file result is incomplete")
                key, start = starts[call_id]
                expected = self.required_reads[key]
                if expected["encoding"] == "utf-8":
                    content = result["content"].encode("utf-8")
                else:
                    try:
                        content = base64.b64decode(result["content"], validate=True)
                    except (ValueError, binascii.Error) as error:
                        raise ReviewError("reviewer binary read is not valid base64") from error
                    require(base64.b64encode(content).decode("ascii") == result["content"],
                            "reviewer binary read is not canonical base64")
                require(content == expected["content"],
                        "reviewer observed bytes differ from the exact candidate Git blob")
                require(timestamp(start["timestamp"]) <= timestamp(event["timestamp"]),
                        "reviewer file read completed before it started")
                reads[key] = expected["summary"]
                results[call_id] = event
        require(set(results) == set(starts), "reviewer file-read completion is missing")
        require(set(reads) == set(self.required_reads),
                "reviewer did not observe every required candidate path and side")
        finals = [event for event in messages if event.get("data", {}).get("phase") == "final_answer"]
        require(len(finals) == 1 and timestamp(finals[0]["timestamp"]) < timestamp(completed_at),
                "reviewer final report is missing or outside the completed turn")
        final_time = timestamp(finals[0]["timestamp"])
        require(all(timestamp(start["timestamp"]) < final_time for _, start in starts.values())
                and all(timestamp(event["timestamp"]) < final_time for event in results.values()),
                "reviewer reported before completing all candidate reads")
        report_text = finals[0]["data"].get("content")
        require(isinstance(report_text, str) and report_text.strip(),
                "reviewer report content is missing")
        report_data = parse_json(report_text.encode("utf-8"))
        keys(report_data, ("candidate_sha", "scope", "findings"), "reviewer report")
        require(report_data["candidate_sha"] == self.candidate_reader.head
                and report_data["scope"] == list(sorted(self._scope())),
                "reviewer report is not bound to the assigned candidate and scope")
        report_findings = report_data["findings"]
        require(isinstance(report_findings, list) and len(report_findings) <= MAX_FINDINGS,
                "review finding bound exceeded")
        findings = []
        for item in report_findings:
            keys(item, ("id", "subject", "family", "member", "source_path", "detail"),
                 "review finding")
            identifier(item["id"], NAME_PATTERN, "finding ID")
            identifier(item["subject"], SCOPE_PATTERN, "finding subject")
            identifier(item["family"], NAME_PATTERN, "finding family")
            identifier(item["member"], NAME_PATTERN, "finding member")
            require(item["subject"] in self._scope(), "finding is outside assigned review scope")
            require(item["family"] in FAMILIES and ":" in item["member"]
                    and item["member"].split(":", 1)[0] in FAMILIES[item["family"]],
                    "finding is outside the accepted sibling family")
            path = repo_path(item["source_path"], "finding source path")
            require(isinstance(item["detail"], str) and item["detail"].strip()
                    and len(item["detail"]) <= MAX_DETAIL, "invalid finding detail")
            require(any(
                summary.path == path
                and (summary.side == "head"
                     or (summary.side == "base"
                         and path not in self.candidate_reader.head_tree.entries))
                for summary in reads.values()),
                    "finding source was not observed by the reviewer")
            findings.append(Finding(item["id"], item["subject"], item["family"], item["member"],
                                    self.candidate_reader.head, path,
                                    "local:" + self.dispatch_id, item["detail"]))
        unique([item.id for item in findings], "review findings")
        duration = (timestamp(completed_at) - timestamp(terminal.started_at)).total_seconds()
        require(0 <= duration <= MAX_REVIEW_SECONDS
                and type(self.requested_duration) is int
                and duration <= self.requested_duration,
                "review task exceeded its duration bound")
        self.result = SimpleNamespace(
            task=self.dispatch_id, owner=self.reviewer, role="code-review",
            head=self.candidate_reader.head, subjects=tuple(sorted(self._scope())),
            completed=True, actions=(("view", "skill") if skill_id else ("view",)),
            files=len(reads), findings=tuple(findings),
            started_at=terminal.started_at, completed_at=completed_at,
            interaction_id=self.interaction, instruction_commitment=self.COMMITMENT,
            logical_closed=False,
            candidate_reads=tuple(reads[key] for key in sorted(reads)),
            report=report_text, cli_events=self.events,
        )
        return self.result

    def _scope(self):
        require(isinstance(self.received_prompt, str), "bound reviewer instructions are missing")
        return self.assigned_subjects


class ReviewSession:
    """Adapter around the coordinator's existing task/tool calls, not a backend.

    runtime.start/read/stop are coordinator adapters over the actual CLI task
    observations.
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
        for item in scope:
            identifier(item, SCOPE_PATTERN, "review subject")
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
        self.owner = None
        self.readers = dict(readers or {})
        require(set(self.readers) <= READ_ACTIONS, "unapproved reviewer tool binding")
        self.lease = None
        self.report = None
        self.local_findings: dict[str, Finding] = {}
        self.local_triage: dict[str, tuple[bool, str]] = {}
        self.rounds = RoundState()
        self.accepted: dict[str, Finding] = {}
        self.attempted_candidate_paths: set[str] = set()
        self.candidate_reads: dict[tuple[str, str], CandidateReadSummary] = {}
        self.candidate_binding: CandidateBinding | None = None
        self.task: str | None = None
        self.dispatch_namespace: str | None = None
        self.assignment_event_keys = None
        self.latest_cli_events = ()
        self.event_sources = []
        self._ownership_key = object()
        self.begin_failed = False

    def _candidate_reader(self):
        reader = self.readers.get("read-candidate")
        if reader is None or getattr(reader, "review_candidate_reader", False) is not True:
            return None
        preview = getattr(reader, "preview", None)
        describe = getattr(reader, "describe", None)
        require(callable(preview), "trusted candidate reader preview is unavailable")
        require(callable(describe), "trusted candidate reader description is unavailable")
        source = field_value(reader, "candidate_binding")
        binding = (validate_candidate_binding(source, "trusted candidate reader binding")
                   if source is not _MISSING else CandidateBinding(
                       resolve_existing_repo_root(getattr(reader, "root", None),
                                                 "trusted candidate reader root"),
                       sha(getattr(reader, "base", None)),
                       sha(getattr(reader, "head", None)),
                   ))
        if self.candidate_binding is not None:
            require(binding == self.candidate_binding,
                    "trusted candidate reader root/base/head changed after review start")
        tree_binding = candidate_tree_binding(reader)
        if self.candidate_binding is not None:
            require(tree_binding == self.candidate_binding,
                    "trusted candidate reader immutable Git tree binding changed after review start")
        require(binding == tree_binding,
                "trusted candidate reader binding differs from immutable Git tree binding")
        return reader, preview, describe, binding

    def begin(self, runtime, owner: str, *, duration=1200, max_files=MAX_REVIEW_FILES):
        require(self.lease is None, "duplicate or overlapping reviewer ownership")
        require(isinstance(owner, str) and bool(owner.strip())
                and owner not in {self.coordinator, self.implementer},
                "reviewer ownership overlaps")
        self.owner = owner
        self.dispatch_namespace = getattr(runtime, "root_session_id", None)
        if self.dispatch_namespace is not None:
            identifier(self.dispatch_namespace, NAME_PATTERN, "coordinator session")
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
        reserved = False
        dispatched = False
        try:
            if self.owners is not None:
                self.owners.reserve(self)
                reserved = True
            if isinstance(runtime, CliReviewRuntime):
                dispatched = True
                runtime.assigned_owner = owner
                runtime.assigned_candidate = self.head
                runtime.assigned_subjects = tuple(sorted(self.scope))
                if self.owners is not None:
                    self.owners.bind_dispatch(self, runtime.dispatch_id)
            assigned_reads = json.dumps(
                [[path, side, target["tool_path"], target["encoding"]]
                 for (path, side), target in sorted(runtime.required_reads.items())]
                if isinstance(runtime, CliReviewRuntime) else [],
                ensure_ascii=True, separators=(",", ":"))
            if isinstance(runtime, CliReviewRuntime):
                require(runtime.required_reads, "review assignment has no candidate paths")
                require(candidate_reader is not None
                        and candidate_tree_binding(runtime.candidate_reader) == binding,
                        "review runtime candidate binding differs from the assigned reader")
            instructions = _review_instructions(self.head, self.scope, assigned_reads)
            dispatched = True
            task = runtime.start(
                role="code-review", owner=owner, candidate=self.head,
                subjects=tuple(sorted(self.scope)),
                instructions=instructions,
                duration=duration, max_files=max_files, max_findings=MAX_FINDINGS,
            )
            require(isinstance(task, str) and bool(task.strip()),
                    "runtime task identity must be a nonblank string")
            if isinstance(runtime, CliReviewRuntime):
                self.assignment_event_keys = runtime.assignment_event_keys
            if self.owners is not None:
                self.owners.bind_dispatch(self, task)
        except Exception:
            if reserved and not dispatched:
                self.owners.release_before_dispatch(self)
            if dispatched:
                self.begin_failed = True
                if isinstance(runtime, CliReviewRuntime):
                    started = self.clock()
                    self.task = runtime.dispatch_id
                    self.lease = ReviewLease(runtime.dispatch_id, owner, self.head, self.scope,
                                             started, started + duration, max_files)
            else:
                self.dispatch_namespace = None
                self.owner = None
            raise
        started = self.clock()
        self.lease = ReviewLease(task, owner, self.head, self.scope,
                                 started, started + duration, max_files)
        self.task = task
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
        require(request.path in self.attempted_candidate_paths
                or len(self.attempted_candidate_paths) < self.lease.max_files,
                "review file budget exceeded")
        self.attempted_candidate_paths.add(request.path)
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

    def _read_task(self, runtime, *, terminal=False):
        lease = self.lease
        require(lease is not None and not lease.finished, "no active review task")
        require(terminal or not self.begin_failed,
                "failed review start may only be retired without admission")
        require(getattr(runtime, "root_session_id", None) == self.dispatch_namespace,
                "review runtime root session differs from the assigned task")
        if self.assignment_event_keys is not None and not terminal:
            require(isinstance(runtime, CliReviewRuntime),
                    "review runtime no longer supplies the assigned CLI observations")
        if isinstance(runtime, CliReviewRuntime) and self.owners is not None:
            self.owners.validate_closed_events(
                runtime.events, snapshot_namespace=runtime.root_session_id, snapshot_owner=self)
        if isinstance(runtime, CliReviewRuntime) and not terminal:
            require(self.candidate_binding is not None
                    and candidate_tree_binding(runtime.candidate_reader) == self.candidate_binding,
                    "review runtime candidate binding differs from the assigned reader")
        reader = getattr(runtime, "read_terminal", None) if terminal else None
        result = reader(lease.task) if callable(reader) else runtime.read(lease.task)
        if isinstance(runtime, CliReviewRuntime) and not terminal:
            require(self.assignment_event_keys is not None
                    and runtime.assignment_event_keys == self.assignment_event_keys,
                    "review assignment differs from the original validated records")
        if self.owners is not None and hasattr(result, "cli_events"):
            self.owners.validate_closed_events(
                result.cli_events, snapshot_namespace=self.dispatch_namespace, snapshot_owner=self)
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

    def _retire(self, result, outcome, *, admitted_event_keys=(),
                admitted_event_sessions=(), report=None):
        require(result.completed is True, "review turn is incomplete")
        require(timestamp(getattr(result, "started_at", None))
                <= timestamp(getattr(result, "completed_at", None)),
                "review task chronology is invalid")
        if outcome == "completed" and self.clock() > self.lease.deadline:
            outcome = "timed-out"
        if self.owners is not None:
            self.owners.finish(
                self, self.lease.task, self.lease.owner,
                    getattr(result, "interaction_id", ""), result.started_at, result.completed_at,
                    self.dispatch_namespace, admitted_event_keys, admitted_event_sessions,
                    getattr(result, "cli_events", ()),
                    report if outcome == "completed" else None)
        self.lease.finished = True
        self.lease.outcome = outcome
        return outcome

    def abort(self, runtime):
        result = self._read_task(runtime, terminal=True)
        if not result.completed:
            stop = getattr(runtime, "stop", None)
            require(callable(stop), "runtime stop capability is unavailable")
            stop(self.lease.task)
            result = self._read_task(runtime, terminal=True)
        return self._retire(result, "aborted")

    def finish(self, runtime) -> _ReviewReport:
        result = self._read_task(runtime)
        lease = self.lease
        require(result.completed is True, "review turn is incomplete")
        require(hasattr(result, "started_at") and hasattr(result, "completed_at"),
                "review execution chronology is missing")
        if self.clock() > lease.deadline:
            self._retire(result, "timed-out")
            raise ReviewError("review exceeded duration bound")
        recorded_duration = (timestamp(result.completed_at)
                             - timestamp(result.started_at)).total_seconds()
        if recorded_duration < 0 or recorded_duration > lease.deadline - lease.started:
            self._retire(result, "timed-out")
            raise ReviewError("review exceeded duration bound")
        require(all(hasattr(result, field) for field in (
            "actions", "files", "findings", "instruction_commitment",
            "logical_closed", "candidate_reads", "report")),
            "incomplete runtime review record")
        require(isinstance(result.instruction_commitment, str)
                and result.instruction_commitment == CliReviewRuntime.COMMITMENT,
                "reviewer instruction commitment is missing")
        require(type(result.logical_closed) is bool and result.logical_closed is False,
                "runtime cannot assert coordinator logical closure")
        require(isinstance(result.actions, (tuple, list, set, frozenset))
                and all(isinstance(action, str)
                        and action in ({"view", "skill"} if isinstance(runtime, CliReviewRuntime)
                                       else {"view"})
                        for action in result.actions)
                and "view" in result.actions
                and type(result.files) is int and 0 < result.files <= lease.max_files <= MAX_REVIEW_FILES
                and isinstance(result.findings, (tuple, list))
                and len(result.findings) <= MAX_FINDINGS, "review action/budget violation")
        findings = tuple(result.findings)
        for finding in findings:
            require(isinstance(finding, Finding)
                    and all(isinstance(value, str) and bool(value.strip()) for value in (
                        finding.id, finding.subject, finding.family, finding.member,
                        finding.origin, finding.source_path, finding.review_id))
                    and isinstance(finding.detail, str)
                    and len(finding.detail) <= MAX_DETAIL
                    and finding.subject in self.scope
                    and finding.family in FAMILIES and finding.origin == lease.head
                    and finding.review_id == "local:" + str(lease.task),
                    "local finding has wrong task, head or scope")
        unique([finding.id for finding in findings], "local report findings")
        candidate_reader = self._candidate_reader()
        binding = self.candidate_binding if candidate_reader is not None else None
        observed_reads = getattr(result, "candidate_reads", ())
        require(isinstance(observed_reads, (tuple, list)),
                "candidate read observations must be a collection")
        external_reads = {}
        if binding is not None:
            _, _, describe, _ = candidate_reader
            for value in observed_reads:
                summary = validate_candidate_read_summary(
                    value, base_sha=binding.base, head_sha=binding.head)
                expected = validate_candidate_read_summary(
                    describe(summary.path, summary.side),
                    base_sha=binding.base, head_sha=binding.head)
                require(summary == expected,
                        "reviewer candidate read differs from the immutable Git object")
                key = (summary.path, summary.side)
                require(key not in external_reads, "duplicate reviewer candidate read")
                external_reads[key] = summary
        else:
            require(not observed_reads, "candidate reads require a trusted Git-tree binding")
        for key, summary in self.candidate_reads.items():
            require(key not in external_reads or external_reads[key] == summary,
                    "candidate read observations conflict")
            external_reads[key] = summary
        if hasattr(result, "cli_events"):
            require(isinstance(result.cli_events, (tuple, list)),
                    "native CLI event observations must be a collection")
            require(result.files == len(observed_reads),
                    "review file count differs from native read observations")
            require(isinstance(result.interaction_id, str) and result.interaction_id,
                    "review interaction identity is missing")
            require(all(item.detail.strip() for item in findings),
                    "native review findings require observed detail")
        require(isinstance(result.report, str) and result.report.strip(),
                "review report content is missing")
        report_data = parse_json(result.report.encode("utf-8"))
        keys(report_data, ("candidate_sha", "scope", "findings"), "reviewer report")
        require(report_data["candidate_sha"] == lease.head
                and report_data["scope"] == list(sorted(lease.scope)),
                "reviewer report is not bound to the assigned candidate and scope")
        expected_findings = [{
            "id": item.id, "subject": item.subject, "family": item.family,
            "member": item.member, "source_path": item.source_path, "detail": item.detail,
        } for item in findings]
        require(report_data["findings"] == expected_findings,
                "parsed findings differ from the original reviewer report")
        report = _ReviewReport(
            str(lease.task), lease.owner, "code-review", lease.head, lease.scope, True,
            frozenset(result.actions), result.files,
            tuple(Finding(item.id, item.subject, item.family, item.member,
                          item.origin, item.source_path, item.review_id, item.detail)
                  for item in findings),
            getattr(result, "started_at", None), getattr(result, "completed_at", None),
            result.instruction_commitment, True, result.report,
            None if binding is None else binding.resolved_root,
            None if binding is None else binding.base,
            tuple(external_reads[key] for key in sorted(external_reads)))
        cli_events = tuple(getattr(result, "cli_events", ()))
        if isinstance(runtime, CliReviewRuntime):
            admitted_event_keys = runtime.validated_event_keys
            admitted_event_sessions = runtime.validated_event_sessions
        elif hasattr(result, "cli_events"):
            admitted_event_keys = validate_reviewer_activity(
                cli_events, owner=lease.owner, dispatch=lease.task,
                interaction=result.interaction_id, started_at=result.started_at,
                completed_at=result.completed_at)
            admitted_event_sessions = frozenset(
                session for session, event in cli_events
                if isinstance(session, str) and isinstance(event, dict)
                and event.get("agentId") == lease.owner and is_review_activity(event))
        else:
            admitted_event_keys = frozenset()
            admitted_event_sessions = frozenset()
        require(self._retire(
            result, "completed", admitted_event_keys=admitted_event_keys,
            admitted_event_sessions=admitted_event_sessions, report=report) == "completed",
                "review exceeded duration bound")
        self.latest_cli_events = cli_events
        if self.owners is not None:
            self.owners.validate_closed_events(
                self.latest_cli_events, snapshot_namespace=self.dispatch_namespace,
                snapshot_owner=self)
            self.owners.validate_session(self)
        self.local_findings = {finding.id: finding for finding in report.findings}
        self.report = report
        return report

    def observe_cli_events(self, events, *, complete=False) -> None:
        require(isinstance(events, (tuple, list)), "CLI event observations must be a collection")
        require(type(complete) is bool, "CLI snapshot completeness must be explicit")
        if complete:
            self.latest_cli_events = tuple(events)
        if self.owners is not None:
            record = self.owners.record_for(self)
            if record is not None and record.state == "terminal":
                self.event_sources.append(events)
            self.owners.validate_closed_events(
                events, snapshot_namespace=self.dispatch_namespace if complete else None,
                snapshot_owner=self if complete else None)
            self.owners.validate_session(self)

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
        require(not any(item.fact.id == finding.review_id and item.outcome == "failed"
                        for item in self.rounds.events),
                "failed review has accepted findings")
        require(finding.subject in self.scope and finding.family in FAMILIES,
                "finding is outside accepted scope")
        sha(finding.origin)
        self.accepted[finding.id] = finding

    def triage(self, decision: Triage) -> None:
        if decision.outcome == "failed":
            require(not any(item.review_id == decision.fact.id for item in self.accepted.values()),
                    "failed review has accepted findings")
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

    def review_state(self, facts, triage, *, pre_review_required, cli_events=(),
                     cli_events_complete=False):
        require(type(cli_events_complete) is bool, "CLI snapshot completeness must be explicit")
        if cli_events or cli_events_complete:
            self.observe_cli_events(cli_events, complete=cli_events_complete)
        if pre_review_required and self.owners is not None:
            self.owners.validate_session(self)
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
            require(self.report.logical_closed is True
                    and bool(self.report.instruction_commitment.strip()),
                    "review turn lacks instruction commitment or logical closure")
            require(self.owners is not None, "coordinator review ownership index required")
            self.owners.validate_closed_events(
                getattr(self, "latest_cli_events", ()),
                snapshot_namespace=self.dispatch_namespace, snapshot_owner=self)
            require(self.owners.owns_completed_report(self, self.report),
                    "review report differs from the coordinator ownership record")
            if facts:
                require(timestamp(self.report.completed_at) < min(
                    timestamp(item.submitted_at) for item in facts),
                    "fresh pre-review did not precede the first remote review")
        ready = all(item.outcome != "untriaged" and not item.fact.unresolved_threads for item in triage)
        last_failed = max((timestamp(item.fact.submitted_at) for item in triage
                           if item.outcome == "failed"), default=None)
        if last_failed is not None:
            ready = ready and any(item.outcome == "clean" and item.fact.head == self.head
                                  and timestamp(item.fact.submitted_at) > last_failed
                                  for item in triage)
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
