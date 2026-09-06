"""Bounded review convergence. Tool provenance belongs to the coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
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
MAX_REVIEW_SECONDS = 3600


class ReviewError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewError(message)


def keys(value: Any, expected: tuple[str, ...], label: str) -> dict:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == set(expected), f"{label} has missing or unknown fields")
    return value


def identifier(value: Any, pattern: str, label: str) -> str:
    require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
            f"invalid {label}")
    return value


def sha(value: Any) -> str:
    return identifier(value, SHA_PATTERN, "full lowercase Git SHA")


def integer(value):
    return type(value) is int or (type(value) is float and value.is_integer())


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

    require(len(raw) <= 1024 * 1024, "review request exceeds size bound")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as error:
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
        require(finding["family"] in FAMILIES, "unknown family")
    unique([item["finding_id"] for item in findings], "findings")
    return {**value, "schema_version": 1, "pull_request": int(value["pull_request"])}


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

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.subject, self.family, self.member


def validate_members(members: tuple[Obligation, ...]) -> None:
    require(0 < len(members) <= MAX_MEMBERS, "finite member budget exceeded or empty")
    unique([member.identity for member in members], "members")
    roles = {}
    for member in members:
        require(member.family in FAMILIES and member.role in FAMILIES[member.family],
                "unknown member role")
        require(all((member.subject, member.member, member.producer, member.consumer,
                     member.representation, member.revalidation, member.probe,
                     member.profile, member.evidence, member.inputs)),
                "incomplete production/evidence mapping")
        roles.setdefault((member.subject, member.family), set()).add(member.role)
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
    kind: str

    def validate(self) -> None:
        sha(self.revision)
        sha(self.tool_revision)
        require(self.verdict in {"satisfied", "contract-violation", "unavailable"},
                "unknown observation verdict")
        require(type(self.checks) is int and self.checks >= 0, "invalid check count")
        require(self.kind in {"native", "arm-object", "parsed", "host"},
                "unknown evidence kind; host results are not ROM observations")
        unique(self.evidence, "evidence classes")
        require(set(self.evidence) == set(self.obligation.evidence),
                "missing or unrelated evidence class")
        unique([path for path, _ in self.source_objects], "source objects")
        require(set(path for path, _ in self.source_objects) == set(self.obligation.inputs),
                "missing or unrelated source evidence")
        for _, oid in self.source_objects:
            sha(oid)
        require(self.verdict == "unavailable" or self.checks > 0,
                "zero tests cannot establish a contract")


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
        require(self.outcome in {"clean", "changes-requested", "untriaged"},
                "unknown coordinator triage")
        sha(self.fact.head)
        timestamp(self.fact.submitted_at)
        unique([item.id for item in self.findings], "accepted findings")
        for finding in self.findings:
            require(finding.origin == self.fact.head and finding.review_id == self.fact.id,
                    "finding has wrong review origin")
        if self.outcome == "clean":
            require(not self.findings and not self.fact.unresolved_threads
                    and self.fact.state != "CHANGES_REQUESTED",
                    "clean triage contradicts review facts")


@dataclass(frozen=True)
class Disposition:
    review_id: str
    held_head: str
    coordinator: str
    action: str
    reason: str


@dataclass
class RoundState:
    consecutive: int = 0
    seen: set[str] = field(default_factory=set)
    hold: tuple[str, str] | None = None
    handoffs: list[dict] = field(default_factory=list)
    dispositions: set[tuple[str, str]] = field(default_factory=set)
    events: list[Triage] = field(default_factory=list)

    def observe(self, review: Triage) -> None:
        review.validate()
        require(review.fact.id not in self.seen, "duplicate review round")
        if self.events:
            previous = self.events[-1].fact
            require((timestamp(review.fact.submitted_at), review.fact.id)
                    >= (timestamp(previous.submitted_at), previous.id),
                    "review chronology moved backwards")
        self.events.append(review)
        self.seen.add(review.fact.id)
        requested = review.outcome == "changes-requested" or self.fact_requests(review)
        if self.hold is not None or (review.outcome == "untriaged" and not requested):
            return
        if review.outcome == "clean":
            self.consecutive = 0
            return
        self.consecutive += 1
        if self.consecutive == 3:
            self.hold = review.fact.id, review.fact.head
        else:
            require(len(review.findings) <= MAX_FINDINGS, "review finding budget exceeded")
            self.handoffs.append({
                "review_id": review.fact.id,
                "head": review.fact.head,
                "consecutive": self.consecutive,
                "findings": [item.id for item in review.findings],
            })

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


@dataclass
class ReviewLease:
    task: Any
    owner: str
    head: str
    scope: frozenset[str]
    started: float
    deadline: float
    finished: bool = False


class ReviewOwnership:
    """Small in-process index owned/shared by the existing coordinator."""

    def __init__(self):
        self.records = {}

    def reserve(self, session):
        require(session.identity is not None, "ownership requires frozen PR identity")
        for identity, head, scope, active in self.records.values():
            if identity[:2] == session.identity[:2] and scope & session.scope:
                require(not active and head != session.head, "duplicate/overlapping review ownership")
        self.records[id(session)] = session.identity, session.head, session.scope, True

    def finish(self, session):
        identity, head, scope, _ = self.records[id(session)]
        self.records[id(session)] = identity, head, scope, False


class ReviewSession:
    """Adapter around the coordinator's existing task/tool calls, not a backend.

    runtime.start/read are the actual CLI task invocation/result operations.
    They are supplied by trusted orchestration code, never by request JSON.
    There is no polling: finish is called after the task-completion event.
    """

    def __init__(self, coordinator: str, implementer: str, scope: frozenset[str],
                 head: str, *, identity=None, owners=None, clock=time.monotonic, readers=None):
        require(bool(coordinator) and bool(implementer) and coordinator != implementer,
                "coordinator and implementer ownership overlap")
        sha(head)
        require(0 < len(scope) <= MAX_SUBJECTS, "invalid accepted review scope")
        if identity is not None:
            require(isinstance(identity, tuple) and len(identity) == 3,
                    "invalid frozen PR identity")
            identifier(identity[0], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "repository")
            require(type(identity[1]) is int and identity[1] > 0, "invalid PR identity")
            sha(identity[2])
        self.coordinator = coordinator
        self.implementer = implementer
        self.scope = scope
        self.head = head
        self.identity = identity
        self.owners = owners
        self.clock = clock
        self.readers = dict(readers or {})
        require(set(self.readers) <= READ_ACTIONS, "unapproved reviewer tool binding")
        self.lease = None
        self.report = None
        self.rounds = RoundState()
        self.accepted: dict[str, Finding] = {}

    def begin(self, runtime, owner: str, *, duration=1200, max_files=200):
        require(self.lease is None, "duplicate or overlapping reviewer ownership")
        require(owner not in {self.coordinator, self.implementer} and bool(owner),
                "reviewer ownership overlaps")
        require(type(duration) is int and 0 < duration <= MAX_REVIEW_SECONDS
                and type(max_files) is int and 0 < max_files <= 200,
                "invalid review bounds")
        if self.owners is not None:
            self.owners.reserve(self)
        try:
            task = runtime.start(
                role="code-review", owner=owner, candidate=self.head,
                subjects=tuple(sorted(self.scope)), actions=tuple(sorted(READ_ACTIONS)),
                duration=duration, max_files=max_files, max_findings=MAX_FINDINGS,
            )
        except Exception:
            if self.owners is not None:
                self.owners.records.pop(id(self), None)
            raise
        started = self.clock()
        self.lease = ReviewLease(task, owner, self.head, self.scope,
                                 started, started + duration)
        return task

    def read_action(self, action: str, *args):
        require(action in READ_ACTIONS, "reviewer action is prohibited")
        require(self.lease is not None and not self.lease.finished
                and self.clock() <= self.lease.deadline, "review lease is not active")
        require(action in self.readers, "reviewer tool is not bound")
        return self.readers[action](*args)

    def finish(self, runtime) -> Any:
        lease = self.lease
        require(lease is not None and not lease.finished, "no active review task")
        require(self.clock() <= lease.deadline, "review exceeded duration bound")
        result = runtime.read(lease.task)
        require(result.task == lease.task and result.owner == lease.owner
                and result.role == "code-review" and result.head == lease.head
                and frozenset(result.subjects) == lease.scope
                and result.completed, "wrong, incomplete or stale task result")
        require(set(result.actions) <= READ_ACTIONS and 0 <= result.files <= 200
                and len(result.findings) <= MAX_FINDINGS, "review action/budget violation")
        require(timestamp(result.started_at) <= timestamp(result.completed_at),
                "review task chronology is invalid")
        lease.finished = True
        if self.owners is not None:
            self.owners.finish(self)
        self.report = result
        return result

    def advance(self, observed_head: str) -> None:
        """Preserve existing work without clearing a review or architecture hold."""
        self.head = sha(observed_head)

    def accept(self, finding: Finding) -> None:
        require(finding.id not in self.accepted, "duplicate accepted finding")
        require(finding.subject in self.scope and finding.family in FAMILIES,
                "finding is outside accepted scope")
        sha(finding.origin)
        self.accepted[finding.id] = finding

    def triage(self, decision: Triage) -> None:
        self.rounds.observe(decision)
        for finding in decision.findings:
            self.accept(finding)


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
    proposed = {item["finding_id"]: item for item in request["findings"]}
    require(set(proposed) == set(session.accepted), "missing or invented accepted finding")
    for finding in session.accepted.values():
        item = proposed[finding.id]
        require((subject_key(item), item["family"], item["reported_member"])
                == (finding.subject, finding.family, finding.member),
                "finding subject/member classification drift")
    require(len({fact.id for fact in remote_reviews}) == len(remote_reviews),
            "duplicate remote review")
    require(tuple(item.fact for item in triage) == remote_reviews,
            "missing triage or changed review content")
    require(tuple(session.rounds.events) == triage, "round state differs from observed triage")
    for item in triage:
        item.validate()
    if pre_review_required:
        require(session.report is not None and session.lease.finished,
                "actual independent task observation required")
        require(session.owners is not None, "coordinator review ownership index required")
        if remote_reviews:
            require(timestamp(session.report.completed_at) < min(
                timestamp(item.submitted_at) for item in remote_reviews),
                "fresh pre-review did not precede the first remote review")
    index = {}
    for observation in observations:
        observation.validate()
        key = observation.obligation.identity, observation.revision
        require(key not in index, "duplicate member observation")
        require(observation.tool_revision == tool_revision, "wrong reviewed tool revision")
        index[key] = observation
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
            require(before is not None and before.obligation == member
                    and before.verdict != "unavailable", "origin evidence unavailable")
            require(finding.source_path in member.inputs or finding.member != member.member,
                    "reported finding has unrelated source evidence")
            outcomes.append({
                "finding_id": finding.id, "member": member.member,
                "outcome": ("affected-fixed" if before.verdict == "contract-violation"
                            else "verified-unaffected"),
            })
    require(set(index) == required, "missing or unrelated test evidence")
    for finding in session.accepted.values():
        matching = [row for row in outcomes if row["finding_id"] == finding.id]
        require(any(row["member"] == finding.member for row in matching),
                "unknown reported member")
        require(any(row["outcome"] == "affected-fixed" for row in matching),
                "no affected-fixed production obligation")
    latest = triage[-1] if triage else None
    clean = bool(latest and latest.fact.head == session.head and latest.outcome == "clean")
    held = session.rounds.hold
    return {
        "schema_version": 1,
        "candidate_sha": session.head,
        "tool_revision": tool_revision,
        "scope": sorted(session.scope),
        "members": len(members),
        "outcomes": outcomes,
        "round_handoffs": session.rounds.handoffs,
        "architecture_hold": None if held is None else {"review_id": held[0], "head": held[1]},
        "new_narrow_work_allowed": held is None,
        "handoff_eligible": held is None and all(item.outcome != "untriaged" for item in triage),
        "exact_head_review_clean": clean,
        "required_final_gates": ["copilot", "security", "candidate-Build", "master-Build",
                                 "remote-completion"],
        "publication": "persist-assigned-branch-as-ineligible-WIP" if held else "immediate",
        "merge_permission": False,
    }
