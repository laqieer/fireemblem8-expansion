#!/usr/bin/env python3
"""Validate candidate ownership with verifier code pinned to the exact PR base."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


TRUSTED_ROOT = Path(__file__).resolve().parents[2]
if str(TRUSTED_ROOT) not in sys.path:
    sys.path.insert(0, str(TRUSTED_ROOT))

from scripts.validation_ownership import reporter


TRUSTED_PREFIX = "scripts/validation_ownership/"
BASE_STEP_MARKER = "    - name: Validate ownership with exact PR-base verifier\n"


class PinnedAuthorityLoader(reporter.AuthorityLoader):
    """Read verifier package files from base and all other authority from head."""

    def __init__(
        self,
        candidate_root: Path,
        entries: dict[str, reporter.GitTreeEntry],
        candidate_sha: str,
        base_loader: reporter.AuthorityLoader,
        trusted_paths: set[str],
    ):
        super().__init__(candidate_root, entries, candidate_sha)
        self.base_loader = base_loader
        self.trusted_paths = trusted_paths

    def read_blob(self, relative: str | Path, label: str) -> bytes:
        path = reporter._validate_relative_path(relative, label)
        if path in self.trusted_paths:
            return self.base_loader.read_blob(path, label)
        return super().read_blob(path, label)


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    return subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(root),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=environment,
    )


def _exact_commit(root: Path, value: str, label: str) -> str:
    completed = _git(root, "rev-parse", "--verify", f"{value}^{{commit}}")
    if completed.returncode != 0:
        raise reporter.OwnershipError(f"{label} is not an available commit")
    resolved = completed.stdout.decode("ascii").strip()
    if resolved != value:
        raise reporter.OwnershipError(
            f"{label} must be an exact full commit SHA"
        )
    return resolved


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
    required = {
        f"{TRUSTED_PREFIX}ci_gate.mk",
        f"{TRUSTED_PREFIX}ci_verifier.py",
        f"{TRUSTED_PREFIX}isolated_launcher.py",
        f"{TRUSTED_PREFIX}make_probe.py",
        f"{TRUSTED_PREFIX}reporter.py",
        f"{TRUSTED_PREFIX}sandbox_exec.py",
        f"{TRUSTED_PREFIX}shell_interceptor.c",
    }
    if not required <= paths:
        raise reporter.OwnershipError(
            "exact base lacks the complete validation ownership verifier"
        )
    for path in sorted(paths):
        target = trusted_root / path
        if not target.is_file() or target.is_symlink():
            raise reporter.OwnershipError(
                f"trusted verifier path {path!r} is missing or not regular"
            )
        if target.read_bytes() != base_loader.read_blob(
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
        if path.read_bytes() != base_loader.read_blob(
            relative,
            "trusted module identity",
        ):
            raise reporter.OwnershipError(
                f"trusted module {name!r} differs from the exact base"
            )
        result.append(relative)
    return result


def _pinned_loader(
    candidate_root: Path,
    candidate_sha: str,
    base_loader: reporter.AuthorityLoader,
    trusted_paths: set[str],
) -> tuple[dict[str, reporter.GitTreeEntry], PinnedAuthorityLoader, list[str]]:
    candidate_entries = reporter.git_tree_entries(candidate_root, candidate_sha)
    changed = []
    for path in trusted_paths:
        candidate = candidate_entries.get(path)
        base = base_loader.entries[path]
        if candidate != base:
            changed.append(path)
        candidate_entries[path] = base
    unexpected = sorted(
        path
        for path in candidate_entries
        if path.startswith(TRUSTED_PREFIX) and path not in trusted_paths
    )
    if unexpected:
        raise reporter.OwnershipError(
            f"candidate adds untrusted verifier files {unexpected}"
        )
    return (
        candidate_entries,
        PinnedAuthorityLoader(
            candidate_root,
            candidate_entries,
            candidate_sha,
            base_loader,
            trusted_paths,
        ),
        sorted(changed),
    )


def _base_step(text: str) -> str:
    start = text.find(BASE_STEP_MARKER)
    if start < 0:
        raise reporter.OwnershipError(
            "Build workflow lacks the exact PR-base verifier step"
        )
    end = text.find("\n    - name:", start + len(BASE_STEP_MARKER))
    return text[start:] if end < 0 else text[start:end + 1]


def _verify_base_step(
    loader: PinnedAuthorityLoader,
    base_loader: reporter.AuthorityLoader,
) -> None:
    path = reporter.BUILD_WORKFLOW_PATH
    candidate = reporter.AuthorityLoader.read_blob(
        loader,
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


def verify(
    trusted_root: Path,
    repository_root: Path,
    base_sha: str,
    candidate_sha: str,
) -> dict[str, Any]:
    trusted_root = trusted_root.resolve(strict=True)
    repository_root = reporter.validate_repository_root(repository_root)
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
    base_sha = _exact_commit(repository_root, base_sha, "base SHA")
    candidate_sha = _exact_commit(
        repository_root,
        candidate_sha,
        "candidate SHA",
    )
    head = _git(repository_root, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.decode("ascii").strip() != candidate_sha:
        raise reporter.OwnershipError(
            "candidate SHA does not match the checked-out HEAD"
        )
    base_entries = reporter.git_tree_entries(repository_root, base_sha)
    base_loader = reporter.AuthorityLoader(
        repository_root,
        base_entries,
        base_sha,
    )
    trusted_paths = _trusted_paths(trusted_root, base_loader)
    loaded_before = _verify_loaded_modules(trusted_root, base_loader)
    entries, loader, candidate_changes = _pinned_loader(
        repository_root,
        candidate_sha,
        base_loader,
        trusted_paths,
    )
    _verify_base_step(loader, base_loader)
    graph = loader.read_json(reporter.GRAPH_PATH, "candidate ownership graph")
    schema = base_loader.read_json(
        reporter.SCHEMA_PATH,
        "base ownership schema",
    )
    oracle = base_loader.read_json(
        reporter.PROBE_ORACLE_PATH,
        "base ownership oracle",
    )
    reporter.validate_probe_oracle(oracle, graph, entries)
    model = reporter.validate_graph(graph, schema, loader, entries)
    loaded_after = _verify_loaded_modules(trusted_root, base_loader)
    return {
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "candidate_trusted_changes": candidate_changes,
        "coverage_paths": len(model["coverage"]),
        "evidence_authorities": len(model["authorities"]),
        "mode": "exact-base-pinned",
        "trusted_modules": sorted(set(loaded_before) | set(loaded_after)),
        "trusted_package_files": len(trusted_paths),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-root", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        result = verify(
            arguments.trusted_root,
            arguments.repository_root,
            arguments.base_sha,
            arguments.candidate_sha,
        )
    except (OSError, ValueError, reporter.OwnershipError) as error:
        print(f"validation-ownership-base-verifier: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
