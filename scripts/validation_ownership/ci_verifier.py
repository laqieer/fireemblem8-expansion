#!/usr/bin/env python3
"""Validate candidate ownership with verifier code pinned to the exact PR base."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


if __name__ == "__main__" and (not sys.flags.isolated or not sys.flags.no_site):
    raise SystemExit("validation-ownership-base-verifier: isolated no-site startup (-I -S) is required")

TRUSTED_ROOT = Path(__file__).resolve().parents[2]
if str(TRUSTED_ROOT) not in sys.path:
    sys.path.insert(0, str(TRUSTED_ROOT))

from scripts.validation_ownership import isolated_launcher

if __name__ == "__main__":
    isolated_launcher._clear_ambient_execution_environment()

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, git_command
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.graph_report import capture, inventory
from scripts.validation_ownership.graph_commands import ROOT_RUNTIME_FILES
from scripts.validation_ownership.make_probe import ProbeSession


TRUSTED_PREFIX = "scripts/validation_ownership/"
BASE_STEP_MARKER = "    - name: Validate ownership with exact PR-base verifier\n"
TRUSTED_RUNTIME_PATHS = frozenset(
    {
        f"{TRUSTED_PREFIX}ci_gate.mk",
        f"{TRUSTED_PREFIX}ci_verifier.py",
        f"{TRUSTED_PREFIX}generated_registry_probe.py",
        f"{TRUSTED_PREFIX}graph.schema.json",
        f"{TRUSTED_PREFIX}isolated_launcher.py",
        f"{TRUSTED_PREFIX}make_probe.py",
        f"{TRUSTED_PREFIX}reporter.py",
        f"{TRUSTED_PREFIX}sandbox_exec.py",
        f"{TRUSTED_PREFIX}shell_interceptor.c",
        *(f"{TRUSTED_PREFIX}{name}" for name in (
            "authority.py", "budget.py", "lifecycle.py", "syscall_guard.py",
            "make_observer.c", "dispatch.h", "graph_commands.py", "graph_registry.py",
            "graph_probe.py", "graph_report.py", "graph_lifecycle.py", "scaninc_sources.cpp",
            "coordinator_capture.py",
        )),
    }
)
BASE_AUTHORITY_PATHS = frozenset(
    {
        *TRUSTED_RUNTIME_PATHS,
        reporter.GRAPH_PATH.as_posix(),
        reporter.MAKE_DYNAMIC_PATH.as_posix(),
        reporter.PROBE_ORACLE_PATH.as_posix(),
    }
)


def _git(root: Path, *arguments: str, budget: ProbeBudget) -> subprocess.CompletedProcess[bytes]:
    return budget.run([*git_command(root), *arguments], env=ENVIRONMENT,
                      output_limit=budget.limits.file_bytes)


def _exact_commit(root: Path, value: str, label: str, *, budget: ProbeBudget) -> str:
    completed = _git(root, "rev-parse", "--verify", f"{value}^{{commit}}", budget=budget)
    if completed.returncode != 0:
        raise reporter.OwnershipError(f"{label} is not an available commit")
    resolved = completed.stdout.decode("ascii").strip()
    if resolved != value:
        raise reporter.OwnershipError(
            f"{label} must be an exact full commit SHA"
        )
    return resolved


def _prepare_trusted_runtime_root(trusted_root: Path) -> Path:
    if not hasattr(os, "O_NOFOLLOW"):
        raise reporter.OwnershipError(
            "trusted verifier runtime root requires O_NOFOLLOW"
        )
    runtime_root = trusted_root / ".validation-ownership-runtime"
    try:
        os.mkdir(runtime_root, mode=0o700)
        entry_stat = os.lstat(runtime_root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(runtime_root, flags)
    except OSError as error:
        raise reporter.OwnershipError(
            f"cannot create trusted verifier runtime root: {error}"
        ) from error
    try:
        opened_stat = os.fstat(descriptor)
        if (
            opened_stat.st_dev != entry_stat.st_dev
            or opened_stat.st_ino != entry_stat.st_ino
            or opened_stat.st_uid != os.getuid()
            or opened_stat.st_mode & 0o777 != 0o700
        ):
            raise reporter.OwnershipError(
                "trusted verifier runtime root identity is invalid"
            )
    finally:
        os.close(descriptor)
    return runtime_root


def _base_authority_mode(
    base_entries: dict[str, reporter.GitTreeEntry],
) -> str:
    has_validation_package = any(
        path.startswith(TRUSTED_PREFIX)
        for path in base_entries
    )
    present_authority = BASE_AUTHORITY_PATHS & set(base_entries)
    if not has_validation_package and not present_authority:
        return "bootstrap-not-authoritative"
    graph_markers = {
        reporter.GRAPH_PATH.as_posix(), reporter.SCHEMA_PATH.as_posix(),
        reporter.MAKE_DYNAMIC_PATH.as_posix(), reporter.PROBE_ORACLE_PATH.as_posix(),
        f"{TRUSTED_PREFIX}reporter.py", f"{TRUSTED_PREFIX}ci_verifier.py",
    }
    foundation = {
        f"{TRUSTED_PREFIX}{name}" for name in (
            "authority.py", "budget.py", "make_probe.py", "syscall_guard.py",
            "sandbox_exec.py", "shell_interceptor.c", "make_observer.c", "lifecycle.py",
        )
    }
    if not graph_markers & set(base_entries) and foundation <= set(base_entries):
        return "foundation-introduction"
    missing = sorted(BASE_AUTHORITY_PATHS - set(base_entries))
    if missing:
        raise reporter.OwnershipError(
            f"exact base has incomplete validation authority: {missing}"
        )
    return "exact-base-pinned"


def _trusted_paths(
    trusted_root: Path,
    base_loader: reporter.AuthorityLoader,
) -> set[str]:
    paths = {
        path
        for path, entry in base_loader.entries.items()
        if path.startswith(TRUSTED_PREFIX)
        and entry.object_type == "blob"
        and entry.mode in {"100644", "100755"}
    }
    if not TRUSTED_RUNTIME_PATHS <= paths:
        raise reporter.OwnershipError(
            "exact base lacks the complete validation ownership verifier"
        )
    for path in sorted(paths):
        target = trusted_root / path
        if not target.is_file() or target.is_symlink():
            raise reporter.OwnershipError(
                f"trusted verifier path {path!r} is missing or not regular"
            )
        if base_loader.budget.read_bytes(target, "control") != base_loader.read_blob(
            path,
            "trusted verifier identity",
        ):
            raise reporter.OwnershipError(
                f"trusted verifier path {path!r} differs from the exact base"
            )
    actual = {
        item.relative_to(trusted_root).as_posix()
        for item in (trusted_root / TRUSTED_PREFIX).rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
    }
    if actual != paths:
        raise reporter.OwnershipError(
            "trusted verifier package has missing or extra files"
        )
    return paths


def _verify_loaded_modules(
    trusted_root: Path,
    base_loader: reporter.AuthorityLoader,
) -> list[str]:
    result = []
    for name, module in sorted(sys.modules.items()):
        if not name.startswith("scripts"):
            continue
        module_path = getattr(module, "__file__", None)
        if module_path is None:
            continue
        path = Path(module_path).resolve(strict=True)
        try:
            relative = path.relative_to(trusted_root).as_posix()
        except ValueError as error:
            raise reporter.OwnershipError(
                f"trusted module {name!r} loaded outside the exact base"
            ) from error
        if path.suffix != ".py" or relative not in base_loader.entries:
            raise reporter.OwnershipError(
                f"trusted module {name!r} lacks base source authority"
            )
        if base_loader.budget.read_bytes(path, "control") != base_loader.read_blob(
            relative,
            "trusted module identity",
        ):
            raise reporter.OwnershipError(
                f"trusted module {name!r} differs from the exact base"
            )
        result.append(relative)
    return result


def _base_step(text: str) -> str:
    start = text.find(BASE_STEP_MARKER)
    if start < 0:
        raise reporter.OwnershipError(
            "Build workflow lacks the exact PR-base verifier step"
        )
    end = text.find("\n    - name:", start + len(BASE_STEP_MARKER))
    return text[start:] if end < 0 else text[start:end + 1]


def _verify_base_step(
    loader: AuthorityLoader,
    base_loader: reporter.AuthorityLoader,
) -> None:
    path = reporter.BUILD_WORKFLOW_PATH
    candidate = loader.read_blob(
        path,
        "candidate Build workflow",
    ).decode("utf-8")
    base = base_loader.read_blob(
        path,
        "base Build workflow",
    ).decode("utf-8")
    if _base_step(candidate) != _base_step(base):
        raise reporter.OwnershipError(
            "candidate changed the exact PR-base verifier staging step"
        )


def _verify_oracle_pairs(
    oracle: dict[str, Any],
    graph: dict[str, Any],
    model: dict[str, Any],
    base_graph: dict[str, Any],
    base_model: dict[str, Any],
) -> tuple[str, str]:
    expected = []
    for probe in oracle["probes"]:
        if "expected_exclusion" in probe:
            expected.append(
                {
                    "path": probe["path"],
                    "exclusion": probe["expected_exclusion"],
                }
            )
            continue
        expected.append(
            {
                "path": probe["path"],
                "surface": probe["expected_surface"],
                "owners": sorted(
                    probe["expected_owners"],
                    key=lambda item: (
                        item["edge_type"],
                        item["evidence_id"],
                    ),
                ),
            }
        )
    expected_bytes = reporter.normalized_json(expected)
    measurements = (
        ("exact base", reporter._measure(oracle, base_graph, base_model)),
        ("candidate", reporter._measure(oracle, graph, model)),
    )
    candidate_bytes = b""
    for label, measurement in measurements:
        actual_bytes = reporter.normalized_json(measurement["probes"])
        if actual_bytes != expected_bytes:
            raise reporter.OwnershipError(
                f"{label} resolved owner pairs differ byte-for-byte from "
                "the independent base oracle"
            )
        if label == "candidate":
            candidate_bytes = actual_bytes

    def authority_records(
        selected_graph: dict[str, Any],
        selected_model: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records = []
        for probe in oracle["probes"]:
            if "expected_exclusion" in probe:
                continue
            owners = []
            for expected_owner in sorted(
                probe["expected_owners"],
                key=lambda item: (
                    item["edge_type"],
                    item["evidence_id"],
                ),
            ):
                matches = [
                    edge
                    for edge in selected_graph["edges"]
                    if edge["source"] == probe["expected_surface"]
                    and edge["type"] == expected_owner["edge_type"]
                    and edge["target"] == expected_owner["evidence_id"]
                ]
                if len(matches) != 1:
                    raise reporter.OwnershipError(
                        "oracle owner pair does not resolve to one exact graph edge"
                    )
                evidence_id = expected_owner["evidence_id"]
                authority = selected_model["authorities"].get(evidence_id)
                if authority is None:
                    raise reporter.OwnershipError(
                        f"oracle owner {evidence_id!r} lacks resolved authority"
                    )
                owners.append(
                    {
                        "edge_id": matches[0]["id"],
                        "edge_type": expected_owner["edge_type"],
                        "evidence_id": evidence_id,
                        "authority": authority,
                    }
                )
            records.append(
                {
                    "path": probe["path"],
                    "surface": probe["expected_surface"],
                    "owners": owners,
                }
            )
        return records

    base_records = authority_records(base_graph, base_model)
    candidate_records = authority_records(graph, model)
    changed_authorities = {
        node_id
        for node_id in (
            set(base_model["authorities"]) | set(model["authorities"])
        )
        if base_model["authorities"].get(node_id)
        != model["authorities"].get(node_id)
    }
    authority_edges = {
        edge["id"]
        for selected_graph in (base_graph, graph)
        for edge in selected_graph["edges"]
        if edge["target"] in changed_authorities
    }
    invalidation = reporter.compare_graph_edges(
        graph,
        base_graph,
        authority_edges,
    )
    oracle_edge_ids = {
        owner["edge_id"]
        for records in (base_records, candidate_records)
        for record in records
        for owner in record["owners"]
    }
    owned_edge_ids = {
        edge["id"]
        for selected_graph in (base_graph, graph)
        for edge in selected_graph["edges"]
        if edge["type"] != "depends-on"
    }
    unprobed_owned_edges = sorted(owned_edge_ids - oracle_edge_ids)
    if unprobed_owned_edges:
        raise reporter.OwnershipError(
            "exact-base oracle leaves owned edges unprobed: "
            f"{unprobed_owned_edges}"
        )
    invalidated_oracle_edges = sorted(
        oracle_edge_ids & set(invalidation["changed_edge_ids"])
    )
    invalidated_without_authority = sorted(
        set(invalidation["changed_edge_ids"]) - oracle_edge_ids
    )
    base_authority_bytes = reporter.normalized_json(base_records)
    candidate_authority_bytes = reporter.normalized_json(candidate_records)
    if (
        candidate_authority_bytes != base_authority_bytes
        or invalidated_oracle_edges
        or invalidated_without_authority
    ):
        raise reporter.OwnershipError(
            "candidate retargets exact-base oracle authority "
            f"(invalidated_edges={invalidated_oracle_edges}, "
            f"without_authority={invalidated_without_authority})"
        )
    return (
        hashlib.sha256(candidate_bytes).hexdigest(),
        hashlib.sha256(candidate_authority_bytes).hexdigest(),
    )


def verify(
    trusted_root: Path,
    repository_root: Path,
    base_sha: str,
    candidate_sha: str,
    *,
    trusted_sha: str | None = None,
    expected_mode: str = "exact-base-pinned",
) -> dict[str, Any]:
    budget = ProbeBudget()
    try:
        return _verify(
            trusted_root, repository_root, base_sha, candidate_sha,
            trusted_sha=trusted_sha, expected_mode=expected_mode, budget=budget,
        )
    finally:
        budget.close()


def _verify(trusted_root, repository_root, base_sha, candidate_sha, *,
            trusted_sha, expected_mode, budget):
    trusted_root = trusted_root.resolve(strict=True)
    repository_root = reporter.validate_repository_root(repository_root, budget=budget)
    if repository_root == trusted_root:
        raise reporter.OwnershipError(
            "trusted verifier root must be separate from the candidate tree"
        )
    for item in sys.path:
        if not item:
            continue
        path = Path(item).resolve()
        in_candidate = path == repository_root or repository_root in path.parents
        in_trusted = path == trusted_root or trusted_root in path.parents
        if in_candidate and not in_trusted:
            raise reporter.OwnershipError(
                "candidate repository is present on trusted verifier sys.path"
            )
    base_sha = _exact_commit(repository_root, base_sha, "base SHA", budget=budget)
    candidate_sha = _exact_commit(
        repository_root,
        candidate_sha,
        "candidate SHA",
        budget=budget,
    )
    head = _git(repository_root, "rev-parse", "HEAD", budget=budget)
    if head.returncode != 0 or head.stdout.decode("ascii").strip() != candidate_sha:
        raise reporter.OwnershipError(
            "candidate SHA does not match the checked-out HEAD"
        )
    base_entries = reporter.git_tree_entries(repository_root, base_sha, budget=budget)
    base_mode = _base_authority_mode(base_entries)
    if base_mode == "bootstrap-not-authoritative":
        return {
            "authority": "none",
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "mode": base_mode,
            "reason": "exact base predates validation ownership authority",
        }
    if expected_mode not in {"exact-base-pinned", "foundation-introduction"} or expected_mode != base_mode:
        raise reporter.OwnershipError("verifier result mode differs from the independently expected mode")
    source_sha = base_sha if trusted_sha is None else _exact_commit(
        repository_root, trusted_sha, "trusted source SHA", budget=budget,
    )
    if base_mode == "exact-base-pinned" and source_sha != base_sha:
        raise reporter.OwnershipError("exact-base verification requires exact BASE verifier source")
    if base_mode == "foundation-introduction" and trusted_sha is None:
        raise reporter.OwnershipError("graph introduction requires independently selected verifier source")
    runtime_root = _prepare_trusted_runtime_root(trusted_root)
    source_loader = capture(repository_root, source_sha, budget, scratch_root=runtime_root)
    base_loader = capture(repository_root, base_sha, budget, scratch_root=runtime_root)
    loader = capture(repository_root, candidate_sha, budget, scratch_root=runtime_root)
    trusted_paths = _trusted_paths(trusted_root, source_loader)
    loaded_before = _verify_loaded_modules(trusted_root, source_loader)
    candidate_changes = sorted(
        path for path in trusted_paths
        if loader.entries.get(path) != source_loader.entries[path]
    )
    if base_mode == "exact-base-pinned":
        _verify_base_step(loader, base_loader)
    entries = inventory(loader)
    with ProbeSession(loader, scratch_root=runtime_root, budget=budget,
                      runtime_files=ROOT_RUNTIME_FILES) as session:
        graph = loader.read_json(reporter.GRAPH_PATH, "candidate ownership graph")
        schema = source_loader.read_json(reporter.SCHEMA_PATH, "trusted ownership schema")
        oracle = source_loader.read_json(reporter.PROBE_ORACLE_PATH, "trusted ownership oracle")
        reporter.validate_probe_oracle(oracle, graph, entries)
        model = reporter.validate_graph(graph, schema, loader, entries, session=session)
        lifecycle = reporter.validate_executable_lifecycle(
            repository_root, graph, session=session, schema=schema, oracle=oracle, model=model,
        )
        if base_mode == "exact-base-pinned":
            with session.select_view(base_loader):
                base_graph = base_loader.read_json(reporter.GRAPH_PATH, "BASE ownership graph")
                reporter.validate_probe_oracle(oracle, base_graph, inventory(base_loader))
                base_model = reporter.validate_graph(
                    base_graph, schema, base_loader, inventory(base_loader), session=session,
                )
            pairs, authorities = _verify_oracle_pairs(oracle, graph, model, base_graph, base_model)
        else:
            measured = reporter._measure(oracle, graph, model)
            pairs = hashlib.sha256(reporter.normalized_json(measured["probes"])).hexdigest()
            authorities = hashlib.sha256(reporter.normalized_json(model["authorities"])).hexdigest()
        loaded_after = _verify_loaded_modules(trusted_root, source_loader)
        result = {
            "authority": "exact-base" if base_mode == "exact-base-pinned" else "explicit-introduction",
            "base_sha": base_sha, "candidate_sha": candidate_sha, "trusted_sha": source_sha,
            "candidate_trusted_changes": candidate_changes,
            "coverage_paths": len(model["coverage"]), "evidence_authorities": len(model["authorities"]),
            "mode": base_mode, "oracle_authority_sha256": authorities, "oracle_pairs_sha256": pairs,
            "trusted_modules": sorted(set(loaded_before) | set(loaded_after)),
            "trusted_package_files": len(trusted_paths),
            "runs": budget.runs, "states": budget.states, "processes": session.processes_used,
            "lifecycle": lifecycle,
        }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-root", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--trusted-sha")
    parser.add_argument("--expected-mode", choices=("exact-base-pinned", "foundation-introduction"),
                        default="exact-base-pinned")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        result = verify(
            arguments.trusted_root,
            arguments.repository_root,
            arguments.base_sha,
            arguments.candidate_sha,
            trusted_sha=arguments.trusted_sha, expected_mode=arguments.expected_mode,
        )
    except (OSError, ValueError, reporter.OwnershipError) as error:
        print(f"validation-ownership-base-verifier: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
