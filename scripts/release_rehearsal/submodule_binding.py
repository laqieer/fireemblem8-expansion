#!/usr/bin/env python3
"""Bind submodule declarations directly to immutable candidate gitlinks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from scripts.release_rehearsal import candidate_tree as ct
from scripts.release_rehearsal import git_source as gs
from scripts.release_rehearsal import gitmodules as gm

REQUIRED_URL_SCHEME = "https://"


class SubmoduleBindingError(ValueError):
    """The immutable tree and .gitmodules declaration disagree."""


def check_submodule_binding(repo_root: Path, target_sha: str = "HEAD") -> List[str]:
    if not gs.is_git_repo(repo_root):
        return []
    try:
        tree = ct.load(repo_root, target_sha)
        sections = gm.load_gitmodules_sections(repo_root, target_sha)
    except (ct.CandidateTreeError, gm.GitmodulesError, gs.GitSourceError) as error:
        raise SubmoduleBindingError(str(error)) from error

    declared = {}
    reasons: List[str] = []
    for name, section in sections.items():
        path = section.get("path")
        url = section.get("url")
        if not path or not url:
            reasons.append(f".gitmodules section {name!r} requires path and url")
            continue
        if path in declared:
            reasons.append(f".gitmodules has duplicate sections for {path!r}")
        declared[path] = url
        if not url.startswith(REQUIRED_URL_SCHEME):
            reasons.append(f".gitmodules URL for {path!r} must use {REQUIRED_URL_SCHEME}")

    gitlinks = {entry.path for entry in tree.gitlink_entries}
    reasons.extend(f"{path}: gitlink has no .gitmodules declaration" for path in sorted(gitlinks - set(declared)))
    reasons.extend(
        f"{path}: .gitmodules declaration has no immutable target-tree gitlink"
        for path in sorted(set(declared) - gitlinks)
    )
    return sorted(set(reasons))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--target-sha", default="HEAD")
    args = parser.parse_args(argv)
    try:
        target_sha = gs.resolve_sha(args.repo_root, args.target_sha)
        reasons = check_submodule_binding(args.repo_root, target_sha)
    except (SubmoduleBindingError, gs.GitSourceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"reasons": reasons}, indent=2, sort_keys=True))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
