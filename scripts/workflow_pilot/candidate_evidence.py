"""Evaluate candidate Build evidence from authoritative dynamic check contexts."""

from __future__ import annotations

from dataclasses import dataclass


FULL_CONTEXTS = (
    "event-classifier",
    "host-tests",
    "build",
    "extended-host-tests",
    "legacy",
    "summary",
)
METADATA_CONTEXTS = (
    "metadata-classifier",
    "metadata-host-tests-skipped",
    "metadata-build-skipped",
    "metadata-extended-host-tests-skipped",
    "metadata-legacy-skipped",
    "metadata-summary",
)
KNOWN_CONTEXTS = frozenset(FULL_CONTEXTS) | frozenset(METADATA_CONTEXTS) | {
    "event-router",
    "patch-release",
}


class CandidateEvidenceError(ValueError):
    """A workflow-run record cannot prove candidate eligibility."""


@dataclass(frozen=True)
class CandidateEvidence:
    eligible: bool
    mode: str
    reason: str
    run_id: int | None


def _contexts(run: dict) -> dict[str, str]:
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
    for field in ("base_sha", "head_sha"):
        value = run[field]
        if (
            not isinstance(value, str)
            or len(value) != 40
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise CandidateEvidenceError(f"{field} must be a full lowercase SHA")
    if not isinstance(run["contexts"], list):
        raise CandidateEvidenceError("contexts must be a list")

    contexts = {}
    for index, raw in enumerate(run["contexts"]):
        if not isinstance(raw, dict) or set(raw) != {"conclusion", "name"}:
            raise CandidateEvidenceError(f"contexts[{index}] has invalid fields")
        name = raw["name"]
        conclusion = raw["conclusion"]
        if name not in KNOWN_CONTEXTS:
            raise CandidateEvidenceError(f"unknown Build check context {name!r}")
        if name in contexts:
            raise CandidateEvidenceError(f"duplicate Build check context {name!r}")
        if conclusion not in {"failure", "skipped", "success"}:
            raise CandidateEvidenceError(
                f"invalid Build check conclusion {conclusion!r}"
            )
        contexts[name] = conclusion
    return contexts


def run_mode(run: dict) -> str:
    contexts = _contexts(run)
    has_full = any(name in contexts for name in FULL_CONTEXTS)
    has_metadata = any(name in contexts for name in METADATA_CONTEXTS)
    if has_full and has_metadata:
        raise CandidateEvidenceError("run mixes full and metadata check contexts")
    if has_metadata:
        return "metadata-only"
    if has_full:
        return "full"
    raise CandidateEvidenceError("run has no authoritative Build mode context")


def evaluate_candidate_runs(
    runs: list[dict],
    *,
    head_sha: str,
    base_sha: str,
) -> CandidateEvidence:
    matching = []
    seen_ids = set()
    for run in runs:
        contexts = _contexts(run)
        run_id = run["run_id"]
        if run_id in seen_ids:
            raise CandidateEvidenceError(f"duplicate workflow run {run_id}")
        seen_ids.add(run_id)
        mode = run_mode(run)
        if run["head_sha"] != head_sha or run["base_sha"] != base_sha:
            continue
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
    missing = [name for name in FULL_CONTEXTS if name not in contexts]
    failed = [
        name
        for name in FULL_CONTEXTS
        if contexts.get(name) != "success"
    ]
    if missing or failed:
        return CandidateEvidence(
            False,
            mode,
            "latest-full-run-is-not-successful",
            run_id,
        )
    return CandidateEvidence(True, mode, "latest-full-run-is-successful", run_id)


def latest_contexts(runs: list[dict]) -> dict[str, tuple[int, str]]:
    latest: dict[str, tuple[int, str]] = {}
    for run in sorted(runs, key=lambda item: item.get("run_id", 0)):
        contexts = _contexts(run)
        for name, conclusion in contexts.items():
            latest[name] = (run["run_id"], conclusion)
    return latest
