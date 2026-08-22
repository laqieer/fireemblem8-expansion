#!/usr/bin/env python3
"""Release manifest and identity checks (issue #9).

Ties together config.mk's SemVer, the embedded C metadata contract
(include/expansion_metadata.h / include/save_format.h), a hypothetical
candidate tag string, the changelog, required docs, save-format
compatibility, migration declarations, provenance, and the source-release
guard into one machine report. Never creates a tag/ref -- ``candidate_tag``
is validated as text only. See docs/release_process.md.

Deliberately dependency-free (Python stdlib only); reuses
scripts/modernize/expansion_config.py rather than re-deriving version/
fingerprint logic.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize"))

import expansion_config as ec  # noqa: E402

from scripts.release_rehearsal import archive_rehearsal as ar  # noqa: E402
from scripts.release_rehearsal import candidate_tree as ct  # noqa: E402
from scripts.release_rehearsal import changelog as cl  # noqa: E402
from scripts.release_rehearsal import consistency as cc  # noqa: E402
from scripts.release_rehearsal import doc_links as dl  # noqa: E402
from scripts.release_rehearsal import epoch_claims as epc  # noqa: E402
from scripts.release_rehearsal import stale_count_claims as scc  # noqa: E402
from scripts.release_rehearsal import git_source as gs  # noqa: E402
from scripts.release_rehearsal import source_guard as sg  # noqa: E402
from scripts.release_rehearsal import submodule_binding as sb  # noqa: E402
from scripts.modernize.migrations import registry as migrations_registry  # noqa: E402

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CANDIDATE_TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHORT_SHA_LEN = 8

REQUIRED_DOCS = (
    "docs/release_process.md",
    "docs/public_api_policy.md",
    "docs/migration_registry.md",
    "docs/save_format.md",
    "docs/release_data/version_ledger.json",
)


class ManifestError(ValueError):
    """An actionable, well-formed input/consistency error -- distinct from
    the expected 'failed' business status."""


def _is_git_repo(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _verify_target_sha_is_exact_reachable_commit(repo_root: Path, target_sha: str) -> None:
    """Issue #9 trust-boundary fix (target-object exactness): an explicit
    ``--target-sha`` override, when `repo_root` actually is a git
    repository, must never be accepted as a bare, format-validated-only
    40-hex string -- it must be exactly a real, existing **commit**
    object, not a tree, a blob, or (the subtle case) an **annotated tag
    object's own SHA** (which peels successfully via ``^{commit}`` but
    names a *different* object than the commit it points at).

    Uses the same safe, argv-only git plumbing invocation
    (``git rev-parse --verify <sha>^{commit}``) that ordinary revision
    resolution already uses: for a tree/blob this fails outright (there
    is no commit to peel to at all -- reported as an actionable
    `ManifestError`, exactly like a missing/unreachable/fake SHA); for an
    annotated tag object it *succeeds* but resolves to the commit the tag
    points *at* -- a SHA different from the tag object's own -- so
    requiring the resolved SHA to equal the exact SHA supplied is what
    actually rejects "peelable" in favor of "is itself the commit".

    Finally, enforces the same target/HEAD reachability semantics
    already promised elsewhere in this module family (see
    `git_source.check_generation_basis_is_commit`'s identical "HEAD or a
    genuine ancestor of it" rule): an override naming a real, existing
    commit that nonetheless sits on an unrelated/off-branch/dangling
    history is still never a valid release target."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{target_sha}^{{commit}}"],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not FULL_SHA_RE.fullmatch(resolved):
        raise ManifestError(
            f"--target-sha {target_sha!r} does not resolve to an exact, existing commit object in "
            f"this repository (git rev-parse --verify {target_sha}^{{commit}} failed: "
            f"{result.stderr.strip()}) -- a missing/unreachable/fake SHA, or one naming a tree/blob "
            "(neither of which can ever be peeled to a commit), is never accepted"
        )
    if resolved != target_sha:
        raise ManifestError(
            f"--target-sha {target_sha!r} is not itself a commit object -- it resolves (peels) to a "
            f"different commit {resolved!r}. This is exactly what an annotated tag object's own SHA "
            "does: a real, valid, peelable object, but never itself the commit. Supply the exact "
            "40-lowercase-hex commit SHA itself, never an annotated tag object's SHA"
        )
    if not gs.is_ancestor_commit(repo_root, target_sha, "HEAD"):
        raise ManifestError(
            f"--target-sha {target_sha!r} is a real, existing commit object in this repository, but "
            "is not HEAD nor any ancestor of it -- an unreachable/off-branch/dangling commit (one a "
            "future 'git gc' could prune, or one that never belongs to this branch's own history at "
            "all) can never be a valid release target"
        )


def resolve_target_sha(repo_root: Path, override: Optional[str]) -> str:
    repo_root = Path(repo_root)
    if override is not None:
        if not FULL_SHA_RE.fullmatch(override):
            raise ManifestError(
                f"--target-sha {override!r} must be an exact 40-lowercase-hex commit SHA"
            )
        if _is_git_repo(repo_root):
            # A real git repository can, and must, actually verify this
            # exact-commit-object claim locally -- see
            # `_verify_target_sha_is_exact_reachable_commit` above.
            _verify_target_sha_is_exact_reachable_commit(repo_root, override)
        # else: a non-git materialization (a genuine extracted archive/
        # non-git candidate tree) has no local object database to
        # resolve/verify anything against at all (issue #9 trust-
        # boundary fix A3) -- this exact 40-hex override is accepted
        # purely as an externally-supplied, externally-attested
        # exact-commit binding; this module never claims (and must
        # never be read as claiming) any local object verification for
        # it. See `check_release_tag_authority_non_git`'s own required,
        # separately-bound external attestation, which is the only
        # mechanism that can actually corroborate this binding for a
        # non-git candidate.
        return override
    if not _is_git_repo(repo_root):
        raise ManifestError(
            "no .git metadata found (an archive or non-git tree); an explicit "
            "--target-sha (exact 40-lowercase-hex) override is required"
        )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not FULL_SHA_RE.fullmatch(sha):
        raise ManifestError(
            "git rev-parse HEAD did not return a clean 40-lowercase-hex SHA; "
            "pass an explicit --target-sha override"
        )
    return sha


def derive_short_sha(target_sha: str) -> str:
    """Mirrors scripts/modernize/save_format_tool.py's
    build_commit[:8] short-form derivation used for the on-media
    ExpansionSaveMeta.buildCommitShort diagnostic field."""
    return target_sha[:SHORT_SHA_LEN]


SHORT_SHA_RE = re.compile(r"^[0-9a-f]{8}$")


def verify_short_sha(target_sha: str, embedded_short: str) -> None:
    """Mandatory embedded short-SHA verification for a release candidate:
    a missing/malformed/wrong-length/wrong-case value is rejected with an
    actionable, distinct message (never merely "did not match"), and the
    fixed sentinel "unknown" (scripts/modernize/expansion_config.py's own
    no-git fallback) is exactly as unacceptable as any other malformed
    value here -- a release candidate manifest must never accept an
    unresolved build identity."""
    if not isinstance(embedded_short, str) or not SHORT_SHA_RE.fullmatch(embedded_short):
        raise ManifestError(
            f"embedded short-form build commit {embedded_short!r} must be exactly "
            f"{SHORT_SHA_LEN} lowercase hex characters (e.g. not 'unknown', not missing, "
            "not wrong length/case)"
        )
    expected = derive_short_sha(target_sha)
    if embedded_short != expected:
        raise ManifestError(
            f"embedded short-form build commit {embedded_short!r} does not match "
            f"the first {SHORT_SHA_LEN} hex characters of the full target SHA "
            f"{target_sha!r} ({expected!r})"
        )


def build_candidate_tag(version_string: str) -> str:
    tag = f"v{version_string}"
    if not CANDIDATE_TAG_RE.fullmatch(tag):
        raise ManifestError(f"candidate tag text {tag!r} is not a valid vMAJOR.MINOR.PATCH tag")
    return tag


def check_required_docs(repo_root: Path) -> List[str]:
    return sorted(str(doc) for doc in REQUIRED_DOCS if not (repo_root / doc).is_file())


def check_changelog(repo_root: Path) -> Dict:
    ok, errors, rendered, impact = cl.check(
        repo_root / "changelog_fragments", repo_root / "CHANGELOG.md"
    )
    return {"ok": ok, "errors": errors, "aggregate_impact": impact}


def check_tree_coverage(repo_root: Path, target_sha: str) -> Dict:
    """Load the exact path/mode/gitlink set from the immutable candidate."""
    try:
        tree = ct.load(repo_root, target_sha)
    except ct.CandidateTreeError as error:
        raise ManifestError(str(error)) from error
    return {"ok": True, "errors": [], "modes": tree.modes}



def check_source_guard(repo_root: Path, target_sha: Optional[str] = None) -> Dict:
    """Evaluates the actual source-release candidate set for `repo_root`,
    consistent with scripts/release_rehearsal/archive_rehearsal.py: a git
    working tree is scanned as its tracked-files-intersected-with-the-
    allowlist candidate set (so gitignored/untracked build byproducts --
    .dep/ output, a built ROM/ELF, host tool binaries, etc. -- sitting in
    a live development worktree can never change this report purely
    because of host/build state), while a genuine extracted archive or
    other non-git candidate tree is still scanned closed-world and fails
    closed (see sg.scan_source_release_candidate)."""
    map_hex_exceptions_path = repo_root / "docs" / "release_data" / "map_hex_exceptions.json"
    try:
        if gs.is_git_repo(repo_root):
            try:
                resolved_target_sha = target_sha or gs.resolve_sha(repo_root, "HEAD")
            except gs.GitSourceError:
                resolved_target_sha = gs.write_index_tree(repo_root)
            source_paths = ct.load(repo_root, resolved_target_sha).source_paths
        else:
            source_paths = tuple(
                path.relative_to(repo_root).as_posix()
                for path in repo_root.rglob("*")
                if path.is_file()
            )
        map_hex_exceptions = (
            sg.load_map_hex_exceptions(map_hex_exceptions_path)
            if map_hex_exceptions_path.is_file() else frozenset()
        )
        violations = sg.scan_source_release_candidate(
            repo_root, source_paths, map_hex_exceptions
        )
    except (sg.SourceGuardError, ct.CandidateTreeError) as error:
        raise ManifestError(str(error)) from error
    return {
        "passed": not violations,
        "violations": [f"{path}: {rule}" for path, rule in violations],
    }


def check_allowlist_exact(repo_root: Path, target_sha: str) -> Dict:
    """Compatibility entry point for exact candidate-tree membership."""
    try:
        tree = ct.load(repo_root, target_sha)
    except ct.CandidateTreeError as error:
        raise ManifestError(str(error)) from error
    return {"ok": True, "errors": [], "modes": tree.modes}



def check_submodule_binding(repo_root: Path, target_sha: str) -> Dict:
    """mgfembp submodule three-way binding (issue #9 mandatory
    correction #4): cross-checks `.gitmodules`, the immutable HEAD tree
    gitlink, the export-exclusion record, and the submodule provenance
    record all agree exactly on path/URL/pinned-commit -- see
    `scripts/release_rehearsal/submodule_binding.py`. Any finding here
    forces the overall candidate status to "failed", exactly like every
    other sub-check."""
    try:
        errors = sb.check_submodule_binding(repo_root, target_sha)
    except sb.SubmoduleBindingError as error:
        raise ManifestError(str(error)) from error
    return {"ok": not errors, "errors": errors}


def check_migrations() -> Dict:
    errors = migrations_registry.check_registry()
    return {"ok": not errors, "errors": errors}


def check_allowlist(repo_root: Path, target_sha: str) -> Dict:
    return check_allowlist_exact(repo_root, target_sha)


def check_version_ledger_and_semver(
    repo_root: Path,
    target_sha: str,
    identity,
    changelog_report: Dict,
    release_tag_attestation_path: Optional[Path] = None,
) -> Dict:
    """Folds together the version-ledger topology/candidate-agreement
    check, the changelog-declared-impact-vs-actual-delta check, and the
    immutable-annotated-release-tag SemVer-predecessor authority
    cross-check (issue #9 SemVer trust-boundary fix -- see
    scripts/release_rehearsal/consistency.py's
    `check_release_tag_authority`/`check_release_tag_authority_non_git`)
    into one report, since all three read/cross-check the same ledger
    file's declared `previous_supported_version` claim.

    The ledger's own `previous_supported_version` is never itself
    authoritative: in a real git repository, it is cross-checked against
    this repository's actual, immutable, annotated
    `expansion/MAJOR.MINOR.PATCH` release-tag history; for a genuine
    non-git/archive candidate tree (no local tag history exists at all),
    an explicit, external, protected release-history attestation file
    (`release_tag_attestation_path`) bound to this exact `target_sha` and
    `identity.version_string` is required instead -- a missing one is
    its own explicit, actionable finding, never a silently-fabricated
    empty release history."""
    ledger_path = repo_root / "docs" / "release_data" / "version_ledger.json"
    if not ledger_path.is_file():
        return {"ok": False, "errors": [f"{ledger_path} not found"], "ledger": {}}
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"ok": False, "errors": [f"{ledger_path}: not valid JSON: {error}"], "ledger": {}}

    errors = cc.check_version_ledger(ledger, identity.version_string)
    errors += cc.check_changelog_semver_delta(
        ledger.get("previous_supported_version"),
        identity.version_string,
        changelog_report.get("aggregate_impact", "none"),
        identity.version_major,
    )
    if gs.is_git_repo(repo_root):
        errors += cc.check_release_tag_authority(
            repo_root, target_sha, identity.version_string,
            ledger.get("previous_supported_version"),
        )
    else:
        errors += cc.check_release_tag_authority_non_git(
            release_tag_attestation_path, target_sha, identity.version_string,
            ledger.get("previous_supported_version"),
        )
    return {"ok": not errors, "errors": errors, "ledger": ledger}


def check_c_fallback(repo_root: Path) -> Dict:
    try:
        config_values = ec.parse_config_mk(repo_root / "config.mk")
    except ec.ConfigError as error:
        return {"ok": False, "errors": [str(error)]}
    errors = cc.check_c_fallback_metadata(repo_root, config_values)
    return {"ok": not errors, "errors": errors}


def check_migration_reachability(save_compat_epoch: int) -> Dict:
    errors = cc.check_migration_epoch_reachability(save_compat_epoch, migrations_registry.registry())
    return {"ok": not errors, "errors": errors}


def check_doc_links(repo_root: Path) -> Dict:
    broken = dl.find_broken_links(repo_root)
    errors = [f"{doc}: broken link -> {target}" for doc, target in broken]
    return {"ok": not errors, "errors": errors}


def check_epoch_claims(repo_root: Path) -> Dict:
    """issue #9 verifier remediation: scans release docs/headers for a
    stale *current-state* EXPANSION_SAVE_COMPAT_EPOCH claim (e.g. the
    known "epoch stays 1" falsehood after a later commit actually bumped
    it) -- see scripts/release_rehearsal/epoch_claims.py. A legitimate
    historical migration statement ("bumped 1 -> 2") never matches this
    check's pattern at all, so it is never flagged."""
    return epc.check(repo_root)


def check_stale_count_claims(repo_root: Path) -> Dict:
    """issue #9 verifier remediation: scans release closure evidence/docs
    for a hardcoded aggregate test-count claim (e.g. the known "(860
    tests)"/"Ran 860 tests" falsehood that goes stale the moment a test
    is added/renamed) -- see
    scripts/release_rehearsal/stale_count_claims.py. A legitimate small
    semantic constant or migration delta is never flagged."""
    return scc.check(repo_root)


def check_embedded_identity_binding(target_sha: str, embedded_short_sha: Optional[str]) -> Dict:
    """issue #9 verifier remediation: a release candidate's build-identity
    binding (the embedded short-form build commit -- see
    verify_short_sha()/derive_short_sha() above) must never be an
    optional/conditional fact on a publication-eligibility or rehearsal
    path. Unlike a malformed/mismatched *supplied* value (which
    verify_short_sha() itself already rejects as an actionable
    ManifestError, well before this function is ever reached), a simply
    *missing* embedded_short_sha (None -- nobody supplied one at all) is
    not a tooling error; it is an honest, unresolved fact this function
    turns into its own always-present, never-mockable-away reason, so a
    candidate cannot pass this check while its build-identity binding to
    `target_sha` was never actually verified
    against a real embedded artifact."""
    if embedded_short_sha is None:
        return {
            "ok": False,
            "reasons": [
                "no embedded short-form build commit was supplied/verified for this "
                f"candidate (target SHA {target_sha!r}); a release candidate's build-identity "
                "binding can never be certified without verifying it against a real embedded "
                "artifact (see --embedded-short-sha / the rehearsal rebuild's own automatic "
                "embedded-metadata verification)"
            ],
        }
    return {"ok": True, "reasons": []}


def check_rebuild(
    repo_root: Path,
    target_sha: str,
    attempt_build: bool = False,
    build_command: Optional[List[str]] = None,
    output_relpaths: Optional[List[str]] = None,
    precomputed_rebuild_report: Optional[Dict] = None,
) -> Dict:
    """Folds scripts/release_rehearsal/archive_rehearsal.py's rebuild
    rehearsal into the manifest. `attempt_build` defaults to False here
    (a fast eligibility-only check suitable for every `make release-check`
    run) -- eligibility (submodule initialized/approved/identity-matched)
    is still always evaluated; only the actual, potentially-heavy double
    compile-and-compare is opt-in. Never report a clean technical result while
    this reports anything other than `REBUILD_STATUS_VERIFIED_SUCCESS`
    (see build_manifest below) -- a failed/not-run/failed rebuild always
    forces the overall candidate status to "failed".

    `target_sha` is always threaded through explicitly (issue #9 verifier
    remediation) -- never left for `rebuild_rehearsal_blocker` to
    independently re-resolve via its own `gs.resolve_sha(repo_root,
    "HEAD")` fallback, which could otherwise silently diverge from this
    exact manifest's own already-resolved, possibly-overridden identity
    (e.g. a historical-replay `--target-sha` against a real git repo
    whose live HEAD differs).

    `precomputed_rebuild_report`, if given, is returned as-is (never
    re-invoking `rebuild_rehearsal_blocker` a second time) -- this is
    what lets `cli.py`'s `cmd_rehearse` run the real, potentially-heavy
    double build exactly **once** and still fold its exact result into
    this same manifest (rather than this function independently
    re-running it, which would either double the real build work or
    silently disagree with an already-computed outer result)."""
    if precomputed_rebuild_report is not None:
        return precomputed_rebuild_report
    return ar.rebuild_rehearsal_blocker(
        repo_root, attempt_build=attempt_build,
        build_command=build_command, output_relpaths=output_relpaths,
        target_sha=target_sha,
    )


def build_manifest(
    repo_root: Path,
    config_preset: str,
    abi: str,
    rom_size: str,
    target_sha_override: Optional[str] = None,
    embedded_short_sha: Optional[str] = None,
    attempt_rebuild_build: bool = False,
    rebuild_build_command: Optional[List[str]] = None,
    rebuild_output_relpaths: Optional[List[str]] = None,
    precomputed_rebuild_report: Optional[Dict] = None,
    release_tag_attestation_path: Optional[Path] = None,
) -> Dict:
    repo_root = Path(repo_root)
    # Resolve the exact, immutable target SHA *first* (this is the single
    # source of truth for this candidate's identity: an explicit
    # `--target-sha` override in non-git/archive mode, or the actual
    # repository's own resolved HEAD when `repo_root` is a real git root
    # -- see resolve_target_sha). It is then threaded into
    # ec.load_identity() as the build-id override so the embedded
    # `identity.build_commit` is always bound to this exact, already-
    # validated value: never a second, independent `git rev-parse` call
    # against `repo_root` (which -- for a non-git extracted tree nested
    # inside an unrelated outer repository -- could otherwise silently
    # adopt that outer repository's HEAD via git's own upward directory
    # discovery), and never the "unknown" sentinel, which would discard
    # the exact identity the non-git/archive path specifically requires
    # (issue #9 remediation).
    target_sha = resolve_target_sha(repo_root, target_sha_override)
    identity = ec.load_identity(
        config_mk_path=repo_root / "config.mk",
        config_preset=config_preset,
        abi=abi,
        rom_size=rom_size,
        repo_root=repo_root,
        build_id_override=target_sha,
    )
    # issue #9 verifier remediation: a *supplied-but-malformed/mismatched*
    # embedded_short_sha is still an actionable tooling error (raised
    # here, exactly as before); a simply *missing* one is instead folded
    # into `identity_binding_report` below as an always-present, never-
    # optional blocking reason -- never silently skipped.
    if embedded_short_sha is not None:
        verify_short_sha(target_sha, embedded_short_sha)
    identity_binding_report = check_embedded_identity_binding(target_sha, embedded_short_sha)

    candidate_tag = build_candidate_tag(identity.version_string)
    missing_docs = check_required_docs(repo_root)
    changelog_report = check_changelog(repo_root)
    source_guard_report = check_source_guard(repo_root, target_sha)
    migrations_report = check_migrations()
    if gs.is_git_repo(repo_root):
        candidate_tree_report = check_tree_coverage(repo_root, target_sha)
    else:
        candidate_tree_report = {
            "ok": True,
            "errors": [],
            "note": "non-git candidate membership is validated by the closed-world source guard",
        }
    submodule_binding_report = check_submodule_binding(repo_root, target_sha)
    ledger_report = check_version_ledger_and_semver(
        repo_root, target_sha, identity, changelog_report,
        release_tag_attestation_path=release_tag_attestation_path,
    )
    c_fallback_report = check_c_fallback(repo_root)
    migration_reachability_report = check_migration_reachability(identity.save_compat_epoch)
    epoch_claims_report = check_epoch_claims(repo_root)
    stale_count_claims_report = check_stale_count_claims(repo_root)
    doc_links_report = check_doc_links(repo_root)
    rebuild_report = check_rebuild(
        repo_root, target_sha, attempt_build=attempt_rebuild_build,
        build_command=rebuild_build_command, output_relpaths=rebuild_output_relpaths,
        precomputed_rebuild_report=precomputed_rebuild_report,
    )

    ledger = ledger_report["ledger"]

    reasons: List[str] = []
    if missing_docs:
        reasons.append(f"missing required doc(s): {', '.join(missing_docs)}")
    if not changelog_report["ok"]:
        reasons.extend(changelog_report["errors"])
    if not source_guard_report["passed"]:
        reasons.extend(source_guard_report["violations"])
    if not migrations_report["ok"]:
        reasons.extend(migrations_report["errors"])
    if not candidate_tree_report["ok"]:
        reasons.extend(candidate_tree_report["errors"])
    if not submodule_binding_report["ok"]:
        reasons.extend(submodule_binding_report["errors"])
    if not ledger_report["ok"]:
        reasons.extend(ledger_report["errors"])
    if not c_fallback_report["ok"]:
        reasons.extend(c_fallback_report["errors"])
    if not migration_reachability_report["ok"]:
        reasons.extend(migration_reachability_report["errors"])
    if not doc_links_report["ok"]:
        reasons.extend(doc_links_report["errors"])
    if not epoch_claims_report["ok"]:
        reasons.extend(epoch_claims_report["errors"])
    if not stale_count_claims_report["ok"]:
        reasons.extend(stale_count_claims_report["errors"])
    if embedded_short_sha is not None and not identity_binding_report["ok"]:
        reasons.extend(identity_binding_report["reasons"])
    if not rebuild_report["passed"]:
        reasons.extend(
            rebuild_report.get("reasons")
            or ["rebuild check failed"]
        )

    return {
        "version_string": identity.version_string,
        "version_packed": identity.version_packed,
        "candidate_tag": candidate_tag,
        "target_sha": target_sha,
        "target_sha_short": derive_short_sha(target_sha),
        "config_fingerprint": identity.config_fingerprint,
        "save_compat_epoch": identity.save_compat_epoch,
        "previous_supported_version": ledger.get("previous_supported_version"),
        "next_supported_version": ledger.get("next_supported_version"),
        "docs": {"missing": missing_docs},
        "changelog": changelog_report,
        "source_guard": source_guard_report,
        "migrations": migrations_report,
        "candidate_tree": candidate_tree_report,
        "submodule_binding": submodule_binding_report,
        "version_ledger": ledger_report,
        "c_fallback_metadata": c_fallback_report,
        "migration_reachability": migration_reachability_report,
        "doc_links": doc_links_report,
        "epoch_claims": epoch_claims_report,
        "stale_count_claims": stale_count_claims_report,
        "identity_binding": identity_binding_report,
        "embedded_short_sha": embedded_short_sha,
        "rebuild": rebuild_report,
        "reasons": reasons,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--config", default="release", choices=("debug", "release"))
    parser.add_argument("--abi", default="aapcs", choices=("aapcs", "apcs-gnu"))
    parser.add_argument("--rom-size", default="16M")
    parser.add_argument("--target-sha", default=None)
    parser.add_argument("--embedded-short-sha", default=None)
    parser.add_argument(
        "--release-tag-attestation", type=Path, default=None,
        help="path to an external, protected release-history attestation JSON file, required "
             "only for a non-git --repo-root (a genuine extracted archive/non-git candidate "
             "tree) -- see consistency.check_release_tag_authority_non_git",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_manifest(
            args.repo_root,
            args.config,
            args.abi,
            args.rom_size,
            target_sha_override=args.target_sha,
            embedded_short_sha=args.embedded_short_sha,
            release_tag_attestation_path=args.release_tag_attestation,
        )
    except (ManifestError, ec.ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
