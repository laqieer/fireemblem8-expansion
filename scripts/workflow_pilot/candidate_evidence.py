"""Evaluate candidates from common event identity and running attestations."""

from __future__ import annotations

from dataclasses import dataclass


WORKER_JOB_IDS = ("host-tests", "build", "extended-host-tests", "legacy")
KNOWN_JOB_IDS = frozenset(WORKER_JOB_IDS) | {
    "event-identity",
    "event-router",
    "event-classifier",
    "patch-release",
    "summary",
}
FULL_CLASSIFIER = "event-classifier"
FULL_ATTESTATION = "summary"
METADATA_CLASSIFIER = "metadata-classifier"
METADATA_ATTESTATION = "metadata-summary"


class CandidateEvidenceError(ValueError):
    """A workflow-run record cannot prove candidate eligibility."""


@dataclass(frozen=True)
class CandidateEvidence:
    eligible: bool
    mode: str
    reason: str
    run_id: int | None


def _validate_sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CandidateEvidenceError(f"{field} must be a full lowercase SHA")
    return value


def _contexts(run: dict) -> dict[str, tuple[str, str]]:
    if set(run) != {"base_sha", "contexts", "event", "head_sha", "run_id"}:
        raise CandidateEvidenceError("run record has unknown or missing fields")
    if (
        not isinstance(run["run_id"], int)
        or isinstance(run["run_id"], bool)
        or run["run_id"] < 1
    ):
        raise CandidateEvidenceError("run_id must be a positive integer")
    if run["event"] != "pull_request":
        raise CandidateEvidenceError("candidate evidence must be a pull_request run")
    _validate_sha(run["base_sha"], "base_sha")
    _validate_sha(run["head_sha"], "head_sha")
    if not isinstance(run["contexts"], list):
        raise CandidateEvidenceError("contexts must be a list")

    contexts = {}
    for index, raw in enumerate(run["contexts"]):
        if (
            not isinstance(raw, dict)
            or set(raw) != {"conclusion", "job_id", "name"}
        ):
            raise CandidateEvidenceError(f"contexts[{index}] has invalid fields")
        job_id = raw["job_id"]
        name = raw["name"]
        conclusion = raw["conclusion"]
        if job_id not in KNOWN_JOB_IDS:
            raise CandidateEvidenceError(f"unknown Build job identity {job_id!r}")
        if job_id in contexts:
            raise CandidateEvidenceError(f"duplicate Build job identity {job_id!r}")
        if not isinstance(name, str) or not name:
            raise CandidateEvidenceError(f"contexts[{index}].name must be nonempty")
        if conclusion not in {"failure", "skipped", "success"}:
            raise CandidateEvidenceError(
                f"invalid Build check conclusion {conclusion!r}"
            )
        contexts[job_id] = (name, conclusion)
    return contexts


def _mode(contexts: dict[str, tuple[str, str]]) -> str:
    classifier = contexts.get("event-classifier")
    summary = contexts.get("summary")
    if classifier is None or summary is None:
        raise CandidateEvidenceError(
            "run lacks running classifier or summary attestation"
        )
    pair = (classifier[0], summary[0])
    if pair == (FULL_CLASSIFIER, FULL_ATTESTATION):
        return "full"
    if pair == (METADATA_CLASSIFIER, METADATA_ATTESTATION):
        return "metadata-only"
    raise CandidateEvidenceError("classifier and summary attest different modes")


def _validate_mode_contexts(
    contexts: dict[str, tuple[str, str]],
    mode: str,
) -> None:
    if contexts.get("event-identity") != ("event-identity", "success"):
        raise CandidateEvidenceError(
            "run lacks successful canonical event-identity setup"
        )
    if contexts.get("event-router") != ("event-router", "success"):
        raise CandidateEvidenceError(
            "run lacks successful canonical event-router setup"
        )
    if contexts.get("patch-release") != ("patch-release", "skipped"):
        raise CandidateEvidenceError(
            "run lacks canonical skipped patch-release context"
        )
    if mode == "metadata-only":
        for job_id in WORKER_JOB_IDS:
            if job_id not in contexts:
                continue
            _, conclusion = contexts[job_id]
            if conclusion not in {"skipped", "success"}:
                raise CandidateEvidenceError(
                    f"metadata worker {job_id!r} has invalid conclusion"
                )
        return

    for job_id in WORKER_JOB_IDS:
        context = contexts.get(job_id)
        if context is not None and context[0] != job_id:
            raise CandidateEvidenceError(
                f"full worker {job_id!r} has noncanonical check name"
            )


def run_mode(run: dict) -> str:
    contexts = _contexts(run)
    mode = _mode(contexts)
    _validate_mode_contexts(contexts, mode)
    return mode


def evaluate_candidate_runs(
    runs: list[dict],
    *,
    head_sha: str,
    base_sha: str,
) -> CandidateEvidence:
    _validate_sha(head_sha, "requested head_sha")
    _validate_sha(base_sha, "requested base_sha")
    if not isinstance(runs, list):
        raise CandidateEvidenceError("runs must be a list")

    matching = []
    seen_ids = set()
    for run in runs:
        contexts = _contexts(run)
        run_id = run["run_id"]
        if run_id in seen_ids:
            raise CandidateEvidenceError(f"duplicate workflow run {run_id}")
        seen_ids.add(run_id)
        mode = _mode(contexts)
        _validate_mode_contexts(contexts, mode)
        if run["head_sha"] == head_sha and run["base_sha"] == base_sha:
            matching.append((run_id, mode, contexts))

    if not matching:
        return CandidateEvidence(False, "missing", "no-exact-candidate-run", None)
    full_runs = [record for record in matching if record[1] == "full"]
    if not full_runs:
        latest = max(matching, key=lambda record: record[0])
        return CandidateEvidence(
            False,
            latest[1],
            "metadata-only-run-is-not-candidate-evidence",
            latest[0],
        )

    run_id, mode, contexts = max(full_runs, key=lambda record: record[0])
    classifier = contexts["event-classifier"]
    summary = contexts["summary"]
    workers = [contexts.get(job_id) for job_id in WORKER_JOB_IDS]
    if (
        classifier != (FULL_CLASSIFIER, "success")
        or summary != (FULL_ATTESTATION, "success")
        or any(
            context != (job_id, "success")
            for job_id, context in zip(WORKER_JOB_IDS, workers)
        )
    ):
        return CandidateEvidence(
            False,
            mode,
            "latest-full-run-is-not-successful",
            run_id,
        )
    return CandidateEvidence(True, mode, "latest-full-run-is-successful", run_id)


def latest_contexts(runs: list[dict]) -> dict[str, tuple[int, str]]:
    validated = []
    for run in runs:
        contexts = _contexts(run)
        validated.append((run["run_id"], contexts))
    latest: dict[str, tuple[int, str]] = {}
    for run_id, contexts in sorted(validated):
        for name, conclusion in contexts.values():
            latest[name] = (run_id, conclusion)
    return latest
