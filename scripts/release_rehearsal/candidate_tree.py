#!/usr/bin/env python3
"""Immutable candidate-tree membership for release rehearsal.

Git's target commit is the sole authority for release-member paths, modes,
and gitlinks. This module deliberately records no checked-in copy of that
mechanical identity: callers derive it for the exact candidate they verify.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from scripts.release_rehearsal import git_source as gs


class CandidateTreeError(ValueError):
    """The immutable target tree contains a release-unsafe entry."""


@dataclass(frozen=True)
class CandidateTree:
    target_sha: str
    entries: tuple[gs.GitEntry, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    @property
    def source_entries(self) -> tuple[gs.GitEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.is_gitlink)

    @property
    def source_paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.source_entries)

    @property
    def gitlink_entries(self) -> tuple[gs.GitEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_gitlink)

    @property
    def modes(self) -> Dict[str, str]:
        return {entry.path: entry.mode for entry in self.entries}


def load(repo_root: Path, target_sha: str) -> CandidateTree:
    """Loads and validates one exact immutable target tree.

    A path may be a regular blob or a gitlink. Symlinks and unknown tree
    modes remain represented so downstream source-guard validation can reject
    them explicitly rather than losing the member from coverage.
    """
    entries = tuple(gs.list_tree(repo_root, target_sha))
    seen = set()
    errors: List[str] = []
    for entry in entries:
        if entry.path in seen:
            errors.append(f"duplicate path {entry.path!r} in target tree")
        seen.add(entry.path)
        if entry.is_gitlink and entry.mode != gs.MODE_GITLINK:
            errors.append(
                f"{entry.path}: gitlink must use mode {gs.MODE_GITLINK}, found {entry.mode!r}"
            )
        elif not entry.is_gitlink and entry.obj_type != "blob":
            errors.append(
                f"{entry.path}: non-gitlink entry has unsupported object type {entry.obj_type!r}"
            )
    if errors:
        raise CandidateTreeError("; ".join(sorted(errors)))
    return CandidateTree(target_sha=target_sha, entries=entries)


def source_paths(entries: Iterable[gs.GitEntry]) -> List[str]:
    """Returns sorted archive-source paths from already-validated entries."""
    return sorted(entry.path for entry in entries if not entry.is_gitlink)
