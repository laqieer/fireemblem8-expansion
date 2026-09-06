"""Adaptive candidate timing over the existing coordinator and review records.

These are unsigned coordinator observations, not authorization supplied by a PR.
The workflow may always be dispatched by an owner; admission is a separate step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import base64
import hashlib
import json
from pathlib import Path
import re

from . import agent_handoff as handoff
from . import candidate_evidence
from . import coordinator_observations as observations
from . import event_classifier
from . import pr_metadata as github
from . import reporter


HIGH_RISKS = frozenset({"protocol", "replay", "transport", "security", "save",
                        "lifecycle", "abi", "migration"})
SECURITY_CHECKS = frozenset({
    ("CodeQL", 57789, "github-advanced-security"),
    ("GitGuardian Security Checks", 46505, "gitguardian"),
})
BINDING_PREFIX = "workflow-pilot-candidate:v1:"
PREFLIGHT_CLASSIFIER = "review-first-classifier"
MAX_CANDIDATES = 128


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class GateDecision:
    head_sha: str
    decision_oid: str | None
    mode: str
    reason: str
    pre_review_required: bool
    known: bool
    paused: bool


def select_mode(raw, *, number, head_sha, decision_oid, changed_lines,
                data=None, repository_root=None):
    reporter.expect_sha(head_sha, "decision head")
    if decision_oid is not None:
        reporter.expect_sha(decision_oid, "decision object")
    try:
        reporter.expect_int(changed_lines, "changed lines", 0)
        record = reporter.historical_decision_record(raw, head_sha, number)
        high_risk = bool(HIGH_RISKS.intersection(record["risk_boundaries"]))
        required = high_risk or changed_lines > 2000
        if record["pilot"]["disposition"] == "paused":
            return GateDecision(head_sha, decision_oid, "concurrent", "pilot-paused",
                                required, True, True)
        history = record["threshold"]["override_history"]
        if history:
            require(data is not None and repository_root is not None,
                    "override provenance unavailable")
            require(reporter.load_decisions_from_commit(repository_root, head_sha) == raw,
                    "override record is not the candidate's committed decision")
            first = min((review for review in data["reviews"].values()
                         if review["pr_number"] == number and review["author"] == reporter.REVIEW_BOT),
                        key=lambda item: reporter.parse_time(item["submitted_at"], "review"),
                        default=None)
            introductions = [event for event in data["events"].values()
                             if event["type"] == "threshold_override_introduced"
                             and event["pr_number"] == number]
            require({item["override_index"] for item in introductions} == set(range(len(history)))
                    and len(introductions) == len(history), "override introductions incomplete")
            for index, override in enumerate(history):
                introduction = next(item for item in introductions if item["override_index"] == index)
                reporter.validate_override_git_provenance(
                    repository_root, data, number, index, override, introduction, first)
            if history[-1]["enabled"] and not high_risk:
                return GateDecision(head_sha, decision_oid, record["gate_mode"],
                                    "validated-pre-review-override", required, True, False)
        mode = "review-first" if required else record["gate_mode"]
        return GateDecision(head_sha, decision_oid, mode,
                            "named-risk" if high_risk else "large-change" if required else "small-change",
                            required, True, False)
    except (KeyError, TypeError, ValueError, reporter.PilotDataError) as error:
        return GateDecision(head_sha, decision_oid, "concurrent",
                            "unknown-decision: " + str(error)[:512], True, False, False)


def pause_for_safety(record, events):
    """Update the existing #176 pause record; never alter final quality gates."""
    relevant = [event for event in events if event["type"] in {
        "security_finding", "escaped_defect", "broken_master"}]
    if relevant:
        record["pilot"]["disposition"] = "paused"
    return tuple(event["id"] for event in relevant)


def fetch_candidate(client, repository, number):
    response = client.request("GET", github._endpoint(repository, f"pulls/{number}"),
                              label="adaptive candidate")
    state = github._parse_pull_request_payload(response.payload, repository, number)
    raw = response.payload
    lines = None
    if type(raw.get("additions")) is int and type(raw.get("deletions")) is int:
        if raw["additions"] >= 0 and raw["deletions"] >= 0:
            lines = raw["additions"] + raw["deletions"]
    return state, lines


def fetch_decision(client, pr, changed_lines):
    oid = None
    raw = None
    try:
        response = client.request(
            "GET", github._query_endpoint(pr.repository, "contents/" + reporter.DECISION_RECORD_PATH.as_posix(),
                                           [("ref", pr.head_sha)]),
            label="committed adaptive decision")
        item = response.payload
        require(item.get("type") == "file" and item.get("encoding") == "base64"
                and item.get("path") == reporter.DECISION_RECORD_PATH.as_posix(),
                "decision is not the selected regular file")
        oid = reporter.expect_sha(item["sha"], "decision object")
        payload = base64.b64decode(item["content"], validate=False)
        require(len(payload) <= observations.MAX_JSON_BYTES, "decision exceeds input bound")
        require(hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest() == oid,
                "decision bytes differ from the Git object")
        raw = reporter.parse_json(payload.decode("utf-8"), "committed adaptive decision")
    except (KeyError, TypeError, ValueError, reporter.PilotDataError, github.MetadataEditError):
        pass
    return select_mode(raw, number=pr.number, head_sha=pr.head_sha, decision_oid=oid,
                       changed_lines=changed_lines)


def frozen_base(client, pr):
    response = client.request(
        "GET", github._endpoint(pr.repository, f"compare/{pr.base_sha}...{pr.head_sha}"),
        label="candidate merge base")
    require(response.payload["base_commit"]["sha"] == pr.base_sha, "compare base identity changed")
    return reporter.expect_sha(response.payload["merge_base_commit"]["sha"], "candidate merge base")


def binding_name(number, head, base):
    reporter.expect_int(number, "PR number", 1)
    reporter.expect_sha(head, "binding head")
    reporter.expect_sha(base, "binding base")
    return f"{BINDING_PREFIX}{number}:{head}:{base}"


def parse_binding(name):
    match = re.fullmatch(re.escape(BINDING_PREFIX) + r"([1-9][0-9]*):([0-9a-f]{40}):([0-9a-f]{40})", name)
    return (int(match[1]), match[2], match[3]) if match else None


def route_event(client, decision, payload, repository):
    """Read only. Missing decision authority keeps the broader full route."""
    if decision.classification != "full" or not decision.identity_valid:
        return decision, None, None
    number = payload.get("number")
    if type(number) is not int or number <= 0 or "pull_request" not in payload:
        return decision, None, None
    event_pr, error = event_classifier._pull_request_identity(
        payload["pull_request"], decision.expected_head, decision.expected_base)
    require(error is None and event_pr is not None, "event/classifier identity changed")
    pr, lines = fetch_candidate(client, repository, number)
    require(pr.head_sha == decision.expected_head and pr.base_ref == event_pr["base"]["ref"]
            and ("ref" not in event_pr["head"] or pr.head_ref == event_pr["head"]["ref"]),
            "pull request head or ref changed")
    # Keep the emitted event identity; compare rather than replace its base tip.
    base = frozen_base(client, replace(pr, base_sha=decision.expected_base))
    if pr.base_sha != decision.expected_base:
        require(frozen_base(client, pr) == base, "event/live candidate merge base changed")
    selected = fetch_decision(client, pr, lines)
    if selected.mode == "review-first":
        decision = replace(decision, classification="review-first", run_expensive=False,
                           reason="review-first-" + selected.reason)
    return decision, selected, binding_name(pr.number, pr.head_sha, base)


def route_dispatch(client, decision, payload, repository, ref):
    require(payload.get("inputs") in (None, {}) and isinstance(ref, str)
            and ref.startswith("refs/heads/")
            and event_classifier._is_git_branch_ref(ref[len("refs/heads/"):]),
            "full dispatch must be input-free on a branch")
    branch = ref[len("refs/heads/"):]
    response = client.request(
        "GET", github._query_endpoint(repository, "pulls",
                                       [("state", "open"), ("head", repository.split("/")[0] + ":" + branch),
                                        ("per_page", "100")]),
        label="dispatched candidate branch")
    require(not response.headers.get("link") and isinstance(response.payload, list)
            and len(response.payload) == 1, "dispatch needs one unambiguous open candidate")
    number = reporter.expect_int(response.payload[0]["number"], "dispatch PR", 1)
    pr, lines = fetch_candidate(client, repository, number)
    require(pr.head_ref == branch and pr.head_sha == decision.expected_head,
            "dispatched branch head changed")
    selected = fetch_decision(client, pr, lines)
    return (replace(decision, expected_base=pr.base_sha), selected,
            binding_name(pr.number, pr.head_sha, frozen_base(client, pr)))


@dataclass(frozen=True)
class SecurityCheck:
    id: int
    name: str
    app_id: int
    app_slug: str
    head_sha: str
    status: str
    conclusion: str | None
    created_at: str
    completed_at: str | None


def security_checks(client, pr):
    rows = github._list_counted_pages(
        client, endpoint_for_page=lambda page: github._query_endpoint(
            pr.repository, f"commits/{pr.head_sha}/check-runs",
            [("filter", "latest"), ("per_page", "100"), ("page", str(page))]),
        item_key="check_runs", label="exact security checks", maximum=1000,
        repository=pr.repository, repository_id=pr.repository_id)
    result = []
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("app"), dict),
                "malformed security check record")
        identity = row.get("name"), row.get("app", {}).get("id"), row.get("app", {}).get("slug")
        if identity[0] not in {item[0] for item in SECURITY_CHECKS}:
            continue
        require(identity in SECURITY_CHECKS, "security check has the wrong app identity")
        require(row.get("head_sha") == pr.head_sha, "stale security check")
        reporter.expect_int(row.get("id"), "security check ID", 1)
        reporter.parse_time(row.get("started_at") or row.get("created_at"), "security start")
        status, conclusion = row.get("status"), row.get("conclusion")
        require(status in {"queued", "in_progress", "completed"}, "unknown security status")
        require((status == "completed") == (conclusion is not None), "incoherent security completion")
        if status == "completed":
            reporter.parse_time(row.get("completed_at"), "security completion")
        result.append(SecurityCheck(row["id"], *identity, pr.head_sha, status, conclusion,
                                    row.get("started_at") or row.get("created_at"),
                                    row.get("completed_at")))
    require(len({item.id for item in result}) == len(result), "duplicate security check")
    require({(item.name, item.app_id, item.app_slug) for item in result} == SECURITY_CHECKS
            and len(result) == len(SECURITY_CHECKS), "missing or ambiguous exact security checks")
    return tuple(sorted(result, key=lambda item: item.name))


def validate_candidate_records(records):
    seen = set()
    for record in handoff.items(records, maximum=MAX_CANDIDATES):
        handoff.fields(record, "pr_number head_sha base_sha base_ref decision_oid mode created_at "
                       "abandoned_reason dispatch_requested_at dispatch_sent_at watermark "
                       "full_run_id full_attempt")
        handoff.integer(record["pr_number"], minimum=1)
        for key in ("head_sha", "base_sha"):
            handoff.sha(record[key])
        if record["decision_oid"] is not None:
            handoff.sha(record["decision_oid"])
        handoff.text(record["base_ref"], maximum=256)
        handoff.choice(record["mode"], reporter.GATE_MODES)
        for key in ("created_at", "dispatch_requested_at", "dispatch_sent_at"):
            if record[key] is not None:
                handoff.timestamp(record[key])
        require(record["created_at"] is not None, "candidate creation observation missing")
        handoff.text(record["abandoned_reason"], nullable=True, maximum=2048)
        handoff.integer(record["watermark"])
        for key in ("full_run_id", "full_attempt"):
            handoff.integer(record[key], minimum=1, nullable=True)
        require((record["full_run_id"] is None) == (record["full_attempt"] is None),
                "incomplete full run binding")
        require(record["dispatch_sent_at"] is None or record["dispatch_requested_at"] is not None,
                "dispatch completion lacks reservation")
        if record["dispatch_requested_at"] is not None:
            require(handoff.timestamp(record["created_at"]) <=
                    handoff.timestamp(record["dispatch_requested_at"]), "dispatch predates candidate")
        if record["dispatch_sent_at"] is not None:
            require(handoff.timestamp(record["dispatch_requested_at"]) <=
                    handoff.timestamp(record["dispatch_sent_at"]), "dispatch chronology reversed")
        identity = record["pr_number"], record["head_sha"], record["base_sha"], record["base_ref"]
        require(identity not in seen, "duplicate candidate identity")
        seen.add(identity)


def begin_candidate(state, pr, base, decision):
    handoff.validate_state(state)
    require(state["repository"] == pr.repository and decision.head_sha == pr.head_sha,
            "candidate coordinator identity mismatch")
    reporter.expect_sha(base, "candidate base")
    records = state.setdefault("candidates", [])
    key = pr.number, pr.head_sha, base, pr.base_ref
    for record in records:
        identity = record["pr_number"], record["head_sha"], record["base_sha"], record["base_ref"]
        if identity == key:
            require(record["decision_oid"] == decision.decision_oid, "decision identity changed")
            return record
        if record["pr_number"] == pr.number and record["abandoned_reason"] is None:
            record["abandoned_reason"] = "superseded-head-or-base"
    require(len(records) < MAX_CANDIDATES, "candidate history bound reached")
    abandoned = next((item["abandoned_reason"] for item in records
                      if item["pr_number"] == pr.number and item["head_sha"] == pr.head_sha
                      and item["abandoned_reason"] not in (None, "superseded-head-or-base")), None)
    record = {
        "pr_number": pr.number, "head_sha": pr.head_sha, "base_sha": base, "base_ref": pr.base_ref,
        "decision_oid": decision.decision_oid, "mode": decision.mode, "created_at": observations.utc_now(),
        "abandoned_reason": abandoned, "dispatch_requested_at": None, "dispatch_sent_at": None,
        "watermark": 0, "full_run_id": None, "full_attempt": None,
    }
    records.append(record)
    handoff.validate_state(state)
    return record


def _local_ready(state, pr):
    handoff.summarize_handoffs(state)
    return any(entry["validation"] is not None and entry["validation"]["handoff_ready"]
               and entry["validation"]["result_sha"] == pr.head_sha and entry["closed_at"] is not None
               and entry["assignment"]["repository"] == pr.repository
               and entry["assignment"]["pull_request"] in (None, pr.number)
               and entry["assignment"]["expected_branch"] == pr.head_ref
               for entry in state["assignments"])


def assess_candidate(state, record, decision, pr, session, facts, triage, checks, runs,
                     *, family_evidence=None, accepted_security=(), criteria_ready=False):
    """Consume existing typed observations. Does not dispatch, merge or launch a watcher."""
    handoff.validate_state(state)
    require(type(criteria_ready) is bool, "objective/manual readiness must be an actual decision")
    require(record in state.get("candidates", ()), "unrecorded candidate")
    require((pr.repository, pr.number, session.head, session.identity) ==
            (state["repository"], record["pr_number"], record["head_sha"],
             (pr.repository, pr.number, record["base_sha"])), "candidate/review identity mismatch")
    require(pr.head_sha == record["head_sha"] and pr.base_ref == record["base_ref"]
            and decision.head_sha == pr.head_sha and decision.decision_oid == record["decision_oid"],
            "candidate head/base/decision changed")
    current_findings = tuple(item for item in session.accepted.values() if item.origin == pr.head_sha)
    require(all(item in checks for item in accepted_security), "stale accepted security finding")
    if current_findings or accepted_security:
        record["abandoned_reason"] = "accepted-review-or-security-finding"
    rebound = any(item["pr_number"] == record["pr_number"]
                  and item["head_sha"] == record["head_sha"]
                  and (item["base_sha"], item["base_ref"]) !=
                  (record["base_sha"], record["base_ref"])
                  for item in state["candidates"])
    missing = []
    try:
        ready, clean = session.review_state(facts, triage, pre_review_required=decision.pre_review_required)
        if not ready or not clean:
            missing.append("exact-clean-review")
        if (rebound and clean and reporter.parse_time(triage[-1].fact.submitted_at, "review") <
                reporter.parse_time(record["created_at"], "candidate")):
            missing.append("review-predates-candidate-binding")
        if session.accepted and not current_findings:
            require(family_evidence is not None, "prior accepted findings need sibling evidence")
            request, members, observations_, tool_revision = family_evidence
            from .review_family import assess_handoff
            family = assess_handoff(
                request, members, observations_, session, tool_revision=tool_revision,
                remote_reviews=facts, triage=triage, pre_review_required=decision.pre_review_required)
            require(family["handoff_eligible"] and family["exact_head_review_clean"],
                    "sibling evidence is not eligible")
    except ValueError as error:
        missing.append("review: " + str(error)[:512])
    if session.rounds.hold is not None:
        missing.append("architecture-hold")
    if not _local_ready(state, pr):
        missing.append("exact-local-handoff")
    security_ready = (
        len(checks) == len(SECURITY_CHECKS)
        and {(item.name, item.app_id, item.app_slug) for item in checks} == SECURITY_CHECKS
        and all(item.head_sha == pr.head_sha and item.status == "completed"
                and item.conclusion == "success" and item.completed_at is not None
                and (not rebound or reporter.parse_time(item.created_at, "security") >=
                     reporter.parse_time(record["created_at"], "candidate")) for item in checks))
    if not security_ready:
        missing.append("exact-clean-security")
    expected_binding = pr.number, pr.head_sha, record["base_sha"]
    matching = [run for run in runs if run.candidate_binding == expected_binding
                and run.head_sha == pr.head_sha and run.head_branch == pr.head_ref]
    unknown = [run for run in runs if run.head_sha == pr.head_sha and run.head_branch == pr.head_ref
               and run.status in github.ACTIVE_RUN_STATUSES and run.binding != "explicit-other"
               and (run.candidate_binding is None or run.mode == "active-unknown")]
    if unknown:
        missing.append("unclassified-active-run")
    visible = [*matching, *(run for run in unknown if run not in matching)]
    full = [run for run in matching if run.mode in {"full", "active-full"}]
    if any(run.head_sha != pr.head_sha for run in runs):
        missing.append("stale-run")
    if len({run.run_id for run in full}) != len(full) or len(full) > 1:
        missing.append("duplicate-full-run")
    preflight = any(run.mode == "review-first" and candidate_evidence.preflight_success(
        {job.name: (job.status, job.conclusion) for job in run.jobs}) for run in matching)
    if decision.mode == "review-first" and not preflight:
        missing.append("exact-preflight")
    if record["mode"] != decision.mode:
        missing.append("gate-mode-changed")
    if record["abandoned_reason"] is not None:
        missing.append("abandoned-candidate")
    if not criteria_ready:
        missing.append("objective-or-manual-criteria")
    admitted = None
    if len(full) == 1:
        run = full[0]
        if record["full_attempt"] not in (None, run.run_attempt):
            missing.append("unbound-run-attempt")
        elif record["full_run_id"] not in (None, run.run_id):
            missing.append("wrong-full-run")
        elif decision.mode == "review-first":
            # GitHub creation times have second precision; retain the native reservation.
            if (record["dispatch_sent_at"] is None or run.event != "workflow_dispatch"
                    or run.run_number <= record["watermark"]
                    or run.created_at < reporter.parse_time(
                        record["dispatch_requested_at"], "dispatch").replace(microsecond=0)):
                missing.append("early-or-unbound-full-run")
            else:
                record["full_run_id"], record["full_attempt"] = run.run_id, run.run_attempt
                admitted = run
        elif run.event == "pull_request":
            record["full_run_id"], record["full_attempt"] = run.run_id, run.run_attempt
            admitted = run
        else:
            missing.append("unexpected-full-dispatch")
    complete = False
    if admitted is not None:
        try:
            github.require_full_success(admitted)
            complete = True
        except github.MetadataEditError:
            if admitted.status == "completed":
                missing.append("full-Build-" + str(admitted.conclusion))
    elif decision.mode == "concurrent":
        missing.append("full-Build-missing")
    dispatchable = (not missing and decision.mode == "review-first" and not full
                    and record["dispatch_requested_at"] is None)
    phase = ("superseded" if record["abandoned_reason"] == "superseded-head-or-base" else
             "review-abandoned" if record["abandoned_reason"] else
             "merge-ready" if complete and not missing else
             "build-failed" if admitted and admitted.status == "completed" and not complete else
             "building" if admitted else
             "review-first-preflight" if decision.mode == "review-first" and not preflight else
             "dispatchable" if dispatchable else "review-pending")
    return {
        "repository": pr.repository, "pull_request": pr.number, "head_sha": pr.head_sha,
        "base_sha": record["base_sha"], "live_base_sha": pr.base_sha,
        "decision": asdict(decision), "state": phase, "missing": sorted(set(missing)),
        "dispatchable": dispatchable, "merge_eligible": complete and not missing,
        "record": dict(record), "review_ids": [fact.id for fact in facts],
        "rounds": session.rounds.consecutive, "findings": [item.id for item in current_findings],
        "unresolved": sum(len(fact.unresolved_threads) for fact in facts),
        "security": [asdict(item) for item in checks],
        "runs": [{"run_id": run.run_id, "attempt": run.run_attempt, "mode": run.mode} for run in visible],
        "final_master_build_required": True, "remote_completion_required": True,
    }


def evidence_comment(assessment, preserved_text=""):
    require(github.EVIDENCE_MARKER not in preserved_text, "duplicate canonical evidence marker")
    return (github.EVIDENCE_MARKER + "\n" + preserved_text.rstrip() +
            "\n\n## Adaptive candidate gates\n\n```json\n" +
            json.dumps(assessment, sort_keys=True, indent=2) + "\n```\n")


def assess_observed(client, state, record, session, triage, review_tools, *,
                    family_evidence=None, accepted_security=(), criteria_ready=False):
    """Refresh through #177/#179 and validate the unique actual Git merge base."""
    from . import trusted_review_gate

    class ReviewAPI(trusted_review_gate.GitHub):
        def query(self, repository, number, cursor=None):
            owner, name = repository.split("/")
            return client.request(
                "POST", "graphql", body={"query": trusted_review_gate.REVIEW_QUERY,
                                         "variables": {"owner": owner, "name": name,
                                                       "number": number, "cursor": cursor}},
                label="actual review facts").payload

    pr, lines = fetch_candidate(client, state["repository"], record["pr_number"])
    decision = fetch_decision(client, pr, lines)
    request = {"candidate_sha": pr.head_sha, "base_sha": record["base_sha"]}
    review_tools.validate_base(request, pr.base_sha)
    remote = ReviewAPI()
    identity, facts = remote.snapshot(pr.repository, pr.number, review_tools.model)
    require(identity == (pr.base_sha, pr.head_sha), "review/PR identity changed")
    checks = security_checks(client, pr)
    runs = github.list_candidate_runs(client, pr, include_dispatch=True)
    after, _ = fetch_candidate(client, pr.repository, pr.number)
    require((after.head_sha, after.head_ref, after.base_ref) ==
            (pr.head_sha, pr.head_ref, pr.base_ref), "candidate changed during assessment")
    review_tools.validate_base(request, after.base_sha)
    after_identity, after_facts = remote.snapshot(pr.repository, pr.number, review_tools.model)
    if after_facts != facts:
        session.refresh_reviews(after_facts)
    require(after_identity == (after.base_sha, pr.head_sha) and after_facts == facts,
            "review evidence changed during assessment")
    require(security_checks(client, after) == checks, "security evidence changed during assessment")
    require(github.list_candidate_runs(client, after, include_dispatch=True) == runs,
            "Build evidence changed during assessment")
    assessment = assess_candidate(
        state, record, decision, after, session, facts, triage, checks, runs,
        family_evidence=family_evidence, accepted_security=accepted_security,
        criteria_ready=criteria_ready)
    return assessment, runs


def reserve_full_dispatch(state, record, assessment, runs):
    handoff.validate_state(state)
    require(record in state.get("candidates", ()) and assessment["record"] == record
            and assessment["dispatchable"], "candidate is not dispatchable")
    require(record["dispatch_requested_at"] is None and record["abandoned_reason"] is None,
            "duplicate or abandoned full dispatch")
    record["watermark"] = max((run.run_number for run in runs), default=0)
    record["dispatch_requested_at"] = observations.utc_now()
    return record["dispatch_requested_at"]


def dispatch_full(client, state_path, pr, assess):
    """Persist reservation before POST. Unknown delivery never permits retry."""
    with observations.locked_state(state_path) as state:
        handoff.validate_state(state)
        record, assessment, runs = assess(state)
        current = github.fetch_pull_request(client, pr.repository, pr.number)
        github.require_identity(current, head_sha=pr.head_sha, base_sha=pr.base_sha)
        require((current.head_ref, current.base_ref) == (pr.head_ref, pr.base_ref),
                "dispatch branch identity changed")
        reservation = reserve_full_dispatch(state, record, assessment, runs)
        identity = record["pr_number"], record["head_sha"], record["base_sha"], record["base_ref"]
    client.request("POST", github._endpoint(pr.repository, "actions/workflows/build.yml/dispatches"),
                   body={"ref": pr.head_ref}, label="one input-free full Build")
    with observations.locked_state(state_path) as state:
        record = next(item for item in state["candidates"] if (
            item["pr_number"], item["head_sha"], item["base_sha"], item["base_ref"]) == identity)
        require(record["dispatch_requested_at"] == reservation, "dispatch reservation changed")
        record["dispatch_sent_at"] = observations.utc_now()
    return {"state": "dispatch-observation-pending", "head_sha": pr.head_sha}


def cancel_abandoned(client, state_path, record, run):
    state = observations.load_json(state_path)
    handoff.validate_state(state)
    require(record in state.get("candidates", ()) and record["abandoned_reason"] is not None,
            "cancellation requires recorded abandonment")
    require(run.head_sha == record["head_sha"] and run.candidate_binding ==
            (record["pr_number"], record["head_sha"], record["base_sha"]),
            "cancellation would affect unrelated work")
    actual = observations.github_run(state["repository"], run.run_id, run.run_attempt, run.head_sha)
    require(actual["status"] != "completed" and actual["workflow_id"] == run.workflow_id,
            "run is terminal or belongs to another workflow")
    client.request("POST", github._endpoint(state["repository"], f"actions/runs/{run.run_id}/cancel"),
                   label="cancel abandoned full Build")
    return actual
