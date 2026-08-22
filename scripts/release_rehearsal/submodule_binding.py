#!/usr/bin/env python3
"""Fail-closed submodule binding from the immutable candidate tree.

The target tree is the sole authority for a gitlink's pinned commit and mode.
Committed human provenance supplies only the reviewed path and source URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from scripts.release_rehearsal import candidate_tree as ct
from scripts.release_rehearsal import git_source as gs
from scripts.release_rehearsal import gitmodules as gm
from scripts.release_rehearsal import provenance as prov

DEFAULT_METADATA_PATH = Path("docs/release_data/provenance.json")
REQUIRED_URL_SCHEME = "https://"


class SubmoduleBindingError(ValueError):
    """The submodule source metadata conflicts with the immutable tree."""


def check_submodule_binding(
    repo_root: Path,
    target_sha: str = "HEAD",
    metadata_path: Path = DEFAULT_METADATA_PATH,
) -> List[str]:
    if not gs.is_git_repo(repo_root):
        return ["a non-git candidate cannot derive immutable gitlink bindings"]
    try:
        tree = ct.load(repo_root, target_sha)
        entries = prov.load_metadata(metadata_path)
        sections = gm.load_gitmodules_sections(repo_root, target_sha)
    except (ct.CandidateTreeError, prov.ProvenanceError, gm.GitmodulesError, gs.GitSourceError) as error:
        raise SubmoduleBindingError(str(error)) from error

    metadata = {entry["path"]: entry for entry in entries if entry["category"] == "submodule"}
    section_by_path = {}
    reasons: List[str] = []
    for name, section in sections.items():
        path = section.get("path")
        url = section.get("url")
        if not path or not url:
            reasons.append(f".gitmodules section {name!r} requires path and url")
            continue
        if path in section_by_path:
            reasons.append(f".gitmodules has duplicate sections for {path!r}")
        section_by_path[path] = url
        if not url.startswith(REQUIRED_URL_SCHEME):
            reasons.append(f".gitmodules URL for {path!r} must use {REQUIRED_URL_SCHEME}")

    gitlinks = {entry.path: entry for entry in tree.gitlink_entries}
    for path in sorted(set(gitlinks) - set(metadata)):
        reasons.append(f"{path}: gitlink has no exact human provenance metadata")
    for path in sorted(set(metadata) - set(gitlinks)):
        reasons.append(f"{path}: submodule human provenance is stale; target tree has no gitlink")
    for path in sorted(set(gitlinks) - set(section_by_path)):
        reasons.append(f"{path}: gitlink has no .gitmodules section")
    for path in sorted(set(section_by_path) - set(gitlinks)):
        reasons.append(f"{path}: .gitmodules section has no target-tree gitlink")
    for path in sorted(set(gitlinks) & set(metadata) & set(section_by_path)):
        if metadata[path]["url"] != section_by_path[path]:
            reasons.append(f"{path}: human provenance URL does not match .gitmodules")
    return sorted(set(reasons))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--target-sha", default="HEAD")
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    args = parser.parse_args(argv)
    try:
        target_sha = gs.resolve_sha(args.repo_root, args.target_sha)
        reasons = check_submodule_binding(args.repo_root, target_sha, args.metadata)
    except (SubmoduleBindingError, gs.GitSourceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"reasons": reasons}, indent=2, sort_keys=True))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
