#!/usr/bin/env python3
"""Validate candidate ownership with verifier code pinned to the exact PR base."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import stat
import subprocess
import sys
import re
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
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, git_command, relative_path
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.graph_report import capture, inventory
from scripts.validation_ownership.graph_commands import (
    ROOT_RUNTIME_FILES, SCANINC_MAKEFILE, SCANINC_SOURCES, SCANINC_WRAPPER,
)
from scripts.validation_ownership.lifecycle import finish_cleanup
from scripts.validation_ownership.make_probe import ProbeSession


TRUSTED_PREFIX = "scripts/validation_ownership/"
BASE_STEP_NAME = "Validate ownership with exact PR-base verifier"
BASE_STEP_MARKER = f"    - name: {BASE_STEP_NAME}\n"
REVIEWED_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
EXPECTED_MODES = ("exact-base-pinned", "foundation-introduction", "reviewed-evolution")
AUTHORITY_BY_MODE = {
    "exact-base-pinned": "exact-base",
    "foundation-introduction": "explicit-introduction",
    "reviewed-evolution": "reviewed-evolution",
}
CI_VERIFIER_PATH = f"{TRUSTED_PREFIX}ci_verifier.py"
FOUNDATION_BOOTSTRAP_PATHS = frozenset(
    f"{TRUSTED_PREFIX}{name}" for name in (
        "authority.py", "budget.py", "make_probe.py", "syscall_guard.py",
        "sandbox_exec.py", "shell_interceptor.c", "make_observer.c", "lifecycle.py",
    )
)
GRAPH_BOOTSTRAP_MARKERS = frozenset(
    {
        reporter.GRAPH_PATH.as_posix(),
        reporter.SCHEMA_PATH.as_posix(),
        reporter.MAKE_DYNAMIC_PATH.as_posix(),
        reporter.PROBE_ORACLE_PATH.as_posix(),
        f"{TRUSTED_PREFIX}reporter.py",
    }
)
BASE_BOOTSTRAP_SENTINELS = frozenset(
    {CI_VERIFIER_PATH, *FOUNDATION_BOOTSTRAP_PATHS, *GRAPH_BOOTSTRAP_MARKERS}
)
TRUSTED_SHARED_RUNTIME_PATHS = frozenset({
    "scripts/bash_parser.py", SCANINC_MAKEFILE, *SCANINC_SOURCES,
})
TRUSTED_RUNTIME_PATHS = frozenset(
    {
        CI_VERIFIER_PATH,
        f"{TRUSTED_PREFIX}generated_registry_probe.py",
        f"{TRUSTED_PREFIX}graph.schema.json",
        f"{TRUSTED_PREFIX}isolated_launcher.py",
        f"{TRUSTED_PREFIX}make_probe.py",
        f"{TRUSTED_PREFIX}metadata_transport.py",
        *TRUSTED_SHARED_RUNTIME_PATHS,
        f"{TRUSTED_PREFIX}python_commands.py",
        f"{TRUSTED_PREFIX}reporter.py",
        f"{TRUSTED_PREFIX}sandbox_exec.py",
        f"{TRUSTED_PREFIX}shell_interceptor.c",
        *(f"{TRUSTED_PREFIX}{name}" for name in (
            "authority.py", "budget.py", "lifecycle.py", "syscall_guard.py",
            "make_observer.c", "dispatch.h", "graph_commands.py", "graph_registry.py",
            "graph_probe.py", "graph_report.py", "graph_lifecycle.py", "scaninc_sources.cpp",
            "coordinator_capture.py",
            "graph_regex.py",
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


@dataclass(frozen=True)
class _OwnedRuntimeRoot:
    path: Path
    parent_device: int
    parent_inode: int
    device: int
    inode: int
    uid: int
    mode: int


def _cleanup_trusted_runtime_root(owned: _OwnedRuntimeRoot) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_descriptor = descriptor = -1
    try:
        parent_descriptor = os.open(owned.path.parent, flags)
        parent = os.fstat(parent_descriptor)
        if (parent.st_dev, parent.st_ino) != (
            owned.parent_device, owned.parent_inode,
        ):
            raise reporter.OwnershipError(
                "trusted verifier runtime root parent identity changed"
            )
        current = os.stat(
            owned.path.name, dir_fd=parent_descriptor, follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (owned.device, owned.inode)
            or current.st_uid != owned.uid
            or stat.S_IMODE(current.st_mode) != owned.mode
        ):
            raise reporter.OwnershipError(
                "trusted verifier runtime root identity changed before cleanup"
            )
        descriptor = os.open(owned.path.name, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (owned.device, owned.inode):
            raise reporter.OwnershipError(
                "trusted verifier runtime root changed while opening for cleanup"
            )
        os.rmdir(owned.path.name, dir_fd=parent_descriptor)
        try:
            os.stat(owned.path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise reporter.OwnershipError(
                "trusted verifier runtime root was replaced during cleanup"
            )
    except reporter.OwnershipError:
        raise
    except OSError as error:
        raise reporter.OwnershipError(
            f"cannot remove trusted verifier runtime root: {error}"
        ) from error
    finally:
        for file_descriptor in (descriptor, parent_descriptor):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _prepare_trusted_runtime_root(trusted_root: Path) -> _OwnedRuntimeRoot:
    if not hasattr(os, "O_NOFOLLOW"):
        raise reporter.OwnershipError(
            "trusted verifier runtime root requires O_NOFOLLOW"
        )
    trusted_root = trusted_root.resolve(strict=True)
    runtime_root = trusted_root / ".validation-ownership-runtime"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_descriptor = descriptor = -1
    owned = None
    primary = None
    try:
        parent_descriptor = os.open(trusted_root, flags)
        parent_stat = os.fstat(parent_descriptor)
        os.mkdir(runtime_root.name, mode=0o700, dir_fd=parent_descriptor)
        entry_stat = os.stat(
            runtime_root.name, dir_fd=parent_descriptor, follow_symlinks=False,
        )
        owned = _OwnedRuntimeRoot(
            runtime_root,
            parent_stat.st_dev,
            parent_stat.st_ino,
            entry_stat.st_dev,
            entry_stat.st_ino,
            entry_stat.st_uid,
            stat.S_IMODE(entry_stat.st_mode),
        )
        descriptor = os.open(runtime_root.name, flags, dir_fd=parent_descriptor)
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(entry_stat.st_mode)
            or opened_stat.st_dev != entry_stat.st_dev
            or opened_stat.st_ino != entry_stat.st_ino
            or opened_stat.st_uid != os.getuid()
            or stat.S_IMODE(opened_stat.st_mode) != 0o700
        ):
            raise reporter.OwnershipError(
                "trusted verifier runtime root identity is invalid"
            )
        return owned
    except reporter.OwnershipError as error:
        primary = error
        raise
    except OSError as error:
        primary = reporter.OwnershipError(
            f"cannot create trusted verifier runtime root: {error}"
        )
        raise primary from error
    finally:
        actions = [
            lambda file_descriptor=file_descriptor: os.close(file_descriptor)
            for file_descriptor in (descriptor, parent_descriptor)
            if file_descriptor >= 0
        ]
        if primary is not None and owned is not None:
            actions.append(lambda: _cleanup_trusted_runtime_root(owned))
        finish_cleanup(actions, primary=primary)


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
    if CI_VERIFIER_PATH not in base_entries and (
        FOUNDATION_BOOTSTRAP_PATHS <= set(base_entries)
        and not GRAPH_BOOTSTRAP_MARKERS & set(base_entries)
    ):
        return "foundation-introduction"
    missing = sorted(BASE_AUTHORITY_PATHS - set(base_entries))
    if missing:
        raise reporter.OwnershipError(
            f"exact base has incomplete validation authority: {missing}"
        )
    return "exact-base-pinned"


def _scaninc_build_contract(source: str):
    """Recognize the supported Make execution contract without evaluating Make functions."""
    variables = {
        "CXX": ("g++",), "CXXFLAGS": ("-Wall", "-Werror", "-std=c++11", "-O2"),
        "SRCS": tuple(Path(path).name for path in SCANINC_SOURCES if path.endswith(".cpp")),
        "HEADERS": tuple(Path(path).name for path in SCANINC_SOURCES if path.endswith(".h")),
        "LDFLAGS": (),
    }
    prerequisites = {".PHONY": ("clean",), "scaninc": ("$(HEADERS)", "$(SRCS)"), "clean": ()}
    recipes = {
        ".PHONY": (),
        "scaninc": ("$(CXX)", "$(CXXFLAGS)", "$(SRCS)", "-o", "$@", "$(LDFLAGS)"),
        "clean": ("$(RM)", "scaninc", "scaninc.exe"),
    }
    assignments, rules = set(), {}
    target = default = None
    error = reporter.OwnershipError("unsupported trusted scanner Makefile build contract")
    for raw in source.replace("\\\n", " ").splitlines():
        line = re.sub(r"\$\{(\w+|@)\}", r"$(\1)", raw.partition("#")[0].strip()).replace("$(@)", "$@")
        if not line:
            continue
        if raw.startswith("\t"):
            if target is None or rules[target] or tuple(line.removeprefix("@").split()) != recipes[target]:
                raise error
            rules[target] = True
            continue
        target = None
        assignment = re.fullmatch(r"(\w+)\s*(?::=|=)\s*(.*)", line)
        if assignment is not None:
            name, value = assignment.groups()
            if name not in variables or sorted(value.split()) != sorted(variables[name]):
                raise error
            assignments.add(name)
            continue
        rule = re.fullmatch(r"(\.PHONY|scaninc|clean)\s*:\s*(.*)", line)
        if rule is None:
            raise error
        target, dependencies = rule.groups()
        if target in rules or tuple(sorted(dependencies.split())) != prerequisites[target]:
            raise error
        if target == "scaninc" and not {"SRCS", "HEADERS"} <= assignments:
            raise error
        if target != ".PHONY" and default is None:
            default = target
        rules[target] = False
    if (
        assignments - {"LDFLAGS"} != set(variables) - {"LDFLAGS"}
        or default != "scaninc" or rules != {".PHONY": False, "scaninc": True, "clean": True}
    ):
        raise error


def _trusted_namespace(loader):
    return {
        path for path in loader.entries
        if path.startswith(TRUSTED_PREFIX) or path in TRUSTED_SHARED_RUNTIME_PATHS
    }


def _trusted_changes(before, after, modules=()):
    paths = _trusted_namespace(before) | _trusted_namespace(after) | set(modules)
    return sorted(path for path in paths if before.entries.get(path) != after.entries.get(path))


def _trusted_paths(trusted_root: Path, base_loader: reporter.AuthorityLoader) -> set[str]:
    paths = _trusted_namespace(base_loader)
    if not TRUSTED_RUNTIME_PATHS <= paths:
        raise reporter.OwnershipError(
            "exact base lacks the complete validation ownership verifier"
        )
    for path in sorted(paths):
        base_loader.entry(path, "trusted verifier source")
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
    actual.update(
        path for path in TRUSTED_SHARED_RUNTIME_PATHS
        if (trusted_root / path).is_file() and "__pycache__" not in (trusted_root / path).parts
    )
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


def _base_step(text: str) -> tuple:
    try:
        _, _, jobs = reporter.workflow_verify._parse_workflow_structure_text(text)
    except ValueError as error:
        raise reporter.OwnershipError(f"Build verifier staging authority is invalid: {error}") from error
    matches = [
        (job, context, role, fields)
        for job, context, steps in jobs
        for role, name, fields in steps
        if name == BASE_STEP_NAME
    ]
    if len(matches) != 1 or matches[0][0] != "host-tests":
        raise reporter.OwnershipError(
            "Build workflow requires one PR-base verifier in host-tests"
        )
    return matches[0][1:]


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


def _candidate_changed_paths(
    repository_root: Path,
    base_sha: str,
    candidate_sha: str,
    *,
    budget: ProbeBudget,
) -> list[str]:
    completed = _git(
        repository_root,
        "diff",
        "--name-only",
        "--no-renames",
        base_sha,
        candidate_sha,
        budget=budget,
    )
    if completed.returncode != 0:
        raise reporter.OwnershipError("candidate change scope is unavailable")
    paths = [relative_path(line) for line in completed.stdout.decode("utf-8").splitlines() if line]
    if len(paths) != len(set(paths)):
        raise reporter.OwnershipError("candidate change scope duplicates a path")
    return sorted(paths)


def _reviewed_evolution_selection(
    repository: str | None,
    pull_request: int | None,
    changed_paths: list[str] | None,
    changed_edge_ids: list[str] | None,
    affected_consumers: list[str] | None,
) -> dict[str, Any]:
    if not isinstance(repository, str) or not REVIEWED_REPOSITORY_RE.fullmatch(repository):
        raise reporter.OwnershipError("reviewed evolution requires an exact repository/owner")
    if type(pull_request) is not int or pull_request < 1:
        raise reporter.OwnershipError("reviewed evolution requires a positive pull request number")
    changed_edge_ids = [] if changed_edge_ids is None else changed_edge_ids
    affected_consumers = [] if affected_consumers is None else affected_consumers
    for label, values, required in (
        ("changed path", changed_paths, True),
        ("changed edge", changed_edge_ids, False),
        ("affected consumer", affected_consumers, False),
    ):
        if (
            not isinstance(values, list) or required and not values
            or any(not isinstance(value, str) or not value for value in values)
            or values != sorted(set(values))
        ):
            raise reporter.OwnershipError(f"reviewed evolution requires a sorted exact {label} scope")
    if bool(changed_edge_ids) != bool(affected_consumers):
        raise reporter.OwnershipError("reviewed edge and consumer scopes must both be empty or nonempty")
    return {
        "repository": repository,
        "pull_request": pull_request,
        "changed_paths": changed_paths,
        "changed_edge_ids": changed_edge_ids,
        "affected_consumers": affected_consumers,
    }


def _affected_consumers(
    candidate_graph: dict[str, Any],
    base_graph: dict[str, Any],
    changed_edge_ids: list[str],
) -> list[str]:
    selected_edges = {}
    for graph in (base_graph, candidate_graph):
        for edge in graph["edges"]:
            selected_edges.setdefault(edge["id"], []).append(edge)
    surfaces = {
        node["id"]
        for graph in (base_graph, candidate_graph)
        for node in graph["nodes"]
        if node["kind"] == "surface"
    }
    affected = set()
    for edge_id in changed_edge_ids:
        edges = selected_edges.get(edge_id)
        if edges is None:
            raise reporter.OwnershipError(
                f"reviewed evolution changed edge {edge_id!r} has no affected consumer"
            )
        for edge in edges:
            if edge["source"] in surfaces:
                affected.add(edge["source"])
            if edge["type"] == "depends-on" and edge["target"] in surfaces:
                affected.add(edge["target"])
    dependencies = {}
    for graph in (base_graph, candidate_graph):
        for node in graph["nodes"]:
            if node["kind"] == "surface":
                dependencies.setdefault(node["id"], set()).update(node["dependencies"])
    changed = True
    while changed:
        before = len(affected)
        affected.update(
            consumer
            for consumer, required in dependencies.items()
            if required & affected
        )
        changed = len(affected) != before
    return sorted(affected)


def _reviewed_evolution_authority(
    base_oracle: dict[str, Any],
    base_graph: dict[str, Any],
    base_model: dict[str, Any],
    base_loader: reporter.AuthorityLoader,
    candidate_oracle: dict[str, Any],
    candidate_graph: dict[str, Any],
    candidate_model: dict[str, Any],
    candidate_loader: reporter.AuthorityLoader,
) -> tuple[str, str, dict[str, Any]]:
    base_pairs = reporter._measure(base_oracle, base_graph, base_model)
    candidate_pairs = reporter._measure(candidate_oracle, candidate_graph, candidate_model)

    def authority_records(
        oracle: dict[str, Any],
        selected_graph: dict[str, Any],
        selected_model: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], set[str], set[str]]:
        records = []
        oracle_edge_ids = set()
        oracle_surfaces = set()
        for probe in oracle["probes"]:
            if "expected_exclusion" in probe:
                continue
            oracle_surfaces.add(probe["expected_surface"])
            owners = []
            for expected_owner in sorted(
                probe["expected_owners"],
                key=lambda item: (item["edge_type"], item["evidence_id"]),
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
                        "reviewed oracle owner pair does not resolve to one exact graph edge"
                    )
                evidence_id = expected_owner["evidence_id"]
                authority = selected_model["authorities"].get(evidence_id)
                if authority is None:
                    raise reporter.OwnershipError(
                        f"reviewed oracle owner {evidence_id!r} lacks resolved authority"
                    )
                owners.append(
                    {
                        "edge_id": matches[0]["id"],
                        "edge_type": expected_owner["edge_type"],
                        "evidence_id": evidence_id,
                        "authority": authority,
                    }
                )
                oracle_edge_ids.add(matches[0]["id"])
            records.append(
                {
                    "path": probe["path"],
                    "surface": probe["expected_surface"],
                    "owners": owners,
                }
            )
        owned_edge_ids = {
            edge["id"]
            for edge in selected_graph["edges"]
            if edge["type"] != "depends-on"
        }
        missing = sorted(owned_edge_ids - oracle_edge_ids)
        if missing:
            raise reporter.OwnershipError(
                f"reviewed oracle leaves owned edges unprobed: {missing}"
            )
        dependency_edge_ids = {
            edge["id"]
            for edge in selected_graph["edges"]
            if edge["type"] == "depends-on"
            and edge["source"] in oracle_surfaces
            and edge["target"] in oracle_surfaces
        }
        return records, oracle_edge_ids, dependency_edge_ids

    base_records, base_oracle_edges, base_dependency_edges = authority_records(
        base_oracle, base_graph, base_model,
    )
    candidate_records, candidate_oracle_edges, candidate_dependency_edges = authority_records(
        candidate_oracle, candidate_graph, candidate_model,
    )
    authority_edges = reporter._authority_changed_edges(
        candidate_graph,
        base_graph,
        candidate_model,
        candidate_loader,
        base_loader,
        base_model=base_model,
    )
    invalidation = reporter.compare_graph_edges(candidate_graph, base_graph, authority_edges)
    uncovered = sorted(
        set(invalidation["changed_edge_ids"])
        - (
            base_oracle_edges
            | candidate_oracle_edges
            | base_dependency_edges
            | candidate_dependency_edges
        )
    )
    if uncovered:
        raise reporter.OwnershipError(
            "reviewed evolution leaves invalidated edges without oracle coverage: "
            f"{uncovered}"
        )
    return (
        hashlib.sha256(reporter.normalized_json(candidate_pairs["probes"])).hexdigest(),
        hashlib.sha256(reporter.normalized_json(candidate_records)).hexdigest(),
        invalidation,
    )


def verify(
    trusted_root: Path,
    repository_root: Path,
    base_sha: str,
    candidate_sha: str,
    *,
    trusted_sha: str | None = None,
    expected_mode: str = "exact-base-pinned",
    reviewed_repository: str | None = None,
    reviewed_pull_request: int | None = None,
    reviewed_paths: list[str] | None = None,
    reviewed_edge_ids: list[str] | None = None,
    reviewed_consumers: list[str] | None = None,
) -> dict[str, Any]:
    budget = ProbeBudget()
    runtime_owner = []
    primary = None

    def cleanup_runtime():
        budget.close()
        for owned in runtime_owner:
            _cleanup_trusted_runtime_root(owned)

    try:
        return _verify(
            trusted_root, repository_root, base_sha, candidate_sha,
            trusted_sha=trusted_sha,
            expected_mode=expected_mode,
            reviewed_repository=reviewed_repository,
            reviewed_pull_request=reviewed_pull_request,
            reviewed_paths=reviewed_paths,
            reviewed_edge_ids=reviewed_edge_ids,
            reviewed_consumers=reviewed_consumers,
            budget=budget, runtime_owner=runtime_owner,
        )
    except BaseException as error:
        primary = error
        raise
    finally:
        finish_cleanup([cleanup_runtime], primary=primary)


def _verify(trusted_root, repository_root, base_sha, candidate_sha, *,
            trusted_sha, expected_mode, reviewed_repository, reviewed_pull_request,
            reviewed_paths, reviewed_edge_ids, reviewed_consumers, budget, runtime_owner):
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
    if expected_mode not in EXPECTED_MODES:
        raise reporter.OwnershipError("verifier result mode differs from the independently expected mode")
    if expected_mode == "reviewed-evolution":
        if base_mode != "exact-base-pinned":
            raise reporter.OwnershipError("reviewed evolution requires an exact-base authority")
    elif expected_mode != base_mode:
        raise reporter.OwnershipError("verifier result mode differs from the independently expected mode")
    source_sha = base_sha if trusted_sha is None else _exact_commit(
        repository_root, trusted_sha, "trusted source SHA", budget=budget,
    )
    if base_mode == "exact-base-pinned" and source_sha != base_sha:
        if expected_mode != "reviewed-evolution":
            raise reporter.OwnershipError("exact-base verification requires exact BASE verifier source")
    if base_mode == "foundation-introduction" and trusted_sha is None:
        raise reporter.OwnershipError("graph introduction requires independently selected verifier source")
    reviewed = None
    candidate_changed_paths = _candidate_changed_paths(repository_root, base_sha, candidate_sha, budget=budget)
    if expected_mode == "reviewed-evolution":
        reviewed = _reviewed_evolution_selection(
            reviewed_repository,
            reviewed_pull_request,
            reviewed_paths,
            reviewed_edge_ids,
            reviewed_consumers,
        )
        if candidate_changed_paths != reviewed["changed_paths"]:
            raise reporter.OwnershipError("reviewed evolution path scope differs from the exact base/candidate diff")
    elif any(value is not None for value in (
        reviewed_repository,
        reviewed_pull_request,
        reviewed_paths,
        reviewed_edge_ids,
        reviewed_consumers,
    )):
        raise reporter.OwnershipError("non-reviewed verification cannot declare reviewed evolution scope")
    owned_runtime = _prepare_trusted_runtime_root(trusted_root)
    runtime_owner.append(owned_runtime)
    runtime_root = owned_runtime.path
    source_loader = capture(repository_root, source_sha, budget, scratch_root=runtime_root)
    base_loader = capture(repository_root, base_sha, budget, scratch_root=runtime_root)
    loader = capture(repository_root, candidate_sha, budget, scratch_root=runtime_root)
    trusted_paths = _trusted_paths(trusted_root, source_loader)
    loaded_before = _verify_loaded_modules(trusted_root, source_loader)
    candidate_changes = _trusted_changes(source_loader, loader, loaded_before)
    if candidate_changes:
        raise reporter.OwnershipError(f"candidate trusted sources differ from selected source: {candidate_changes}")
    _scaninc_build_contract(source_loader.read_blob(SCANINC_MAKEFILE, "trusted scanner build").decode("utf-8"))
    if expected_mode == "exact-base-pinned":
        _verify_base_step(loader, base_loader)
    entries = inventory(loader)
    with ProbeSession(loader, scratch_root=runtime_root, budget=budget,
                      runtime_files=ROOT_RUNTIME_FILES) as session:
        graph = loader.read_json(reporter.GRAPH_PATH, "candidate ownership graph")
        authority_loader = loader if expected_mode == "reviewed-evolution" else source_loader
        candidate_schema = authority_loader.read_json(
            reporter.SCHEMA_PATH, "trusted candidate ownership schema",
        )
        candidate_oracle = authority_loader.read_json(
            reporter.PROBE_ORACLE_PATH, "trusted candidate ownership oracle",
        )
        reporter.validate_probe_oracle(candidate_oracle, graph, entries)
        model = reporter.validate_graph(graph, candidate_schema, loader, entries, session=session)
        lifecycle = reporter.validate_executable_lifecycle(
            repository_root, graph, session=session, schema=candidate_schema, oracle=candidate_oracle, model=model,
        )
        invalidation = {
            "invalidated": False,
            "reason": "comparison-not-requested",
            "changed_edge_ids": [],
        }
        if base_mode == "exact-base-pinned":
            with session.select_view(base_loader):
                base_graph = base_loader.read_json(reporter.GRAPH_PATH, "BASE ownership graph")
                base_schema = base_loader.read_json(reporter.SCHEMA_PATH, "BASE ownership schema")
                base_oracle = base_loader.read_json(reporter.PROBE_ORACLE_PATH, "BASE ownership oracle")
                reporter.validate_probe_oracle(base_oracle, base_graph, inventory(base_loader))
                base_model = reporter.validate_graph(
                    base_graph, base_schema, base_loader, inventory(base_loader), session=session,
                )
            if expected_mode == "reviewed-evolution":
                pairs, authorities, invalidation = _reviewed_evolution_authority(
                    base_oracle,
                    base_graph,
                    base_model,
                    base_loader,
                    candidate_oracle,
                    graph,
                    model,
                    loader,
                )
                if invalidation["changed_edge_ids"] != reviewed["changed_edge_ids"]:
                    raise reporter.OwnershipError(
                        "reviewed evolution relationship scope differs from actual invalidation"
                    )
                consumers = _affected_consumers(
                    graph,
                    base_graph,
                    invalidation["changed_edge_ids"],
                )
                if consumers != reviewed["affected_consumers"]:
                    raise reporter.OwnershipError(
                        "reviewed evolution affected consumer scope differs from actual invalidation"
                    )
            else:
                pairs, authorities = _verify_oracle_pairs(
                    base_oracle, graph, model, base_graph, base_model,
                )
        else:
            measured = reporter._measure(candidate_oracle, graph, model)
            pairs = hashlib.sha256(reporter.normalized_json(measured["probes"])).hexdigest()
            authorities = hashlib.sha256(reporter.normalized_json(model["authorities"])).hexdigest()
        loaded_after = _verify_loaded_modules(trusted_root, source_loader)
        modules = sorted(set(loaded_before) | set(loaded_after))
        candidate_changes = _trusted_changes(source_loader, loader, modules)
        if candidate_changes:
            raise reporter.OwnershipError(f"candidate trusted sources differ from selected source: {candidate_changes}")
        source_changes = _trusted_changes(base_loader, source_loader, modules)
        if reviewed is not None and not invalidation["invalidated"] and not source_changes:
            raise reporter.OwnershipError("reviewed evolution requires an actual graph or trusted source change")
        result = {
            "authority": AUTHORITY_BY_MODE[expected_mode],
            "base_sha": base_sha, "candidate_sha": candidate_sha, "trusted_sha": source_sha,
            "candidate_trusted_changes": candidate_changes,
            "trusted_source_changes": source_changes,
            "candidate_changed_paths": candidate_changed_paths,
            "coverage_paths": len(model["coverage"]), "evidence_authorities": len(model["authorities"]),
            "mode": expected_mode, "oracle_authority_sha256": authorities, "oracle_pairs_sha256": pairs,
            "trusted_modules": modules,
            "trusted_package_files": len(trusted_paths),
            "runs": budget.runs, "states": budget.states, "processes": session.processes_used,
            "lifecycle": lifecycle,
            "review_invalidation": invalidation,
        }
        if reviewed is not None:
            result["reviewed_evolution"] = reviewed
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-root", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--trusted-sha")
    parser.add_argument("--expected-mode", choices=EXPECTED_MODES,
                        default="exact-base-pinned")
    parser.add_argument("--reviewed-repository")
    parser.add_argument("--reviewed-pull-request", type=int)
    parser.add_argument("--reviewed-path", action="append", dest="reviewed_paths", default=None)
    parser.add_argument("--reviewed-edge", action="append", dest="reviewed_edge_ids", default=None)
    parser.add_argument("--reviewed-consumer", action="append", dest="reviewed_consumers", default=None)
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
            reviewed_repository=arguments.reviewed_repository,
            reviewed_pull_request=arguments.reviewed_pull_request,
            reviewed_paths=arguments.reviewed_paths,
            reviewed_edge_ids=arguments.reviewed_edge_ids,
            reviewed_consumers=arguments.reviewed_consumers,
        )
    except (OSError, ValueError, reporter.OwnershipError) as error:
        cleanup = getattr(error, "cleanup_errors", ())
        detail = "; ".join((str(error), *cleanup))
        print(f"validation-ownership-base-verifier: {detail}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
