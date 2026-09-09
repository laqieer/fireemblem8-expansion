"""The ownership verifier's existing #178 coordinator-check integration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    reviewed_repository: str | None = None
    reviewed_pull_request: int | None = None
    reviewed_paths: tuple[str, ...] = ()
    reviewed_edge_ids: tuple[str, ...] = ()

    def reviewed_evolution(self):
        return {
            "repository": self.reviewed_repository,
            "pull_request": self.reviewed_pull_request,
            "changed_paths": list(self.reviewed_paths),
            "changed_edge_ids": list(self.reviewed_edge_ids),
        }

    def evidence_id(self):
        if self.mode != "reviewed-evolution":
            return "ownership"
        payload = {
            "mode": self.mode,
            "repository": self.reviewed_repository,
            "pull_request": self.reviewed_pull_request,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "trusted_sha": self.trusted_sha,
            "changed_paths": list(self.reviewed_paths),
            "changed_edge_ids": list(self.reviewed_edge_ids),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return "ownership-reviewed-" + digest

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
            if self.trusted_sha != self.candidate_sha:
                raise MakeProbeError("reviewed evolution requires exact candidate verifier source")
            reviewed = self.reviewed_evolution()
            if (
                not isinstance(self.reviewed_repository, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.reviewed_repository)
                or type(self.reviewed_pull_request) is not int
                or self.reviewed_pull_request < 1
                or not self.reviewed_paths
                or tuple(sorted(set(self.reviewed_paths))) != self.reviewed_paths
                or any(not isinstance(path, str) or not path for path in self.reviewed_paths)
                or not self.reviewed_edge_ids
                or tuple(sorted(set(self.reviewed_edge_ids))) != self.reviewed_edge_ids
                or any(not isinstance(edge_id, str) or not edge_id for edge_id in self.reviewed_edge_ids)
            ):
                raise MakeProbeError("reviewed evolution expectation requires exact repository, review scope and edge scope")
        elif any(
            value not in {None, ()}
            for value in (
                self.reviewed_repository,
                self.reviewed_pull_request,
                self.reviewed_paths,
                self.reviewed_edge_ids,
            )
        ):
            raise MakeProbeError("non-reviewed ownership expectations cannot declare reviewed evolution scope")
        if self.repository_root.resolve() == self.trusted_root.resolve():
            raise MakeProbeError("coordinator verifier source must be separate from candidate data")


def trusted_executor(expectation: VerifierExpectation):
    expectation.validate()
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
        if expectation.mode == "reviewed-evolution":
            argv.extend(
                [
                    "--reviewed-repository",
                    expectation.reviewed_repository,
                    "--reviewed-pull-request",
                    str(expectation.reviewed_pull_request),
                    *(
                        item
                        for path in expectation.reviewed_paths
                        for item in ("--reviewed-path", path)
                    ),
                    *(
                        item
                        for edge_id in expectation.reviewed_edge_ids
                        for item in ("--reviewed-edge", edge_id)
                    ),
                ]
            )
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
            if any(type(result.get(name)) is not int or result[name] < 1 for name in (
                "coverage_paths", "evidence_authorities", "trusted_package_files",
            )):
                raise MakeProbeError("captured ownership verification is incomplete")
            if expectation.mode == "reviewed-evolution":
                if (
                    result.get("reviewed_evolution") != expectation.reviewed_evolution()
                    or result.get("review_invalidation", {}).get("invalidated") is not True
                    or result.get("review_invalidation", {}).get("changed_edge_ids")
                    != list(expectation.reviewed_edge_ids)
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
