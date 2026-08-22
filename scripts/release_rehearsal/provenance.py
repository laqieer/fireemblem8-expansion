#!/usr/bin/env python3
"""Human provenance metadata bound to an immutable candidate tree.

The committed metadata records human facts for exact paths only. It contains
no commit, blob, object, ROM, or content hashes: Git's immutable target tree
provides member paths, modes, and gitlinks at verification time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping

from scripts.release_rehearsal import candidate_tree as ct
from scripts.release_rehearsal import git_source as gs

SCHEMA_VERSION = 1
DEFAULT_METADATA_PATH = Path("docs/release_data/provenance.json")
CATEGORIES = frozenset(("code", "asset", "submodule"))
UNRESOLVED_MARKERS = ("NOASSERTION", "", None)
FORBIDDEN_KEYS = frozenset(
    ("generation_basis_sha", "oid", "blob_oid", "sha256", "pinned_commit", "commit")
)


class ProvenanceError(ValueError):
    """A provenance document is malformed or contradicts the target tree."""


def _load_json(path: Path) -> Dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"{path}: not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ProvenanceError(f"{path}: top-level JSON value must be an object")
    return data


def load_metadata(path: Path) -> List[Dict]:
    """Loads exact-path human facts without accepting identity snapshots."""
    data = _load_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceError(
            f"{path}: schema_version must be exactly {SCHEMA_VERSION}, found {data.get('schema_version')!r}"
        )
    forbidden = sorted(FORBIDDEN_KEYS & set(data))
    if forbidden:
        raise ProvenanceError(f"{path}: forbidden identity field(s): {', '.join(forbidden)}")

    facts = data.get("facts")
    raw_entries = data.get("entries")
    if not isinstance(facts, dict) or not facts:
        raise ProvenanceError(f"{path}: facts must be a non-empty object")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ProvenanceError(f"{path}: entries must be a non-empty array")

    normalized_facts: Dict[str, Dict] = {}
    for fact_id, fact in facts.items():
        if not isinstance(fact_id, str) or not fact_id:
            raise ProvenanceError(f"{path}: fact identifiers must be non-empty strings")
        if not isinstance(fact, dict):
            raise ProvenanceError(f"{path}.facts[{fact_id!r}] must be an object")
        forbidden = sorted(FORBIDDEN_KEYS & set(fact))
        if forbidden:
            raise ProvenanceError(
                f"{path}.facts[{fact_id!r}]: forbidden identity field(s): {', '.join(forbidden)}"
            )
        required = (
            "category", "author", "rightsholder", "license",
            "redistribution_approved", "reviewer", "notes",
        )
        missing = [field for field in required if field not in fact]
        if missing:
            raise ProvenanceError(
                f"{path}.facts[{fact_id!r}]: missing required field(s): {', '.join(missing)}"
            )
        if fact["category"] not in CATEGORIES:
            raise ProvenanceError(
                f"{path}.facts[{fact_id!r}]: category {fact['category']!r} is not supported"
            )
        if not isinstance(fact["redistribution_approved"], bool):
            raise ProvenanceError(
                f"{path}.facts[{fact_id!r}]: redistribution_approved must be a boolean"
            )
        if not isinstance(fact["notes"], str) or not fact["notes"].strip():
            raise ProvenanceError(f"{path}.facts[{fact_id!r}]: notes must be a non-empty string")
        if fact["category"] == "submodule":
            if not isinstance(fact.get("url"), str) or not fact["url"].startswith("https://"):
                raise ProvenanceError(
                    f"{path}.facts[{fact_id!r}]: submodule facts require an https:// url"
                )
        elif "url" in fact:
            raise ProvenanceError(f"{path}.facts[{fact_id!r}]: only submodule facts may contain url")
        normalized_facts[fact_id] = dict(fact)

    entries: List[Dict] = []
    seen_paths = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or set(raw) != {"path", "fact"}:
            raise ProvenanceError(f"{path}.entries[{index}] must contain exactly path and fact")
        entry_path = raw["path"]
        fact_id = raw["fact"]
        if not isinstance(entry_path, str) or not entry_path or entry_path.startswith("/"):
            raise ProvenanceError(f"{path}.entries[{index}].path must be a relative non-empty path")
        if entry_path in seen_paths:
            raise ProvenanceError(f"{path}: duplicate exact-path metadata for {entry_path!r}")
        if fact_id not in normalized_facts:
            raise ProvenanceError(f"{path}.entries[{index}]: unknown fact {fact_id!r}")
        seen_paths.add(entry_path)
        entries.append({"path": entry_path, "fact": fact_id, **normalized_facts[fact_id]})
    return sorted(entries, key=lambda entry: entry["path"])


def evaluate(entries: Iterable[Mapping]) -> tuple[str, List[str]]:
    reasons: List[str] = []
    for entry in entries:
        path = entry["path"]
        if entry["author"] in UNRESOLVED_MARKERS:
            reasons.append(f"{path}: author is NOASSERTION/unrecorded")
        if entry["rightsholder"] in UNRESOLVED_MARKERS:
            reasons.append(f"{path}: rightsholder is NOASSERTION/unrecorded")
        if entry["license"] in UNRESOLVED_MARKERS:
            reasons.append(f"{path}: license is NOASSERTION/unrecorded")
        if not entry["redistribution_approved"]:
            reasons.append(f"{path}: redistribution_approved is false")
        if not entry["reviewer"]:
            reasons.append(f"{path}: no named reviewer")
    return ("blocked" if reasons else "mechanically eligible", sorted(reasons))


def check_candidate_tree(entries: Iterable[Mapping], tree: ct.CandidateTree) -> List[str]:
    """Fail closed when human metadata and immutable tree membership diverge."""
    by_path = {entry["path"]: entry for entry in entries}
    tree_by_path = {entry.path: entry for entry in tree.entries}
    reasons = [
        f"missing human provenance metadata for {path}"
        for path in sorted(set(tree_by_path) - set(by_path))
    ]
    reasons.extend(
        f"stale human provenance metadata for {path}"
        for path in sorted(set(by_path) - set(tree_by_path))
    )
    for path in sorted(set(by_path) & set(tree_by_path)):
        entry = by_path[path]
        tree_entry = tree_by_path[path]
        expected_category = "submodule" if tree_entry.is_gitlink else entry["category"]
        if tree_entry.is_gitlink and entry["category"] != "submodule":
            reasons.append(f"{path}: gitlink requires submodule human provenance metadata")
        if not tree_entry.is_gitlink and entry["category"] == "submodule":
            reasons.append(f"{path}: non-gitlink cannot use submodule human provenance metadata")
        if expected_category not in CATEGORIES:
            reasons.append(f"{path}: unsupported category {expected_category!r}")
    return sorted(reasons)


def check_submodule_urls(entries: Iterable[Mapping], repo_root: Path, target_sha: str) -> List[str]:
    """Cross-check only the durable human URL fact; pins come from the tree."""
    submodule_entries = [entry for entry in entries if entry["category"] == "submodule"]
    if not submodule_entries or not gs.is_git_repo(repo_root):
        return []
    from scripts.release_rehearsal import gitmodules as gm

    try:
        sections = gm.load_gitmodules_sections(repo_root, target_sha)
    except gm.GitmodulesError as error:
        return [str(error)]
    by_path = {}
    for section in sections.values():
        path = section.get("path")
        if path in by_path:
            return [f".gitmodules has duplicate sections for {path!r}"]
        by_path[path] = section.get("url")
    reasons = []
    for entry in submodule_entries:
        if by_path.get(entry["path"]) != entry["url"]:
            reasons.append(
                f"{entry['path']}: human provenance URL does not match .gitmodules at {target_sha}"
            )
    return sorted(reasons)


def check(repo_root: Path, target_sha: str, metadata_path: Path = DEFAULT_METADATA_PATH) -> Dict:
    entries = load_metadata(metadata_path)
    status, reasons = evaluate(entries)
    if gs.is_git_repo(repo_root):
        tree = ct.load(repo_root, target_sha)
        reasons.extend(check_candidate_tree(entries, tree))
        reasons.extend(check_submodule_urls(entries, repo_root, target_sha))
        tree_paths = len(tree.entries)
        gitlink_paths = [entry.path for entry in tree.gitlink_entries]
    else:
        tree_paths = None
        gitlink_paths = []
    return {
        "status": "blocked" if reasons else status,
        "reasons": sorted(set(reasons)),
        "metadata_paths": len(entries),
        "tree_paths": tree_paths,
        "gitlink_paths": gitlink_paths,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", nargs="?")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--target-sha",
        default="HEAD",
        help="commit-ish, or 'index' for a local immutable staged-tree rehearsal",
    )
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    args = parser.parse_args(argv)
    try:
        target_sha = (
            gs.write_index_tree(args.repo_root)
            if args.target_sha == "index"
            else args.target_sha
        )
        report = check(args.repo_root, target_sha, args.metadata)
    except (ProvenanceError, ct.CandidateTreeError, gs.GitSourceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
