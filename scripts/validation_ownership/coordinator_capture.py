"""The ownership verifier's existing #178 coordinator-check integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from scripts.workflow_pilot import agent_handoff, raw_diff_check
from .authority import parse_json
from .budget import MakeProbeError


CHECK_ID = "validation-ownership"


@dataclass(frozen=True)
class VerifierExpectation:
    repository_root: Path
    trusted_root: Path
    base_sha: str
    candidate_sha: str
    trusted_sha: str
    mode: str

    def validate(self):
        for value in (self.base_sha, self.candidate_sha, self.trusted_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise MakeProbeError("verifier expectation requires exact SHA identities")
        if self.mode not in {"exact-base-pinned", "foundation-introduction"}:
            raise MakeProbeError("non-authoritative ownership result mode is not admissible")
        if self.mode == "exact-base-pinned" and self.trusted_sha != self.base_sha:
            raise MakeProbeError("exact-base source expectation differs from BASE")
        if self.repository_root.resolve() == self.trusted_root.resolve():
            raise MakeProbeError("coordinator verifier source must be separate from candidate data")


def capture(entry, expectation: VerifierExpectation):
    """Coordinator supplies the definition/source/BASE expectations, not candidate YAML."""
    expectation.validate()
    assignment = entry["assignment"]
    definition = assignment["required_checks"].get(CHECK_ID)
    if (
        definition is None or definition["contract"] != "coordinator-check"
        or Path(assignment["allowed_worktree"]).resolve() != expectation.repository_root.resolve()
    ):
        raise MakeProbeError("managed ownership admission lacks its coordinator check assignment")

    def execute(current_assignment, result_sha):
        if current_assignment is not assignment or result_sha != expectation.candidate_sha:
            raise MakeProbeError("ownership capture changed its head/assignment binding")
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
        actual = raw_diff_check.run_process(
            argv, cwd=expectation.trusted_root, env=raw_diff_check.git_environment(),
            timeout=min(3600, assignment["max_lifetime_seconds"]),
            max_bytes=raw_diff_check.MAX_BYTES,
        )
        if actual.returncode == 0:
            result = parse_json(actual.stdout, "captured ownership verifier result")
            expected = {
                "base_sha": expectation.base_sha, "candidate_sha": expectation.candidate_sha,
                "trusted_sha": expectation.trusted_sha, "mode": expectation.mode,
                "authority": "exact-base" if expectation.mode == "exact-base-pinned" else "explicit-introduction",
            }
            if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
                raise MakeProbeError("captured verifier result differs from coordinator expectation")
            if any(type(result.get(name)) is not int or result[name] < 1 for name in (
                "coverage_paths", "evidence_authorities", "trusted_package_files",
            )):
                raise MakeProbeError("captured ownership verification is incomplete")
        return actual, dict.fromkeys(agent_handoff.METRICS)

    return agent_handoff.capture_check(
        entry, CHECK_ID, expectation.candidate_sha, trusted_executor=execute,
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
