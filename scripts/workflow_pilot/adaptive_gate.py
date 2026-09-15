"""Adaptive candidate timing over the existing coordinator and review records.

These are unsigned coordinator observations, not authorization supplied by a PR.
The workflow may always be dispatched by an owner; admission is a separate step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import quote, unquote_to_bytes

from . import agent_handoff as handoff
from . import candidate_evidence
from . import coordinator_observations as observations
from . import event_classifier
from . import pr_metadata as github
from . import reporter
from .review_family import MAX_REVIEW_FILES


HIGH_RISKS = frozenset({"protocol", "replay", "transport", "security", "save",
                        "lifecycle", "abi", "migration"})
SECURITY_CHECKS = frozenset({
    ("CodeQL", 57789, "github-advanced-security"),
    ("GitGuardian Security Checks", 46505, "gitguardian"),
})
BINDING_PREFIX = "workflow-pilot-candidate:v1:"
PREFLIGHT_CLASSIFIER = "review-first-classifier"
MAX_CANDIDATES = 128
OWNERSHIP_CHECK_ID = "validation-ownership"


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class PilotControl:
    repository: str
    repository_id: int
    default_ref: str
    commit_sha: str
    decision_oid: str
    paused: bool
    observed_at: str

    @property
    def identity(self):
        return (self.repository, self.repository_id, self.default_ref,
                self.commit_sha, self.decision_oid, self.paused)


@dataclass(frozen=True)
class GateDecision:
    head_sha: str
    decision_oid: str | None
    mode: str
    reason: str
    pre_review_required: bool
    known: bool
    paused: bool
    control: PilotControl | None = None


def validate_pilot_control(value):
    handoff.fields(value, "repository repository_id default_ref commit_sha decision_oid paused observed_at")
    handoff.text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    handoff.integer(value["repository_id"], minimum=1)
    handoff.text(value["default_ref"], maximum=256)
    require(event_classifier._is_git_branch_ref(value["default_ref"]), "invalid control ref")
    handoff.sha(value["commit_sha"])
    handoff.sha(value["decision_oid"])
    handoff.boolean(value["paused"])
    handoff.timestamp(value["observed_at"])
    return value


def select_mode(raw, *, number, head_sha, decision_oid, changed_lines,
                data=None, repository_root=None, verify_override=None):
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
        if high_risk:
            return GateDecision(head_sha, decision_oid, "review-first", "named-risk",
                                True, True, False)
        history = record["threshold"]["override_history"]
        eligible = False
        if history and verify_override is None:
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
            if history[-1]["enabled"]:
                eligible = _local_override_scope(repository_root, data, number, head_sha, raw, changed_lines)
        if history:
            if verify_override is not None:
                eligible = verify_override(record)
                require(type(eligible) is bool, "override scope authority unavailable")
            if history[-1]["enabled"] and eligible:
                return GateDecision(head_sha, decision_oid, record["gate_mode"],
                                    "validated-pre-review-override", required, True, False)
        mode = "review-first" if required else record["gate_mode"]
        return GateDecision(head_sha, decision_oid, mode,
                            "named-risk" if high_risk else "large-change" if required else "small-change",
                            required, True, False)
    except (KeyError, TypeError, ValueError, OSError, ImportError, reporter.PilotDataError) as error:
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


def _decision_at(client, pr, revision):
    reporter.expect_sha(revision, "decision revision")
    response = client.request(
        "GET", github._query_endpoint(pr.repository, "contents/" + reporter.DECISION_RECORD_PATH.as_posix(),
                                       [("ref", revision)]), label="committed adaptive decision")
    item = response.payload
    require(item.get("type") == "file" and item.get("encoding") == "base64"
            and item.get("path") == reporter.DECISION_RECORD_PATH.as_posix(),
            "decision is not the selected regular file")
    oid = reporter.expect_sha(item["sha"], "decision object")
    payload = base64.b64decode(item["content"], validate=False)
    require(len(payload) <= observations.MAX_JSON_BYTES, "decision exceeds input bound")
    require(hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest() == oid,
            "decision bytes differ from the Git object")
    return reporter.parse_json(payload.decode("utf-8"), "committed adaptive decision"), oid


def _baseline_numbers(repository):
    baseline = reporter.load_json(Path(__file__).resolve().parents[2] / reporter.BASELINE_FIXTURE_PATH)
    return ({item["number"] for item in baseline["pull_requests"]}
            if baseline["repository"] == repository else set())


def validate_control_collection(raw, revision, repository):
    """Validate every #176 record, not just the first row that requests a pause."""
    reporter.expect_object(raw, "current decision collection")
    reporter.expect_keys(raw, "current decision collection", ("schema_version", "pull_requests", "artifacts"))
    require(reporter.expect_int(raw["schema_version"], "decision schema", 1) == reporter.SCHEMA_VERSION,
            "unknown current decision schema")
    records = reporter.expect_list(raw["pull_requests"], "current decision records")
    baseline = _baseline_numbers(repository)
    paused = False
    for item in records:
        reporter.expect_object(item, "current decision")
        number = reporter.expect_int(item["pull_request"], "current decision PR", 1)
        record = reporter.historical_decision_record(raw, revision, number)
        stack = record["stack"]
        require((stack["depth"] == 0) == (stack["parent_pr"] is None)
                and stack["parent_pr"] != number, "invalid current decision stack")
        require(stack["depth"] < 3 or stack["exception_reason"] is not None,
                "current deep stack lacks its exception")
        if number in baseline:
            require(record["pilot"] == {"included": False, "disposition": "baseline-only"},
                    "frozen baseline record cannot become a current safety latch")
        else:
            paused |= record["pilot"]["disposition"] == "paused"
    artifacts = reporter.expect_list(raw["artifacts"], "current artifact records")
    ids, unique = [], []
    for record in artifacts:
        reporter.expect_object(record, "current artifact")
        reporter.expect_keys(record, "current artifact", (
            "artifact_id", "owner", "executable_consumer", "unique_decision", "consistency_check",
            "max_maintenance_minutes", "estimated_maintenance_minutes", "deletion_criterion", "expires_at", "history"))
        for key in ("artifact_id", "owner", "executable_consumer", "unique_decision",
                    "consistency_check", "deletion_criterion"):
            reporter.expect_string(record[key], "current artifact " + key)
        ids.append(record["artifact_id"])
        unique.append(record["unique_decision"])
        maximum = reporter.expect_int(record["max_maintenance_minutes"], "artifact maximum", 1)
        require(reporter.expect_int(record["estimated_maintenance_minutes"], "artifact estimate", 0) <= maximum,
                "artifact exceeds its maintenance bound")
        reporter.parse_time(record["expires_at"], "artifact expiry", nullable=True)
        history = reporter.expect_list(record["history"], "artifact history")
        require(bool(history), "artifact history is empty")
        previous = None
        for event in history:
            reporter.expect_object(event, "artifact history entry")
            reporter.expect_keys(event, "artifact history entry", ("recorded_at", "disposition", "reason"))
            at = reporter.parse_time(event["recorded_at"], "artifact history time")
            require(previous is None or previous < at, "artifact history is not chronological")
            previous = at
            reporter.expect_enum(event["disposition"], reporter.ARTIFACT_DISPOSITIONS, "artifact disposition")
            reporter.expect_string(event["reason"], "artifact reason")
    reporter.expect_unique(ids, "current artifact identities")
    reporter.expect_unique(unique, "current artifact decisions")
    return paused


def _default_source(client, repository, repository_id=None):
    github._repository(repository)
    response = client.request("GET", github._endpoint(repository, "").rstrip("/"),
                              label="pilot control repository")
    require(not response.headers.get("link"), "pilot control repository response is incomplete")
    data = response.payload
    require(data["full_name"] == repository, "pilot control repository changed")
    actual_id = github._positive_int(data["id"], "pilot control repository ID")
    require(repository_id in (None, actual_id), "pilot control repository ID changed")
    branch = data["default_branch"]
    require(event_classifier._is_git_branch_ref(branch), "pilot control default ref is invalid")
    response = client.request("GET", github._endpoint(repository, "git/ref/heads/" + quote(branch, safe="")),
                              label="pilot control default ref")
    require(not response.headers.get("link"), "pilot control ref response is incomplete")
    reference = response.payload
    require(reference["ref"] == "refs/heads/" + branch and reference["object"]["type"] == "commit",
            "pilot control default ref is not the requested commit")
    return actual_id, branch, reporter.expect_sha(reference["object"]["sha"], "pilot control commit")


def _tree_entry(client, repository, tree, path):
    response = client.request("GET", github._endpoint(repository, "git/trees/" + tree),
                              label="pilot control tree")
    data = response.payload
    require(not response.headers.get("link") and data["sha"] == tree and data.get("truncated") is False,
            "pilot control tree is incomplete")
    entries = reporter.expect_list(data["tree"], "pilot control entries")
    for entry in entries:
        reporter.expect_object(entry, "pilot control entry")
        reporter.expect_string(entry["path"], "pilot control entry path")
    reporter.expect_unique([entry["path"] for entry in entries], "pilot control paths")
    matches = [entry for entry in entries if entry["path"] == path]
    require(len(matches) == 1, "pilot control path is missing or ambiguous")
    reporter.expect_sha(matches[0]["sha"], "pilot control object")
    return matches[0]


def fetch_pilot_control(client, repository, repository_id=None):
    """Fresh default-ref/commit/regular-blob observation; errors are not unpause."""
    identity = _default_source(client, repository, repository_id)
    actual_id, branch, revision = identity
    response = client.request("GET", github._endpoint(repository, "git/commits/" + revision),
                              label="pilot control commit")
    require(not response.headers.get("link"), "pilot control commit response is incomplete")
    commit = response.payload
    require(commit["sha"] == revision, "pilot control commit identity changed")
    root = reporter.expect_sha(commit["tree"]["sha"], "pilot control root tree")
    directory = _tree_entry(client, repository, root, ".github")
    require((directory["type"], directory["mode"]) == ("tree", "040000"),
            "pilot control directory is not a regular tree")
    entry = _tree_entry(client, repository, directory["sha"], reporter.DECISION_RECORD_PATH.name)
    require(entry["type"] == "blob" and entry["mode"] in {"100644", "100755"},
            "pilot control is not a regular decision blob")
    raw, oid = _decision_at(client, SimpleNamespace(repository=repository), revision)
    require(oid == entry["sha"], "pilot control contents differ from the selected tree")
    paused = validate_control_collection(raw, revision, repository)
    require(_default_source(client, repository, actual_id) == identity, "pilot control moved during observation")
    return PilotControl(repository, actual_id, branch, revision, oid, paused, observations.utc_now())


def validate_safety_publication(value):
    handoff.fields(value, "operation event attribution control target_oid disposition")
    handoff.choice(value["operation"], {"pause", "unpause"})
    event = value["event"]
    handoff.fields(event, "id type occurred_at pr_number sha")
    handoff.text(event["id"])
    handoff.choice(event["type"], {"security_finding", "escaped_defect", "broken_master"})
    handoff.timestamp(event["occurred_at"])
    handoff.integer(event["pr_number"], minimum=1)
    handoff.sha(event["sha"])
    handoff.validate_check(value["attribution"])
    check = value["attribution"]
    require(check["contract"] == "coordinator-check" and check["exit_code"] == 0
            and check["completed_at"] is not None and check["pid"] is not None
            and check["peak_rss_bytes"] is not None, "safety publication lacks actual attribution")
    require(check["id"] == "safety"
            and check["evidence_id"] == ("safety-attribution" if value["operation"] == "pause" else "safety-recovery")
            and handoff.timestamp(check["completed_at"]) >= handoff.timestamp(event["occurred_at"]),
            "safety attribution role or chronology differs")
    if value["operation"] == "pause":
        require(check["result_sha"] == event["sha"], "safety attribution names another incident head")
    if value["control"] is not None:
        validate_pilot_control(value["control"])
    if value["target_oid"] is not None:
        handoff.sha(value["target_oid"])
        require(value["control"] is not None, "publication target lacks a control source")
    handoff.choice(value["disposition"], reporter.PILOT_DISPOSITIONS - {"paused", "baseline-only"}
                   if value["operation"] == "unpause" else {None})


def _safety_event(raw_fixture, event_id, root):
    data = reporter.validate_fixture(raw_fixture)
    reporter.validate_repository_authority(root, data)
    event = data["events"].get(event_id)
    require(event is not None and event["type"] in {
        "security_finding", "escaped_defect", "broken_master"}, "not a validated safety event")
    pr = data["pull_requests"][event["pr_number"]]
    require(pr["state"] == "merged" and pr["merge_sha"] is not None
            and reporter.parse_time(event["occurred_at"], "incident") >=
            reporter.parse_time(pr["merged_at"], "incident merge"),
            "ordinary pre-merge findings are not attributable pilot incidents")
    require(event["pr_number"] not in _baseline_numbers(data["fixture"]["repository"]),
            "frozen baseline incidents cannot create a current latch")
    reporter.run_git(root, "merge-base", "--is-ancestor", pr["merge_sha"], event["sha"])
    return data, event, pr


def _control_ancestor(client, repository, before, after):
    response = client.request("GET", github._endpoint(repository, f"compare/{before}...{after}"),
                              label="control publication ancestry")
    require(not response.headers.get("link") and response.payload["base_commit"]["sha"] == before
            and response.payload["merge_base_commit"]["sha"] == before,
            "current control does not retain the accepted source history")


def _master_run(client, repository, branch, head, run_id, attempt, *, success, repository_id):
    handoff.integer(run_id, minimum=1)
    handoff.integer(attempt, minimum=1)
    workflow = github._workflow_authority(client, SimpleNamespace(repository=repository))
    response = client.request(
        "GET", github._endpoint(repository, f"actions/runs/{run_id}/attempts/{attempt}"),
        label="actual automatic master Build")
    run = response.payload
    for key in ("id", "run_attempt", "workflow_id"):
        github._positive_int(run[key], "master " + key)
    require(run["id"] == run_id and run["run_attempt"] == attempt
            and run["repository"]["full_name"] == repository
            and github._positive_int(run["repository"]["id"], "master repository ID") == repository_id
            and run["workflow_id"] == workflow.workflow_id and run["event"] == "push"
            and run["head_branch"] == branch and run["head_sha"] == head
            and run["path"] in {github.WORKFLOW_PATH, github.WORKFLOW_PATH + "@refs/heads/" + branch}
            and run["status"] == "completed"
            and run["conclusion"] == ("success" if success else "failure"),
            "safety evidence is not the exact automatic master Build outcome")
    times = [github._github_timestamp(run[name], "master " + name)
             for name in ("created_at", "run_started_at", "updated_at")]
    require(times == sorted(times), "master Build chronology is inconsistent")
    if success:
        rows = github._list_counted_pages(
            client, endpoint_for_page=lambda page: github._query_endpoint(
                repository, f"actions/runs/{run_id}/attempts/{attempt}/jobs",
                [("per_page", "100"), ("page", str(page))]),
            item_key="jobs", label="master recovery jobs", maximum=1000,
            repository=repository, repository_id=repository_id)
        jobs = tuple(github._parse_job(
            row, state=SimpleNamespace(repository=repository), run_id=run_id, run_attempt=attempt,
            head_sha=head, head_branch=branch, workflow=workflow, event="push",
            run_created_at=times[0], run_started_at=times[1], run_updated_at=times[2]) for row in rows)
        require({job.name for job in jobs} == candidate_evidence.KNOWN_JOB_IDS
                and len(jobs) == len(candidate_evidence.KNOWN_JOB_IDS)
                and len({job.job_id for job in jobs}) == len(jobs), "master recovery graph is incomplete")
        github.require_full_success(SimpleNamespace(mode="full", status=run["status"],
                                                    conclusion=run["conclusion"], jobs=jobs))
    return run


def _attribution(root, revision, parent, event, data, executor, *, recovery, master_run):
    context = {
        "allowed_worktree": str(root), "assigned_parent_sha": parent,
        "required_checks": {"safety": {"contract": "coordinator-check",
                                      "evidence_id": "safety-recovery" if recovery else "safety-attribution",
                                      "inputs": []}},
        "safety_event": copy.deepcopy(event),
        "safety_events": tuple(copy.deepcopy(item) for item in data["events"].values()
                               if item["type"] in {"security_finding", "escaped_defect", "broken_master"}
                               and item["pr_number"] == event["pr_number"]),
        "recovery": recovery,
        "master_run": copy.deepcopy(master_run),
    }
    check = handoff.capture_check({"assignment": context, "checks": []}, "safety", revision,
                                  executor, task_owned=False)
    require(check["exit_code"] == 0 and check["pid"] is not None and check["peak_rss_bytes"] is not None,
            "actual incident attribution/recovery check did not succeed")
    return check


def prepare_safety_publication(client, state_path, publication_root):
    """Prepare only an ordinary owner branch; publication/merge stays with its owner."""
    state = observations.load_json(state_path)
    handoff.validate_state(state)
    pending = state.get("safety_publication")
    require(pending is not None, "no accepted safety publication")
    control = fetch_pilot_control(client, state["repository"])
    raw, oid = _decision_at(client, SimpleNamespace(repository=state["repository"]), control.commit_sha)
    require(oid == control.decision_oid, "control source changed")
    record = reporter.historical_decision_record(raw, control.commit_sha, pending["event"]["pr_number"])
    require(record["pilot"]["disposition"] != "baseline-only"
            and record["pull_request"] not in _baseline_numbers(state["repository"]),
            "baseline records cannot become a live control")
    before = copy.deepcopy(raw)
    if pending["operation"] == "pause":
        pause_for_safety(record, [pending["event"]])
    else:
        require(record["pilot"]["disposition"] == "paused", "unpause requires an explicit current latch")
        require(pending["attribution"]["result_sha"] == control.commit_sha,
                "unpause recovery is not the current default head")
        record["pilot"]["disposition"] = pending["disposition"]
    validate_control_collection(raw, control.commit_sha, state["repository"])
    _control_ancestor(client, state["repository"], pending["event"]["sha"], control.commit_sha)
    payload = reporter.normalized_json(raw)
    target = (control.decision_oid if raw == before else
              hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest())
    with observations.locked_state(state_path) as current:
        handoff.validate_state(current)
        require(current["safety_publication"] == pending, "safety publication was replaced")
        current["safety_publication"].update(control=asdict(control), target_oid=target)
    if target != control.decision_oid:
        root = Path(publication_root).resolve(strict=True)
        actual = handoff.observe_git({"allowed_worktree": str(root)})
        require(actual["branch"] is not None and actual["branch"] != control.default_ref
                and not actual["dirty_paths"] and not actual["conflicting"],
                "safety publication needs a clean ordinary non-default branch")
        origin = reporter.run_git(root, "config", "--get", "remote.origin.url").decode().strip()
        require(reporter._github_repository_from_remote(origin) == state["repository"],
                "safety publication repository differs")
        reporter.run_git(root, "merge-base", "--is-ancestor", control.commit_sha, actual["head"])
        local_oid = reporter.run_git(root, "rev-parse", "HEAD:" + str(reporter.DECISION_RECORD_PATH)).decode().strip()
        require(local_oid == control.decision_oid, "publication branch must retain the current control")
        require(fetch_pilot_control(client, state["repository"]).identity == control.identity,
                "control moved before publication preparation")
        (root / reporter.DECISION_RECORD_PATH).write_bytes(payload)
    return {"state": "publication-pending", "prepared": True, "target_oid": target,
            "control": asdict(control), "global_visibility_confirmed": False}


def _start_safety_publication(client, state_path, raw_fixture, event_id, attribution_root,
                              publication_root, executor, *, recovery, disposition=None, master_run=None):
    root = Path(attribution_root).resolve(strict=True)
    data, event, pr = _safety_event(raw_fixture, event_id, root)
    repository = data["fixture"]["repository"]
    revision = event["sha"]
    master = None
    if recovery:
        control = fetch_pilot_control(client, repository)
        current, _ = _decision_at(client, SimpleNamespace(repository=repository), control.commit_sha)
        current_record = reporter.historical_decision_record(current, control.commit_sha, event["pr_number"])
        require(current_record["pilot"]["disposition"] == "paused",
                "unpause needs this incident's explicit current latch")
        revision = control.commit_sha
        require(master_run is not None, "unpause requires current master recovery evidence")
        master = _master_run(client, repository, control.default_ref, revision, *master_run,
                             success=True, repository_id=control.repository_id)
        checks = security_checks(client, SimpleNamespace(repository=repository, head_sha=revision,
                                                         repository_id=control.repository_id))
        require(len(checks) == len(SECURITY_CHECKS)
                and {(check.name, check.app_id, check.app_slug) for check in checks} == SECURITY_CHECKS
                and all(
            check.status == "completed" and check.conclusion == "success"
            and check.completed_at is not None for check in checks),
            "unpause requires exact clean security")
    elif event["type"] == "broken_master":
        require(master_run is not None, "broken-master attribution requires its actual run")
        repository_id, branch, _ = _default_source(client, repository)
        master = _master_run(client, repository, branch, revision, *master_run,
                             success=False, repository_id=repository_id)
        reported = data["runs"].get(master_run[0])
        require(reported is not None and reported["attempt"] == master_run[1]
                and reported["workflow"] == "Build CI" and reported["event"] == "push"
                and reported["head_sha"] == revision and reported["head_branch"] == branch
                and reported["status"] == "completed" and reported["conclusion"] == "failure"
                and reporter.parse_time(reported["created_at"], "reported master creation") ==
                github._github_timestamp(master["created_at"], "master creation")
                and reporter.parse_time(reported["started_at"], "reported master start") ==
                github._github_timestamp(master["run_started_at"], "master start")
                and reporter.parse_time(reported["completed_at"], "reported master completion") <=
                reporter.parse_time(event["occurred_at"], "incident observation"),
                "broken-master run differs from the validated incident snapshot")
    check = _attribution(root, revision, pr["merge_sha"], event, data, executor,
                         recovery=recovery, master_run=master)
    if master is not None:
        require(_master_run(client, repository,
                            control.default_ref if recovery else branch, revision, *master_run,
                            success=recovery, repository_id=control.repository_id if recovery else repository_id) == master,
                "master safety evidence changed during attribution")
    if recovery:
        require(security_checks(client, SimpleNamespace(
            repository=repository, head_sha=revision, repository_id=control.repository_id)) == checks,
            "security recovery evidence changed during attribution")
    pending = {"operation": "unpause" if recovery else "pause", "event": copy.deepcopy(event),
               "attribution": check, "control": None, "target_oid": None, "disposition": disposition}
    validate_safety_publication(pending)
    with observations.locked_state(state_path) as state:
        handoff.validate_state(state)
        require(state["repository"] == repository and state.get("safety_publication") is None,
                "another safety publication remains held")
        state["safety_publication"] = pending
    try:
        return prepare_safety_publication(client, state_path, publication_root)
    except (KeyError, TypeError, ValueError, OSError, reporter.PilotDataError) as error:
        return {"state": "publication-pending", "prepared": False,
                "global_visibility_confirmed": False, "detail": str(error)[:2048]}


def pause_pilot(client, state_path, raw_fixture, event_id, attribution_root, publication_root,
                trusted_attribution, *, master_run=None):
    return _start_safety_publication(
        client, state_path, raw_fixture, event_id, attribution_root, publication_root,
        trusted_attribution, recovery=False, master_run=master_run)


def unpause_pilot(client, state_path, raw_fixture, event_id, attribution_root, publication_root,
                  trusted_recovery, *, disposition, master_run):
    return _start_safety_publication(
        client, state_path, raw_fixture, event_id, attribution_root, publication_root,
        trusted_recovery, recovery=True, disposition=disposition, master_run=master_run)


def confirm_safety_publication(client, state_path):
    with observations.locked_state(state_path) as state:
        handoff.validate_state(state)
        pending = state.get("safety_publication")
        require(pending is not None and pending["target_oid"] is not None,
                "safety publication is not prepared")
        control = fetch_pilot_control(client, state["repository"])
        before = PilotControl(**pending["control"])
        require((control.repository_id, control.default_ref, control.decision_oid) ==
                (before.repository_id, before.default_ref, pending["target_oid"]),
                "global safety publication is not confirmed")
        _control_ancestor(client, state["repository"], before.commit_sha, control.commit_sha)
        require(fetch_pilot_control(client, state["repository"]).identity == control.identity,
                "control moved during publication confirmation")
        del state["safety_publication"]
    return {"state": "publication-confirmed", "control": asdict(control),
            "event_id": pending["event"]["id"], "global_visibility_confirmed": True}


def _review_snapshot(client, pr, model):
    from . import trusted_review_gate

    class ReviewAPI(trusted_review_gate.GitHub):
        def query(self, repository, number, cursor=None):
            owner, name = repository.split("/")
            return client.request(
                "POST", "graphql", body={"query": trusted_review_gate.REVIEW_QUERY,
                                         "variables": {"owner": owner, "name": name,
                                                       "number": number, "cursor": cursor}},
                label="actual review facts").payload

    return ReviewAPI().snapshot(pr.repository, pr.number, model)


def _verify_remote_override(client, pr, record, raw, changed_lines):
    """Validate the actual immutable decision as of the first submitted review."""
    from . import review_family
    identity, facts = _review_snapshot(client, pr, review_family)
    require(identity[1] == pr.head_sha, "override review head changed")
    first = facts[0] if facts else None
    revision = first.head if first else pr.head_sha
    if first:
        historical, _ = _decision_at(client, pr, revision)
        require(reporter.historical_decision_record(historical, revision, pr.number) == record,
                "override decision was missing or changed at the first reviewed commit")
        require(frozen_base(client, replace(pr, base_sha=revision)) == revision,
                "override reviewed commit is not in candidate ancestry")
    commit = client.request("GET", github._endpoint(pr.repository, f"git/commits/{revision}"),
                            label="immutable override commit").payload
    require(commit["sha"] == revision, "override commit identity changed")
    committed = reporter.parse_time(commit["committer"]["date"], "override commit date")
    cutoff = reporter.parse_time(first.submitted_at if first else observations.utc_now(), "override cutoff")
    require(committed < cutoff if first else committed <= cutoff,
            "override commit does not predate its first review")
    return (_remote_override_scope(client, pr, raw, changed_lines)
            if record["threshold"]["override_history"][-1]["enabled"] else False)


def _override_scope(files, raw, number, read_base_decisions):
    """Conservative data/document membership, not a semantic validation waiver."""
    from scripts.generated_data.registry import REGISTRY
    from scripts.localization import catalog
    from scripts.localization.game_catalog.build import DEFAULT_AUTHORED_PATHS, DEFAULT_EU_AUTHORED_PATHS
    from scripts.upstream_port.classify import classify_path

    metadata = [item for item in files if item["filename"] == str(reporter.DECISION_RECORD_PATH)]
    if metadata:
        if metadata[0]["status"] not in {"added", "modified"}:
            return False
        before = ({"schema_version": reporter.SCHEMA_VERSION, "artifacts": [], "pull_requests": []}
                  if metadata[0]["status"] == "added" else read_base_decisions())

        def other_decisions(value):
            reporter.expect_keys(value, "scope decisions", ("schema_version", "artifacts", "pull_requests"))
            require(reporter.expect_int(value["schema_version"], "scope schema", 1) == reporter.SCHEMA_VERSION,
                    "unknown scope decision schema")
            reporter.expect_list(value["artifacts"], "scope artifacts")
            others = reporter.project_cohort_decisions(
                value, {item["pull_request"] for item in value["pull_requests"]} - {number})
            others["pull_requests"] = sorted(others["pull_requests"], key=lambda item: item["pull_request"])
            others["artifacts"] = sorted(others["artifacts"], key=lambda item: item["artifact_id"])
            return others

        if not handoff._same_json_value(other_decisions(before), other_decisions(raw)):
            return False
    files = [item for item in files if item not in metadata]
    if not files:
        return False
    if all(item["status"] == "removed" and item["additions"] == 0 and item["deletions"] > 0
           for item in files):
        return True
    generated = {value for name in REGISTRY.all_names()
                 for value in (REGISTRY.resolve(name).default_source, REGISTRY.resolve(name).default_inventory_path)
                 if value and Path(value).suffix}
    localized = {str(value) for value in (
        catalog.DEFAULT_REGISTRY_PATH, *catalog.DEFAULT_CATALOG_PATHS.values(),
        *DEFAULT_AUTHORED_PATHS.values(), *DEFAULT_EU_AUTHORED_PATHS.values())}
    paths = {item["filename"] for item in files} | {
        item["previous_filename"] for item in files if item["status"] == "renamed"}
    return all(name in generated or name in localized or (
        not reporter.is_generated_path(name) and classify_path(name) == "docs"
        and Path(name).suffix == ".md") for name in paths)


def _remote_override_scope(client, pr, raw, changed_lines):
    endpoint = github._endpoint(pr.repository, f"pulls/{pr.number}")

    def observe():
        payload = client.request("GET", endpoint, label="override scope candidate").payload
        current = github._parse_pull_request_payload(payload, pr.repository, pr.number)
        require((current.head_sha, current.head_ref, current.base_sha, current.base_ref)
                == (pr.head_sha, pr.head_ref, pr.base_sha, pr.base_ref), "override scope candidate changed")
        counts = tuple(reporter.expect_int(payload[name], "override " + name, 0)
                       for name in ("changed_files", "additions", "deletions"))
        require(counts[1] + counts[2] == changed_lines, "override diff size changed")
        return counts

    counts = observe()
    endpoint_compare = github._endpoint(pr.repository, f"compare/{pr.base_sha}...{pr.head_sha}")
    response = client.request("GET", endpoint_compare, label="immutable override diff")
    comparison = response.payload
    github._require_api_url(comparison["url"], endpoint_compare, field="override compare URL")
    require(not response.headers.get("link"), "paginated override diff is unavailable")
    require(comparison["base_commit"]["sha"] == pr.base_sha, "override comparison base changed")
    base = reporter.expect_sha(comparison["merge_base_commit"]["sha"], "override merge base")
    commits = reporter.expect_list(comparison["commits"], "override commits")
    require(len(commits) == reporter.expect_int(comparison["total_commits"], "override commit count", 0)
            and (commits[-1]["sha"] == pr.head_sha if commits else pr.head_sha == pr.base_sha),
            "override comparison head or commit completeness changed")
    reporter.expect_unique([reporter.expect_sha(item["sha"], "override commit") for item in commits],
                           "override commit identities")
    files = reporter.expect_list(comparison["files"], "override files")
    require(len(files) == counts[0], "override file list truncated")
    names = []
    for item in files:
        handoff.path(item["filename"])
        names.append(item["filename"])
        reporter.expect_sha(item["sha"], "override file object")
        require(item["status"] in {"added", "modified", "removed", "renamed"}, "unknown override file status")
        if item["status"] == "renamed":
            handoff.path(item["previous_filename"])
        added, deleted, changed = (reporter.expect_int(item[key], "override file " + key, 0)
                                   for key in ("additions", "deletions", "changes"))
        require(added + deleted == changed and (item["status"] != "added" or deleted == 0)
                and (item["status"] != "removed" or added == 0), "incoherent override file counts")
    reporter.expect_unique(names, "override changed paths")
    require((sum(item["additions"] for item in files), sum(item["deletions"] for item in files)) == counts[1:],
            "override diff totals incomplete")
    eligible = _override_scope(files, raw, pr.number, lambda: _decision_at(client, pr, base)[0])
    require(observe() == counts, "override file authority changed during observation")
    return eligible


def _local_override_scope(root, data, number, head, raw, changed_lines):
    pr = data["pull_requests"][number]
    require(pr["state"] == "merged" and pr["head_sha"] == head, "local scope needs an immutable merged candidate")
    merge = reporter.expect_sha(pr["merge_sha"], "scope merge")
    parents = reporter.run_git(root, "show", "-s", "--format=%P", merge).decode().split()
    require(len(parents) == 2 and parents[1] == head, "scope merge does not bind the candidate")
    bases = reporter.run_git(root, "merge-base", "--all", parents[0], head).decode().split()
    require(len(bases) == 1, "scope merge base is ambiguous")
    base = bases[0]
    changes = handoff._changes(root, base, head)
    files = []
    for row in handoff._git(root, "diff", "--numstat", "--no-ext-diff", "--no-textconv",
                           "--no-renames", "-z", base, head, "--").split(b"\0"):
        if not row:
            continue
        added, deleted, name = row.split(b"\t", 2)
        filename = name.decode("utf-8")
        old_mode, new_mode, _, _ = changes[filename]
        require({old_mode, new_mode} <= {b"000000", b"100644", b"100755"}, "unsupported scope file mode")
        files.append({"filename": filename, "additions": int(added), "deletions": int(deleted),
                      "status": "removed" if new_mode == b"000000" else
                                "added" if old_mode == b"000000" else "modified"})
    require({item["filename"] for item in files} == set(changes)
            and sum(item["additions"] + item["deletions"] for item in files) == changed_lines,
            "local override diff is incomplete or has another size")
    return _override_scope(files, raw, number, lambda: reporter.load_decisions_from_commit(root, base))


def fetch_decision(client, pr, changed_lines):
    oid = None
    raw = None
    try:
        raw, oid = _decision_at(client, pr, pr.head_sha)
    except (KeyError, TypeError, ValueError, reporter.PilotDataError, github.MetadataEditError):
        pass
    selected = select_mode(raw, number=pr.number, head_sha=pr.head_sha, decision_oid=oid,
                           changed_lines=changed_lines,
                           verify_override=lambda record: _verify_remote_override(client, pr, record, raw, changed_lines))
    if selected.known:
        try:
            stack_default = _validate_live_stack(client, pr, raw)
        except (KeyError, TypeError, ValueError, reporter.PilotDataError, github.MetadataEditError) as error:
            return replace(selected, mode="concurrent", known=False, pre_review_required=True,
                           reason="unknown-decision: stack: " + str(error)[:480])
    try:
        control = fetch_pilot_control(client, pr.repository, pr.repository_id)
        require(not selected.known or control.default_ref == stack_default,
                "default branch changed between stack and control observations")
    except (KeyError, TypeError, ValueError, OSError, reporter.PilotDataError, github.MetadataEditError) as error:
        return replace(selected, mode="concurrent", known=False, pre_review_required=True,
                       reason="unknown-pilot-control: " + str(error)[:480])
    if control.paused:
        selected = replace(selected, mode="concurrent", paused=True,
                           reason="pilot-paused" if selected.known else selected.reason + "; pilot-paused")
    return replace(selected, control=control)


def _validate_live_stack(client, pr, raw):
    endpoint = github._endpoint(pr.repository, "").rstrip("/")

    def repository():
        value = client.request("GET", endpoint, label="stack repository").payload
        require(value["full_name"] == pr.repository and
                github._positive_int(value["id"], "stack repository ID") == pr.repository_id,
                "stack repository identity changed")
        require(event_classifier._is_git_branch_ref(value["default_branch"]), "stack default branch unavailable")
        return value["default_branch"]

    default = repository()
    first = reporter.historical_decision_record(raw, pr.head_sha, pr.number)
    records, observed = {}, {pr.number: pr}
    current, contents = pr, raw
    for _ in range(first["stack"]["depth"] + 1):
        record = reporter.historical_decision_record(contents, current.head_sha, current.number)
        records[current.number] = record
        parent = record["stack"]["parent_pr"]
        if parent is None or parent in records or len(records) > first["stack"]["depth"]:
            break
        current, _ = fetch_candidate(client, pr.repository, parent)
        observed[parent] = current
        contents, _ = _decision_at(client, current, current.head_sha)
    reporter.validate_stack_decisions(records, {
        "fixture": {"default_branch": default},
        "pull_requests": {number: {"base_ref": item.base_ref, "head_branch": item.head_ref}
                          for number, item in observed.items()},
    })
    for number, record in records.items():
        parent = record["stack"]["parent_pr"]
        if parent is not None:
            child, upstream = observed[number], observed[parent]
            require(child.base_sha == upstream.head_sha
                    and frozen_base(client, child) == upstream.head_sha,
                    "stack parent head is not incorporated into its child")
    for number, before in observed.items():
        after, _ = fetch_candidate(client, pr.repository, number)
        require((after.head_sha, after.head_ref, after.base_ref) ==
                (before.head_sha, before.head_ref, before.base_ref), "stack parent/candidate moved")
        if after.base_sha != before.base_sha:
            require(records[number]["stack"]["parent_pr"] is None
                    and frozen_base(client, before) == frozen_base(client, after),
                    "stack base changed during observation")
    require(repository() == default, "stack default branch changed")
    return default


def frozen_base(client, pr):
    response = client.request(
        "GET", github._endpoint(pr.repository, f"compare/{pr.base_sha}...{pr.head_sha}"),
        label="candidate merge base")
    require(response.payload["base_commit"]["sha"] == pr.base_sha, "compare base identity changed")
    return reporter.expect_sha(response.payload["merge_base_commit"]["sha"], "candidate merge base")


def binding_name(number, head, base, base_ref=None):
    reporter.expect_int(number, "PR number", 1)
    reporter.expect_sha(head, "binding head")
    reporter.expect_sha(base, "binding base")
    name = f"{BINDING_PREFIX}{number}:{head}:{base}"
    if base_ref is not None:
        require(event_classifier._is_git_branch_ref(base_ref), "invalid binding base ref")
        name += ":" + quote(base_ref, safe="")
    return name


def _binding_fields(name):
    match = re.fullmatch(re.escape(BINDING_PREFIX) +
                        r"([1-9][0-9]*):([0-9a-f]{40}):([0-9a-f]{40})(?::([^:\s]+))?", name)
    if not match:
        return None, None
    base_ref = None
    if match[4] is not None:
        try:
            base_ref = unquote_to_bytes(match[4]).decode("utf-8")
        except UnicodeError:
            return None, None
        if not event_classifier._is_git_branch_ref(base_ref) or quote(base_ref, safe="") != match[4]:
            return None, None
    return (int(match[1]), match[2], match[3]), base_ref


def parse_binding(name):
    return _binding_fields(name)[0]


def binding_base_ref(name):
    return _binding_fields(name)[1]


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
    return decision, selected, binding_name(pr.number, pr.head_sha, base, event_pr["base"]["ref"])


def route_dispatch(client, decision, payload, repository, ref, *, expected_candidate):
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
    listed = github._parse_pull_request_payload(response.payload[0], repository, number)
    head_repository = response.payload[0]["head"].get("repo") or {}
    require(head_repository.get("full_name") == repository
            and head_repository.get("id") == listed.repository_id,
            "dispatched candidate belongs to another head repository")
    pr, lines = fetch_candidate(client, repository, number)
    for observed in (listed, pr):
        require(observed.head_ref == branch and observed.head_sha == decision.expected_head
                and (observed.number, observed.base_sha, observed.base_ref) == expected_candidate,
                "dispatched candidate differs from the checked-out integration base")
    selected = fetch_decision(client, pr, lines)
    return (replace(decision, expected_base=pr.base_sha), selected,
            binding_name(pr.number, pr.head_sha, frozen_base(client, pr), pr.base_ref))


@dataclass(frozen=True)
class SecurityCheck:
    id: int
    name: str
    app_id: int
    app_slug: str
    head_sha: str
    status: str
    conclusion: str | None
    created_at: str | None
    completed_at: str | None


def security_checks(client, pr):
    rows = github._list_counted_pages(
        client, endpoint_for_page=lambda page: github._query_endpoint(
            pr.repository, f"commits/{pr.head_sha}/check-runs",
            [("filter", "latest"), ("per_page", "100"), ("page", str(page))]),
        item_key="check_runs", label="exact security checks", maximum=1000,
        repository=pr.repository, repository_id=pr.repository_id)
    result, seen_ids, seen_checks = [], set(), set()
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("app"), dict),
                "malformed security check record")
        identity = row.get("name"), row.get("app", {}).get("id"), row.get("app", {}).get("slug")
        reporter.expect_string(identity[0], "check name")
        reporter.expect_int(identity[1], "check app ID", 1)
        reporter.expect_string(identity[2], "check app slug")
        require(row.get("head_sha") == pr.head_sha, "stale security check")
        reporter.expect_int(row.get("id"), "security check ID", 1)
        require(row["id"] not in seen_ids, "duplicate security check")
        seen_ids.add(row["id"])
        for field in ("created_at", "started_at", "completed_at"):
            if row.get(field) is not None:
                reporter.parse_time(row[field], "security " + field)
        started = row.get("started_at") or row.get("created_at")
        status, conclusion = row.get("status"), row.get("conclusion")
        require(isinstance(status, str) and status in {"queued", "in_progress", "completed"},
                "unknown security status")
        require((status == "completed") == (conclusion is not None), "incoherent security completion")
        require(conclusion is None or (isinstance(conclusion, str) and conclusion in github.RUN_CONCLUSIONS),
                "unknown security conclusion")
        if status != "queued":
            require(started is not None, "security start is missing")
        if status == "completed":
            completed = reporter.parse_time(row.get("completed_at"), "security completion")
            require(reporter.parse_time(started, "security start") <= completed,
                    "security chronology is inconsistent")
        else:
            require(row.get("completed_at") is None, "active security check has completion time")
        if row.get("created_at") is not None and row.get("started_at") is not None:
            require(reporter.parse_time(row["created_at"], "security creation") <=
                    reporter.parse_time(row["started_at"], "security start"),
                    "security start precedes creation")
        if identity[0] not in {item[0] for item in SECURITY_CHECKS}:
            continue
        require(identity in SECURITY_CHECKS, "security check has the wrong app identity")
        require(identity not in seen_checks, "ambiguous exact security checks")
        seen_checks.add(identity)
        result.append(SecurityCheck(row["id"], *identity, pr.head_sha, status, conclusion,
                                    started, row.get("completed_at")))
    return tuple(sorted(result, key=lambda item: item.name))


def candidate_identity(record):
    """The existing frozen identity, within its repository-scoped coordinator state."""
    return tuple(record[key] for key in ("pr_number", "head_sha", "base_sha", "base_ref"))


def find_candidate(state, identity):
    require(isinstance(identity, tuple) and len(identity) == 4, "complete candidate identity required")
    handoff.integer(identity[0], minimum=1)
    for revision in identity[1:3]:
        handoff.sha(revision)
    handoff.text(identity[3], maximum=256)
    records = [record for record in state.get("candidates", ()) if candidate_identity(record) == identity]
    require(len(records) == 1, "candidate identity is missing or ambiguous")
    return records[0]


def validate_candidate_records(records):
    seen = set()
    for record in handoff.items(records, maximum=MAX_CANDIDATES):
        handoff.fields(record, "pr_number head_sha base_sha base_ref decision_oid mode created_at "
                       "abandoned_reason dispatch_requested_at dispatch_sent_at watermark "
                       "full_run_id full_attempt"
                       + (" local_validation" if isinstance(record, dict) and "local_validation" in record else "")
                       + (" dispatch_observed_at" if isinstance(record, dict) and "dispatch_observed_at" in record else "")
                       + (" intake_control" if isinstance(record, dict) and "intake_control" in record else ""))
        if "local_validation" in record:
            validate_local_validation(record["local_validation"])
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
        if "intake_control" in record:
            control = validate_pilot_control(record["intake_control"])
            require(handoff.timestamp(control["observed_at"]) <= handoff.timestamp(record["created_at"]),
                    "intake control postdates candidate registration")
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
        if "dispatch_observed_at" in record:
            handoff.timestamp(record["dispatch_observed_at"])
            require(record["dispatch_requested_at"] is not None and record["full_run_id"] is not None,
                    "observed dispatch lacks reservation/run binding")
            require(handoff.timestamp(record["dispatch_requested_at"]) <=
                    handoff.timestamp(record["dispatch_observed_at"]), "dispatch observation predates request")
        identity = candidate_identity(record)
        require(identity not in seen, "duplicate candidate identity")
        seen.add(identity)


def _history_lane(pr, base, runs):
    require(runs is not None, "candidate intake requires a complete run observation")
    relevant = []
    observed = False
    for run in runs:
        if run.head_sha != pr.head_sha or run.head_branch != pr.head_ref:
            continue
        if run.candidate_binding not in (None, (pr.number, pr.head_sha, base)):
            continue
        if run.candidate_base_ref not in (None, pr.base_ref):
            continue
        observed = True
        if run.mode in {"metadata-only", "active-metadata-only"}:
            continue
        require(run.candidate_binding is not None and run.candidate_base_ref is not None
                and run.mode != "active-unknown", "candidate intake history is unproven")
        relevant.append(run)
    if not relevant:
        require(not observed, "candidate intake has no initial full/preflight evidence")
        return None
    require(len({run.workflow_id for run in relevant}) == 1, "candidate intake workflow is ambiguous")
    lanes = set()
    for run in relevant:
        if run.mode == "review-first" and candidate_evidence.preflight_success(
                {job.name: (job.status, job.conclusion) for job in run.jobs}):
            lanes.add("review-first")
        elif run.mode in {"full", "active-full"} and run.event == "pull_request":
            lanes.add("concurrent")
    require(len(lanes) == 1, "candidate initial execution lane is unknown or ambiguous")
    return next(iter(lanes))


def _finalize_intake(record, pr, runs):
    lane = _history_lane(pr, record["base_sha"], runs)
    require(lane is not None, "initial execution lane has not been observed")
    if "intake_control" in record:
        control = record["intake_control"]
        require((control["repository"], control["repository_id"]) == (pr.repository, pr.repository_id)
                and record["full_run_id"] is None and record["dispatch_requested_at"] is None
                and record["abandoned_reason"] is None, "provisional intake already claims authority")
        record["mode"] = lane
        del record["intake_control"]
    else:
        require(lane == record["mode"], "historical initial lane contradicts the observed run history")


def begin_candidate(state, pr, base, decision, *, runs=None):
    handoff.validate_state(state)
    require(state["repository"] == pr.repository and decision.head_sha == pr.head_sha,
            "candidate coordinator identity mismatch")
    reporter.expect_sha(base, "candidate base")
    records = state.setdefault("candidates", [])
    key = pr.number, pr.head_sha, base, pr.base_ref
    if any(candidate_identity(record) == key for record in records):
        record = find_candidate(state, key)
        require(record["decision_oid"] == decision.decision_oid, "decision identity changed")
        if runs is not None:
            _finalize_intake(record, pr, runs)
        return record
    require(decision.known and decision.control is not None, "candidate intake control is unavailable")
    validate_pilot_control(asdict(decision.control))
    require(not decision.paused or decision.mode == "concurrent", "incoherent paused intake mode")
    require(not decision.control.paused or decision.paused, "global pause was dropped at intake")
    require((decision.control.repository, decision.control.repository_id) == (pr.repository, pr.repository_id),
            "candidate intake control repository differs")
    lane = _history_lane(pr, base, runs)
    require(len(records) < MAX_CANDIDATES, "candidate history bound reached")
    for record in records:
        if record["pr_number"] == pr.number and record["abandoned_reason"] is None:
            record["abandoned_reason"] = "superseded-head-or-base"
    abandoned = next((item["abandoned_reason"] for item in records
                      if item["pr_number"] == pr.number and item["head_sha"] == pr.head_sha
                      and item["abandoned_reason"] not in (None, "superseded-head-or-base")), None)
    record = {
        "pr_number": pr.number, "head_sha": pr.head_sha, "base_sha": base, "base_ref": pr.base_ref,
        "decision_oid": decision.decision_oid, "mode": lane or decision.mode, "created_at": observations.utc_now(),
        "abandoned_reason": abandoned, "dispatch_requested_at": None, "dispatch_sent_at": None,
        "watermark": 0, "full_run_id": None, "full_attempt": None,
    }
    if lane is None:
        record["intake_control"] = asdict(decision.control)
    records.append(record)
    handoff.validate_state(state)
    return record


def begin_observed_candidate(client, state, number):
    """Intake from one refreshed candidate/control/run observation under the caller's lock."""
    handoff.validate_state(state)
    pr, lines = fetch_candidate(client, state["repository"], number)
    decision = fetch_decision(client, pr, lines)
    require(decision.known and decision.control is not None, "live intake authority is unavailable")
    base = frozen_base(client, pr)
    runs = github.list_candidate_runs(client, pr, include_dispatch=True)
    after, _ = fetch_candidate(client, pr.repository, number)
    require((after.head_sha, after.head_ref, after.base_ref) == (pr.head_sha, pr.head_ref, pr.base_ref)
            and frozen_base(client, after) == base, "candidate changed during intake")
    require(fetch_pilot_control(client, pr.repository, pr.repository_id).identity == decision.control.identity,
            "pilot control changed during intake")
    return begin_candidate(state, after, base, decision, runs=runs)


def _validate_local_checks(checks):
    handoff.validate_required_checks(checks)
    require(any(check["contract"] == "coordinator-check" for check in checks.values()),
            "coordinator validation requires registered semantic checks, not raw diff alone")


def validate_review_qualification(value):
    handoff.fields(
        value,
        "schema_version case_id repository pull_request base_sha candidate_sha worktree "
        "checker_revision changed_paths changed_edge_ids affected_consumers "
        "review_scope review_task reviewer review_started_at review_completed_at coordinator_id "
        "git_identity",
    )
    require(value["schema_version"] == 1 and type(value["schema_version"]) is int,
            "review qualification schema version changed")
    handoff.text(value["case_id"], maximum=128, pattern=handoff.ID_RE)
    handoff.text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    handoff.integer(value["pull_request"], minimum=1)
    for key in ("base_sha", "candidate_sha", "checker_revision"):
        handoff.sha(value[key])
    handoff.absolute_path(value["worktree"])
    for path in handoff.items(value["changed_paths"], minimum=1, maximum=MAX_REVIEW_FILES, unique=True):
        handoff.path(path)
    for key in ("changed_edge_ids", "affected_consumers"):
        for item in handoff.items(value[key], minimum=0, maximum=256, unique=True):
            handoff.text(item, maximum=256, pattern=handoff.ID_RE)
    require(bool(value["changed_edge_ids"]) == bool(value["affected_consumers"]),
            "reviewed edge and consumer scopes must both be empty or nonempty")
    for item in handoff.items(value["review_scope"], minimum=4, maximum=40, unique=True):
        handoff.text(item, maximum=1024)
    try:
        from scripts.validation_ownership.coordinator_capture import REVIEW_CASE_ID, reviewed_evolution_scope

        require(value["case_id"] == REVIEW_CASE_ID, "review qualification case identity changed")
        expected_scope = sorted(reviewed_evolution_scope(
            value["checker_revision"],
            value["changed_paths"],
            value["changed_edge_ids"],
            value["affected_consumers"],
        ))
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    require(value["review_scope"] == expected_scope,
            "review qualification scope differs from its complete explicit arrays")
    for key in ("review_task", "reviewer"):
        handoff.text(value[key], maximum=256)
    handoff.text(value["coordinator_id"], maximum=128, pattern=handoff.ID_RE)
    for key in ("review_started_at", "review_completed_at"):
        handoff.timestamp(value[key])
    require(handoff.timestamp(value["review_started_at"])
            <= handoff.timestamp(value["review_completed_at"]),
            "review qualification chronology reversed")
    handoff.fields(value["git_identity"], "worktree git_dir common_dir device inode")
    for key in ("worktree", "git_dir", "common_dir"):
        handoff.absolute_path(value["git_identity"][key])
    handoff.integer(value["git_identity"]["device"])
    handoff.integer(value["git_identity"]["inode"], minimum=1)


def validate_local_validation(local):
    handoff.fields(local, "coordinator_id repository pr_number head_sha base_sha base_ref branch worktree "
                   "git_identity registered_at clock required_checks checks"
                   + (" review_qualification" if isinstance(local, dict)
                      and "review_qualification" in local else ""))
    handoff.text(local["coordinator_id"], maximum=128, pattern=handoff.ID_RE)
    handoff.text(local["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    handoff.integer(local["pr_number"], minimum=1)
    for key in ("head_sha", "base_sha"):
        handoff.sha(local[key])
    for key in ("base_ref", "branch"):
        handoff.text(local[key], maximum=256)
    handoff.absolute_path(local["worktree"])
    handoff.timestamp(local["registered_at"])
    handoff.validate_clock(local["clock"])
    require(local["registered_at"] == local["clock"]["at"], "local registration clock mismatch")
    handoff.fields(local["git_identity"], "worktree git_dir common_dir device inode")
    for key in ("worktree", "git_dir", "common_dir"):
        handoff.absolute_path(local["git_identity"][key])
    handoff.integer(local["git_identity"]["device"])
    handoff.integer(local["git_identity"]["inode"], minimum=1)
    _validate_local_checks(local["required_checks"])
    if "review_qualification" in local:
        validate_review_qualification(local["review_qualification"])
        qualification = local["review_qualification"]
        require(
            (
                qualification["repository"],
                qualification["pull_request"],
                qualification["candidate_sha"],
                qualification["base_sha"],
                qualification["worktree"],
                qualification["coordinator_id"],
                qualification["git_identity"],
            )
            == (
                local["repository"],
                local["pr_number"],
                local["head_sha"],
                local["base_sha"],
                local["worktree"],
                local["coordinator_id"],
                local["git_identity"],
            ),
            "review qualification differs from local validation identity",
        )
    for check_id, captured in handoff.named_records(local["checks"]):
        handoff.fields(captured, "definitions observation")
        handoff.validate_check(captured["observation"])
        require(captured["observation"]["id"] == check_id, "captured check ID mismatch")
        _validate_local_checks(captured["definitions"])


def _local_delegations(state, pr):
    predecessors = {entry["assignment"]["predecessor_id"] for entry in state["assignments"]}
    return [entry for entry in state["assignments"]
            if entry["assignment"]["id"] not in predecessors
            and entry["assignment"]["repository"] == pr.repository
            and (entry["assignment"]["pull_request"] == pr.number
                 or (entry["assignment"]["pull_request"] is None
                     and entry["assignment"]["expected_branch"] == pr.head_ref))]


def _delegation_incomplete(entries):
    return any(entry["closed_at"] is None or entry["validation"] is None
               or not entry["validation"]["handoff_ready"] for entry in entries)


def _coordinator_git(state, record, pr, worktree):
    handoff.summarize_handoffs(state)
    require(not handoff.availability_errors(state, observations.utc_now()), "coordinator availability unavailable")
    require(find_candidate(state, candidate_identity(record)) == record and state["repository"] == pr.repository,
            "local candidate identity changed")
    require(not _delegation_incomplete(_local_delegations(state, pr)),
            "applicable delegated handoff is incomplete or invalid")
    require(record["abandoned_reason"] is None, "candidate is abandoned")
    current = handoff.observe_git({"allowed_worktree": str(worktree)})
    require(current["head"] == pr.head_sha and current["branch"] == pr.head_ref
            and not current["dirty_paths"] and not current["conflicting"], "local worktree/head changed")
    bases = handoff._git(Path(worktree), "merge-base", "--all", pr.base_sha, pr.head_sha).decode().splitlines()
    require(len(bases) == 1 and candidate_identity(record) ==
            (pr.number, pr.head_sha, bases[0], pr.base_ref), "local candidate merge base changed")
    return current


def register_local_validation(
    state,
    record,
    pr,
    worktree,
    required_checks,
    *,
    review_qualification=None,
):
    """Declare coordinator-owned checks; this does not create an owner or handoff."""
    current = _coordinator_git(state, record, pr, worktree)
    clock = observations.clock_observation()
    local = {
        "coordinator_id": state["coordinator_id"], "repository": pr.repository, "pr_number": pr.number,
        "head_sha": pr.head_sha, "base_sha": record["base_sha"], "base_ref": pr.base_ref,
        "branch": pr.head_ref, "worktree": str(worktree), "git_identity": current["identity"],
        "registered_at": clock["at"], "clock": clock,
        "required_checks": copy.deepcopy(required_checks), "checks": {},
    }
    if review_qualification is not None:
        local["review_qualification"] = copy.deepcopy(review_qualification)
    validate_local_validation(local)
    record["local_validation"] = local
    return local


def _registered_local(state, record, pr):
    local = record.get("local_validation")
    require(local is not None, "coordinator local checks are not registered")
    validate_local_validation(local)
    require(candidate_identity(local) == candidate_identity(record)
            and (local["coordinator_id"], local["repository"], local["branch"]) ==
            (state["coordinator_id"], pr.repository, pr.head_ref), "registered local identity changed")
    require(not handoff.availability_errors({**state, "clock": local["clock"]}, observations.utc_now()),
            "registered local clock is stale")
    current = _coordinator_git(state, record, pr, local["worktree"])
    require(current["identity"] == local["git_identity"], "registered worktree identity changed")
    return local


def capture_local_check(state, record, pr, check_id, trusted_executor=None):
    local = _registered_local(state, record, pr)
    definitions = copy.deepcopy(local["required_checks"])
    require(check_id in definitions, "unknown registered local check")
    context = {"allowed_worktree": local["worktree"], "assigned_parent_sha": local["base_sha"],
               "required_checks": definitions, "repository": local["repository"],
               "pull_request": local["pr_number"], "head_sha": local["head_sha"],
               "git_identity": copy.deepcopy(local["git_identity"])}
    if "review_qualification" in local:
        context["review_qualification"] = copy.deepcopy(local["review_qualification"])
    entry = {"assignment": context, "checks": []}
    local["checks"].pop(check_id, None)
    started = observations.utc_now()
    try:
        check = handoff.capture_check(entry, check_id, pr.head_sha, trusted_executor, task_owned=False)
        require(_registered_local(state, record, pr) is local
                and local["required_checks"] == definitions, "registered check set changed during capture")
    except (OSError, RuntimeError, ValueError) as error:
        check = {"id": check_id, "evidence_id": definitions[check_id]["evidence_id"],
                 "contract": definitions[check_id]["contract"], "parent_sha": local["base_sha"],
                 "result_sha": pr.head_sha, "worktree": local["worktree"], "started_at": started,
                 "completed_at": None, "exit_code": None, "pid": None, "peak_rss_bytes": None,
                 "measurements": dict.fromkeys(handoff.METRICS),
                 "detail": "local capture unavailable: " + str(error)[:2000]}
        handoff.validate_check(check)
    local["checks"][check_id] = {"definitions": definitions, "observation": check}
    return check


def coordinator_local_ready(state, record, pr, review_qualification=None):
    try:
        local = _registered_local(state, record, pr)
        if "review_qualification" in local:
            if review_qualification is None:
                return False
            review_qualification.validate_binding(state, record, pr)
            if (
                review_qualification.record() != local["review_qualification"]
                or OWNERSHIP_CHECK_ID not in local["required_checks"]
                or local["required_checks"][OWNERSHIP_CHECK_ID]["contract"] != "coordinator-check"
                or local["required_checks"][OWNERSHIP_CHECK_ID]["evidence_id"]
                != review_qualification.evidence_id()
            ):
                return False
        elif review_qualification is not None:
            return False
        if set(local["checks"]) != set(local["required_checks"]):
            return False
        for check_id, definition in local["required_checks"].items():
            captured = local["checks"][check_id]
            check = captured["observation"]
            if (captured["definitions"] != local["required_checks"]
                    or any(check[key] != expected for key, expected in (
                        ("contract", definition["contract"]), ("evidence_id", definition["evidence_id"]),
                        ("parent_sha", local["base_sha"]), ("result_sha", local["head_sha"]),
                        ("worktree", local["worktree"])))
                    or check["exit_code"] != 0 or check["completed_at"] is None
                    or handoff.timestamp(check["started_at"]) < handoff.timestamp(local["registered_at"])
                    or handoff.timestamp(check["completed_at"]) > handoff.timestamp(observations.utc_now())
                    or (definition["contract"] != "protocol-json"
                        and (check["pid"] is None or check["peak_rss_bytes"] is None))):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _local_ready(state, pr, record, review_qualification=None):
    handoff.summarize_handoffs(state)
    require(find_candidate(state, candidate_identity(record)) == record, "unrecorded local candidate")
    delegated = _local_delegations(state, pr)
    if _delegation_incomplete(delegated) or record["abandoned_reason"] is not None:
        return False
    if "local_validation" in record:
        return coordinator_local_ready(state, record, pr, review_qualification)
    from scripts.validation_ownership.coordinator_capture import REVIEWED_EVIDENCE_PREFIX

    if review_qualification is not None or any(
        entry["assignment"]["required_checks"].get(OWNERSHIP_CHECK_ID, {}).get(
            "evidence_id", "",
        ).startswith(REVIEWED_EVIDENCE_PREFIX) for entry in delegated
    ):
        return False
    for entry in delegated:
        if (entry["validation"]["result_sha"] == pr.head_sha
                and entry["assignment"]["expected_branch"] == pr.head_ref):
            try:
                bases = handoff._git(Path(entry["assignment"]["allowed_worktree"]), "merge-base", "--all",
                                     pr.base_sha, pr.head_sha).decode().splitlines()
                if len(bases) == 1 and candidate_identity(record) == (
                        pr.number, pr.head_sha, bases[0], pr.base_ref):
                    return True
            except (OSError, ValueError):
                pass
    return False


def _reserved_dispatch(record, pr, run, runs):
    binding = run.candidate_binding
    workflows = {prior.workflow_id for prior in runs if prior.mode == "review-first"
                 and prior.head_sha == pr.head_sha and prior.head_branch == pr.head_ref
                 and prior.candidate_base_ref == pr.base_ref
                 and prior.run_number <= record["watermark"] and prior.candidate_binding == binding
                 and candidate_evidence.preflight_success({
                     job.name: (job.status, job.conclusion) for job in prior.jobs})}
    return (isinstance(binding, tuple) and len(binding) == 3
            and candidate_identity(record) == (*binding, pr.base_ref)
            and binding[:2] == (pr.number, pr.head_sha)
            and run.candidate_base_ref == pr.base_ref
            and len(workflows) == 1 and run.workflow_id in workflows
            and record["dispatch_requested_at"] is not None and run.event == "workflow_dispatch"
            and run.head_sha == pr.head_sha and run.head_branch == pr.head_ref
            and run.candidate_binding == (pr.number, pr.head_sha, record["base_sha"])
            and run.run_number > record["watermark"]
            and run.created_at >= reporter.parse_time(
                record["dispatch_requested_at"], "dispatch").replace(microsecond=0)
            and record["full_run_id"] in (None, run.run_id)
            and record["full_attempt"] in (None, run.run_attempt))


def _candidate_runs(state, record, pr, runs):
    """Historical ref witnesses are authoritative; missing witnesses stay unproven."""
    identity = candidate_identity(record)
    others = [item for item in state["candidates"] if candidate_identity(item)[:3] == identity[:3]
              and candidate_identity(item) != identity and item["full_run_id"] is not None]
    result = []
    for run in runs:
        if (run.head_sha != pr.head_sha or run.head_branch != pr.head_ref
                or run.candidate_binding not in (None, identity[:3])
                or run.candidate_base_ref not in (None, identity[3])):
            continue
        if run.candidate_base_ref is not None:
            require(not any((item["full_run_id"], item["full_attempt"]) ==
                            (run.run_id, run.run_attempt) for item in others),
                    "run witness contradicts recorded candidate ownership")
        result.append(run)
    return tuple(result)


def assess_candidate(state, record, decision, pr, session, facts, triage, checks, runs,
                     *, family_evidence=None, accepted_security=(), criteria_ready=False,
                     local_qualification=None):
    """Consume existing typed observations. Does not dispatch, merge or launch a watcher."""
    handoff.validate_state(state)
    require(type(criteria_ready) is bool, "objective/manual readiness must be an actual decision")
    require(find_candidate(state, candidate_identity(record)) == record, "unrecorded candidate")
    require((pr.repository, pr.number, session.head, session.identity) ==
            (state["repository"], record["pr_number"], record["head_sha"],
             (pr.repository, pr.number, record["base_sha"])), "candidate/review identity mismatch")
    require(candidate_identity(record) == (pr.number, pr.head_sha, session.identity[2], pr.base_ref)
            and decision.head_sha == pr.head_sha and decision.decision_oid == record["decision_oid"],
            "candidate head/base/decision changed")
    current_findings = tuple(item for item in session.accepted.values() if item.origin == pr.head_sha)
    require(all(item in checks for item in accepted_security), "stale accepted security finding")
    if current_findings or accepted_security:
        record["abandoned_reason"] = "accepted-review-or-security-finding"
    rebound = any(candidate_identity(item)[:2] == candidate_identity(record)[:2]
                  and candidate_identity(item) != candidate_identity(record)
                  for item in state["candidates"])
    missing = []
    scheduling = []
    if not decision.known or decision.control is None:
        scheduling.append("current-pilot-control")
    elif (decision.control.repository, decision.control.repository_id) != (pr.repository, pr.repository_id):
        scheduling.append("current-pilot-control")
    if "intake_control" in record and (
            record["intake_control"]["repository"], record["intake_control"]["repository_id"]) != (
                pr.repository, pr.repository_id):
        scheduling.append("unproven-initial-lane")
    if state.get("safety_publication") is not None:
        scheduling.append("safety-publication-pending")
    if handoff.availability_errors(state, observations.utc_now()):
        scheduling.append("coordinator-unavailable")
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
        scheduling.append("architecture-hold")
    if decision.pre_review_required:
        report, lease = session.report, session.lease
        ownership = session.owners.records.get(id(session)) if session.owners is not None else None
        if (report is None or lease is None or not lease.finished or lease.outcome != "completed"
                or not report.completed or not report.read_only or report.subjects != session.scope
                or report.owner in {session.coordinator, session.implementer}
                or (lease.task, lease.owner, lease.head) != (report.task, report.owner, report.head)
                or ownership is None or ownership[3]
                or (ownership[0][:2], ownership[1], ownership[2]) != (
                    session.identity[:2], report.head, report.subjects)
                or (facts and reporter.parse_time(report.completed_at, "pre-review completion") >=
                    min(reporter.parse_time(fact.submitted_at, "remote review") for fact in facts))):
            scheduling.append("unbound-original-review-context")
    if not _local_ready(state, pr, record, local_qualification):
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
    expected_binding = candidate_identity(record)[:3]
    current_runs = _candidate_runs(state, record, pr, runs)
    matching = [run for run in current_runs if run.candidate_binding == expected_binding
                and run.candidate_base_ref == record["base_ref"]
                and run.head_sha == pr.head_sha and run.head_branch == pr.head_ref]
    # A raw base-tip mismatch cannot classify an unmarked run as unrelated.
    unknown = [run for run in current_runs if run.head_sha == pr.head_sha and run.head_branch == pr.head_ref
               and run.status in github.ACTIVE_RUN_STATUSES
               and run.mode not in {"metadata-only", "active-metadata-only"}
               and run.candidate_binding in (None, expected_binding)
               and (run.candidate_binding is None or run.candidate_base_ref is None or run.mode == "active-unknown")]
    if unknown:
        scheduling.append("unclassified-active-run")
    visible = current_runs
    full = [run for run in current_runs if run.mode in {"full", "active-full"}]
    if any(run.head_sha != pr.head_sha for run in runs):
        scheduling.append("stale-run")
    if len({run.run_id for run in full}) != len(full) or len(full) > 1:
        scheduling.append("duplicate-full-run")
    preflight = any(run.mode == "review-first" and candidate_evidence.preflight_success(
        {job.name: (job.status, job.conclusion) for job in run.jobs}) for run in matching)
    try:
        _finalize_intake(record, pr, current_runs)
    except ValueError:
        scheduling.append("unproven-initial-lane")
    if record["mode"] == "review-first" and not preflight:
        scheduling.append("exact-preflight")
    if record["mode"] != decision.mode and record["mode"] != "concurrent" and not decision.paused:
        scheduling.append("gate-mode-changed")
    if record["abandoned_reason"] is not None:
        scheduling.append("abandoned-candidate")
    if not criteria_ready:
        missing.append("objective-or-manual-criteria")
    admitted = None
    if len(full) == 1:
        run = full[0]
        if record["full_attempt"] not in (None, run.run_attempt):
            missing.append("unbound-run-attempt")
        elif record["full_run_id"] not in (None, run.run_id):
            missing.append("wrong-full-run")
        elif record["mode"] == "review-first":
            # GitHub creation times have second precision; retain the native reservation.
            if (not _reserved_dispatch(record, pr, run, matching)
                    or (record["dispatch_sent_at"] is None and record.get("dispatch_observed_at") is None)):
                missing.append("early-or-unbound-full-run")
            else:
                record["full_run_id"], record["full_attempt"] = run.run_id, run.run_attempt
                admitted = run
        elif (run.event == "pull_request" and run.candidate_binding == expected_binding
              and run.candidate_base_ref == record["base_ref"]):
            record["full_run_id"], record["full_attempt"] = run.run_id, run.run_attempt
            admitted = run
        else:
            missing.append("unproven-full-run-ownership")
    complete = False
    if admitted is not None:
        try:
            github.require_full_success(admitted)
            complete = True
        except github.MetadataEditError:
            if admitted.status == "completed":
                missing.append("full-Build-" + str(admitted.conclusion))
    elif record["mode"] == "concurrent":
        missing.append("full-Build-missing")
    dispatchable = (not scheduling and record["mode"] == "review-first" and not full
                    and record["dispatch_requested_at"] is None and (decision.paused or not missing))
    missing.extend(scheduling)
    phase = ("superseded" if record["abandoned_reason"] == "superseded-head-or-base" else
             "review-abandoned" if record["abandoned_reason"] else
             "merge-ready" if complete and not missing else
             "build-failed" if admitted and admitted.status == "completed" and not complete else
             "building" if admitted else
             "review-first-preflight" if record["mode"] == "review-first" and not preflight else
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
                    family_evidence=None, accepted_security=(), criteria_ready=False,
                    local_qualification=None):
    """Refresh through #177/#179 and validate the unique actual Git merge base."""
    pr, lines = fetch_candidate(client, state["repository"], record["pr_number"])
    decision = fetch_decision(client, pr, lines)
    require(decision.known, "live decision authority is unavailable")
    request = {"candidate_sha": pr.head_sha, "base_sha": record["base_sha"]}
    review_tools.validate_base(request, pr.base_sha)
    identity, facts = _review_snapshot(client, pr, review_tools.model)
    require(identity == (pr.base_sha, pr.head_sha), "review/PR identity changed")
    checks = security_checks(client, pr)
    runs = github.list_candidate_runs(client, pr, include_dispatch=True)
    after, _ = fetch_candidate(client, pr.repository, pr.number)
    require((after.head_sha, after.head_ref, after.base_ref) ==
            (pr.head_sha, pr.head_ref, pr.base_ref), "candidate changed during assessment")
    review_tools.validate_base(request, after.base_sha)
    after_identity, after_facts = _review_snapshot(client, after, review_tools.model)
    if after_facts != facts:
        session.refresh_reviews(after_facts)
    require(after_identity == (after.base_sha, pr.head_sha) and after_facts == facts,
            "review evidence changed during assessment")
    require(security_checks(client, after) == checks, "security evidence changed during assessment")
    require(github.list_candidate_runs(client, after, include_dispatch=True) == runs,
            "Build evidence changed during assessment")
    control = fetch_pilot_control(client, after.repository, after.repository_id)
    require(decision.control is not None and control.identity == decision.control.identity,
            "pilot control changed during assessment")
    if "intake_control" not in record:
        begin_candidate(state, after, record["base_sha"], decision, runs=runs)
    assessment = assess_candidate(
        state, record, decision, after, session, facts, triage, checks, runs,
        family_evidence=family_evidence, accepted_security=accepted_security,
        criteria_ready=criteria_ready, local_qualification=local_qualification)
    return assessment, runs


def reserve_full_dispatch(state, record, assessment, runs):
    handoff.validate_state(state)
    require(state.get("safety_publication") is None
            and not handoff.availability_errors(state, observations.utc_now()),
            "coordinator coverage or safety publication holds scheduling")
    require(find_candidate(state, candidate_identity(record)) == record and assessment["record"] == record
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
        require(state["repository"] == pr.repository, "dispatch repository changed")
        identity = pr.number, pr.head_sha, frozen_base(client, pr), pr.base_ref
        record, assessment, runs = assess(state)
        require(find_candidate(state, identity) == record, "dispatch candidate identity changed")
        current = github.fetch_pull_request(client, pr.repository, pr.number)
        require((current.head_sha, current.head_ref, current.base_ref) ==
                (pr.head_sha, pr.head_ref, pr.base_ref),
                "dispatch branch identity changed")
        if current.base_sha != pr.base_sha:
            require(frozen_base(client, current) == identity[2], "dispatch frozen base changed")
        control = fetch_pilot_control(client, current.repository, current.repository_id)
        require(assessment["decision"].get("control") is not None
                and control.identity == PilotControl(**assessment["decision"]["control"]).identity,
                "pilot control changed before reservation")
        reservation = reserve_full_dispatch(state, record, assessment, runs)
    client.request("POST", github._endpoint(pr.repository, "actions/workflows/build.yml/dispatches"),
                   body={"ref": pr.head_ref}, label="one input-free full Build")
    with observations.locked_state(state_path) as state:
        require(state["repository"] == pr.repository, "dispatch repository changed")
        record = find_candidate(state, identity)
        require(record["dispatch_requested_at"] == reservation, "dispatch reservation changed")
        record["dispatch_sent_at"] = observations.utc_now()
    return {"state": "dispatch-observation-pending", "head_sha": pr.head_sha}


def reconcile_full_dispatch(client, state_path, pr):
    """Observe the unique reserved run; never repeat POST or invent its HTTP ack."""
    with observations.locked_state(state_path) as state:
        handoff.validate_state(state)
        require(state["repository"] == pr.repository, "reconciliation repository changed")
        identity = pr.number, pr.head_sha, frozen_base(client, pr), pr.base_ref
        record = find_candidate(state, identity)
        require(record["dispatch_requested_at"] is not None, "no reserved full dispatch")
        scoped = _candidate_runs(state, record, pr, github.list_candidate_runs(client, pr, include_dispatch=True))
        unknown = any(run.status in github.ACTIVE_RUN_STATUSES
                      and (run.candidate_binding is None or run.candidate_base_ref is None
                           or run.mode == "active-unknown") for run in scoped)
        full = [run for run in scoped if run.mode in {"full", "active-full"}]
        if unknown or len(full) != 1 or not _reserved_dispatch(record, pr, full[0], scoped):
            return {"state": "dispatch-uncertain", "head_sha": pr.head_sha}
        current = github.fetch_pull_request(client, pr.repository, pr.number)
        if ((current.head_sha, current.head_ref, current.base_ref) !=
                (pr.head_sha, pr.head_ref, pr.base_ref) or frozen_base(client, current) != record["base_sha"]):
            record["abandoned_reason"] = record["abandoned_reason"] or "superseded-head-or-base"
        run = full[0]
        record["full_run_id"], record["full_attempt"] = run.run_id, run.run_attempt
        record["dispatch_observed_at"] = observations.utc_now()
        return {"state": "observed-abandoned" if record["abandoned_reason"] else "dispatch-observed",
                "head_sha": pr.head_sha, "run_id": run.run_id, "attempt": run.run_attempt}


def cancel_abandoned(client, state_path, record, run):
    state = observations.load_json(state_path)
    handoff.validate_state(state)
    require(find_candidate(state, candidate_identity(record)) == record and record["abandoned_reason"] is not None,
            "cancellation requires recorded abandonment")
    require(run.head_sha == record["head_sha"] and run.candidate_binding ==
            candidate_identity(record)[:3]
            and run.candidate_base_ref == record["base_ref"]
            and record["full_run_id"] in (None, run.run_id)
            and record["full_attempt"] in (None, run.run_attempt),
            "cancellation would affect unrelated work")
    if sum(candidate_identity(item)[:3] == run.candidate_binding for item in state["candidates"]) > 1:
        require((record["full_run_id"], record["full_attempt"]) == (run.run_id, run.run_attempt),
                "cancellation base-ref ownership is ambiguous without its observed run")
    actual = observations.github_run(state["repository"], run.run_id, run.run_attempt, run.head_sha)
    require(actual["status"] != "completed" and actual["workflow_id"] == run.workflow_id,
            "run is terminal or belongs to another workflow")
    client.request("POST", github._endpoint(state["repository"], f"actions/runs/{run.run_id}/cancel"),
                   label="cancel abandoned full Build")
    return actual
