"""Deterministic combined coverage for unresolved FE8U locale targets."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Set

from .febuilder import validate_febuilder_evidence_document
from .mapping import MappingError, validate_mapping_document
from .structural_completion import validate_structural_completion_evidence

COMBINED_COVERAGE_SCHEMA_VERSION = 1
COMBINED_COVERAGE_KIND = "fe8u-fallback-integration-coverage"


class CombinedCoverageError(MappingError):
    """Raised when combined evidence cannot form a complete partition."""


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CombinedCoverageError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise CombinedCoverageError(f"{path}: root must be an object")
    return data


def _logical_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _input_record(path: Path, repo_root: Path) -> Dict[str, str]:
    return {
        "path": _logical_path(path, repo_root),
        "sha256": _sha256_path(path),
    }


def _target_ids_with_mark(
    evidence: Mapping[str, Any],
    mark: str,
) -> Set[str]:
    return {
        row["target_id"]
        for row in evidence["targets"]
        if mark in row["marks"]
    }


def build_combined_coverage_report(
    *,
    repo_root: Path,
    target_count: int,
    mapping_path: Path,
    coverage_path: Path,
    structural_crosswalk_path: Path,
    febuilder_path: Path,
    structural_completion_path: Path,
) -> Dict[str, Any]:
    """Build an evidence-only handoff report without modifying the target map."""

    repo_root = Path(repo_root)
    mapping_path = Path(mapping_path)
    coverage_path = Path(coverage_path)
    structural_crosswalk_path = Path(structural_crosswalk_path)
    febuilder_path = Path(febuilder_path)
    structural_completion_path = Path(structural_completion_path)

    mapping = _load_json(mapping_path)
    coverage = _load_json(coverage_path)
    febuilder = _load_json(febuilder_path)
    structural = _load_json(structural_completion_path)

    validate_mapping_document(
        mapping,
        target_count=target_count,
        repo_root=repo_root,
    )
    validate_febuilder_evidence_document(febuilder, target_count=target_count)
    validate_structural_completion_evidence(
        structural,
        repo_root=repo_root,
        target_count=target_count,
    )

    translated = {
        row["target_id"]
        for row in mapping["rows"]
        if row["source"]["kind"] != "english_fallback"
    }
    fallback_rows = [
        row
        for row in mapping["rows"]
        if row["source"]["kind"] == "english_fallback"
    ]
    actionable = {
        row["target_id"]
        for row in fallback_rows
        if row["source"]["reason"] == "not-yet-verified"
    }
    non_actionable_fallbacks = [
        {
            "reason": row["source"]["reason"],
            "target_id": row["target_id"],
        }
        for row in fallback_rows
        if row["target_id"] not in actionable
    ]

    if coverage.get("target_count") != target_count:
        raise CombinedCoverageError("coverage target_count differs from mapping")
    if coverage.get("unresolved_count") != 0:
        raise CombinedCoverageError("coverage contains unresolved targets")
    if coverage.get("translation_coverage", {}).get("count") != len(translated):
        raise CombinedCoverageError("coverage translated count differs from mapping")
    if coverage.get("explicit_fallback_coverage", {}).get("count") != len(
        fallback_rows
    ):
        raise CombinedCoverageError("coverage fallback count differs from mapping")

    febuilder_unique_global = _target_ids_with_mark(
        febuilder, "unique-uncontested"
    )
    febuilder_agree_global = _target_ids_with_mark(
        febuilder, "agrees-with-structural"
    )
    febuilder_conflict_global = _target_ids_with_mark(febuilder, "conflicts")
    febuilder_collision_global = _target_ids_with_mark(
        febuilder, "collision-needs-context"
    )

    febuilder_unique = febuilder_unique_global & actionable
    febuilder_agree = febuilder_agree_global & actionable
    febuilder_conflict = febuilder_conflict_global & actionable
    febuilder_collision = febuilder_collision_global & actionable

    structural_high_global = {
        row["target_id"]
        for row in structural["proposals"]
        if row["confidence"] == "high"
    }
    structural_reference_global = {
        row["target_id"]
        for row in structural["proposals"]
        if row["confidence"] == "reference"
    }
    structural_high = structural_high_global & actionable
    structural_reference = structural_reference_global & actionable
    structural_proposals = structural_high | structural_reference
    structural_collisions_global = {
        row["target_id"] for row in structural["collisions"]
    }
    structural_collisions = structural_collisions_global & actionable
    structural_residuals_global = {
        row["target_id"] for row in structural["residual_targets"]
    }
    structural_residuals = structural_residuals_global & actionable
    structural_research_targets = (
        structural_high_global
        | structural_reference_global
        | structural_residuals_global
    )

    if (
        structural_high_global | structural_reference_global
    ) & structural_residuals_global:
        raise CombinedCoverageError(
            "structural proposals and residuals are not disjoint"
        )
    if not actionable <= structural_research_targets:
        raise CombinedCoverageError(
            "current not-yet-verified targets are absent from structural research"
        )
    if structural_proposals | structural_residuals != actionable:
        raise CombinedCoverageError(
            "structural evidence does not partition not-yet-verified targets"
        )
    if not structural_collisions <= structural_residuals:
        raise CombinedCoverageError(
            "structural context collisions are missing from residuals"
        )

    combined_candidates = febuilder_unique | structural_proposals
    blocked = (
        febuilder_conflict | febuilder_collision | structural_collisions
    )
    unblocked_candidates = combined_candidates - blocked
    candidate_and_blocked = combined_candidates & blocked
    residual = actionable - combined_candidates - blocked

    if unblocked_candidates | blocked | residual != actionable:
        raise CombinedCoverageError(
            "combined candidate, blocked, and residual sets do not partition targets"
        )
    if (unblocked_candidates & blocked) or (unblocked_candidates & residual):
        raise CombinedCoverageError("combined partition sets overlap")
    if blocked & residual:
        raise CombinedCoverageError("blocked and residual sets overlap")

    structural_by_target = {
        row["target_id"]: row for row in structural["proposals"]
    }
    febuilder_by_target = {
        row["target_id"]: row for row in febuilder["targets"]
    }
    blocked_targets = []
    for target_id in sorted(blocked):
        blockers = []
        if target_id in febuilder_conflict:
            blockers.append("febuilder-conflict")
        if target_id in febuilder_collision:
            blockers.append("febuilder-collision-needs-context")
        if target_id in structural_collisions:
            blockers.append("structural-context-required")

        candidate_sources = []
        if target_id in febuilder_unique:
            candidate_sources.append("febuilder-unique-uncontested")
        if target_id in structural_high:
            candidate_sources.append("structural-high")
        if target_id in structural_reference:
            candidate_sources.append("structural-reference")

        febuilder_row = febuilder_by_target.get(target_id)
        structural_row = structural_by_target.get(target_id)
        blocked_targets.append(
            {
                "blockers": blockers,
                "candidate_sources": candidate_sources,
                "febuilder_marks": (
                    febuilder_row["marks"] if febuilder_row is not None else []
                ),
                "structural_confidence": (
                    structural_row["confidence"]
                    if structural_row is not None
                    else None
                ),
                "target_id": target_id,
            }
        )

    fallback_reason_counts = Counter(
        row["source"]["reason"] for row in fallback_rows
    )
    report = {
        "authoritative": False,
        "authority": "evidence-only",
        "blocked_targets": blocked_targets,
        "candidate_targets": {
            "combined_unblocked": sorted(unblocked_candidates),
            "febuilder_unique_uncontested": sorted(febuilder_unique),
            "structural_high": sorted(structural_high),
            "structural_reference": sorted(structural_reference),
        },
        "global_exceptions": {
            "febuilder_collision_targets": sorted(
                febuilder_collision_global
            ),
            "febuilder_conflict_targets": sorted(febuilder_conflict_global),
            "structural_collision_targets": sorted(
                structural_collisions_global
            ),
        },
        "inputs": {
            "febuilder_alignment_evidence": _input_record(
                febuilder_path, repo_root
            ),
            "structural_completion_evidence": _input_record(
                structural_completion_path, repo_root
            ),
            "structural_crosswalk_evidence": _input_record(
                structural_crosswalk_path, repo_root
            ),
            "target_map": _input_record(mapping_path, repo_root),
            "target_map_coverage": _input_record(coverage_path, repo_root),
        },
        "intersections": {
            "candidate_and_blocked_count": len(candidate_and_blocked),
            "candidate_and_blocked_targets": sorted(candidate_and_blocked),
            "febuilder_and_structural_candidate_count": len(
                febuilder_unique & structural_proposals
            ),
            "febuilder_only_candidate_count": len(
                febuilder_unique - structural_proposals
            ),
            "febuilder_structural_collision_overlap_count": len(
                febuilder_collision & structural_collisions
            ),
            "structural_only_candidate_count": len(
                structural_proposals - febuilder_unique
            ),
        },
        "kind": COMBINED_COVERAGE_KIND,
        "non_actionable_fallbacks": non_actionable_fallbacks,
        "note": (
            "Generated handoff summary for current not-yet-verified targets. "
            "It preserves candidate conflicts and collisions and cannot promote "
            "rows into the authoritative target map."
        ),
        "policy": {
            "conflicts_and_collisions_preserved": True,
            "promotion_permitted": False,
            "updates_authoritative_map": False,
        },
        "residual_targets": sorted(residual),
        "schema_version": COMBINED_COVERAGE_SCHEMA_VERSION,
        "summary": {
            "actionable_not_yet_verified_target_count": len(actionable),
            "combined_blocked_target_count": len(blocked),
            "combined_candidate_target_count": len(combined_candidates),
            "combined_unblocked_candidate_target_count": len(
                unblocked_candidates
            ),
            "explicit_fallback_target_count": len(fallback_rows),
            "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
            "febuilder_actionable_agrees_with_structural_count": len(
                febuilder_agree
            ),
            "febuilder_actionable_collision_count": len(
                febuilder_collision
            ),
            "febuilder_actionable_conflict_count": len(febuilder_conflict),
            "febuilder_global_collision_count": len(
                febuilder_collision_global
            ),
            "febuilder_global_conflict_count": len(febuilder_conflict_global),
            "febuilder_unique_uncontested_candidate_count": len(
                febuilder_unique
            ),
            "non_actionable_fallback_target_count": len(
                non_actionable_fallbacks
            ),
            "residual_target_count": len(residual),
            "structural_context_collision_count": len(
                structural_collisions
            ),
            "structural_global_context_collision_count": len(
                structural_collisions_global
            ),
            "structural_high_candidate_count": len(structural_high),
            "structural_reference_candidate_count": len(
                structural_reference
            ),
            "target_count": target_count,
            "translated_target_count": len(translated),
        },
    }
    return report
