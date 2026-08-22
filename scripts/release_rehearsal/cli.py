#!/usr/bin/env python3
"""Read-only technical release checks and deterministic archive rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize"))

import expansion_config as ec  # noqa: E402

from scripts.release_rehearsal import archive_rehearsal as ar  # noqa: E402
from scripts.release_rehearsal import candidate_tree as ct  # noqa: E402
from scripts.release_rehearsal import git_source as gs  # noqa: E402
from scripts.release_rehearsal import manifest as rm  # noqa: E402
from scripts.release_rehearsal import source_guard as sg  # noqa: E402

EXIT_OK = 0
EXIT_TECHNICAL_FAILURE = 1
EXIT_TOOLING_ERROR = 2

EXPECTED_ERRORS = (
    rm.ManifestError,
    ec.ConfigError,
    ar.ArchiveRehearsalError,
    ct.CandidateTreeError,
    gs.GitSourceError,
    sg.SourceGuardError,
    OSError,
)


def _technical_failures(report: dict) -> list[str]:
    return list(report.get("reasons", ()))


def _render_summary(report: dict) -> str:
    lines = ["## Deterministic Archive Check", ""]
    failures = _technical_failures(report)
    lines.append(
        "**Technical checks:** " + ("passed" if not failures else f"failed ({len(failures)})")
    )
    lines.append("")
    for reason in failures[:25]:
        lines.append(f"- {reason}")
    if len(failures) > 25:
        lines.append(f"- ... and {len(failures) - 25} more")
    if failures:
        lines.append("")
    rows = []
    for key, value in report.items():
        if isinstance(value, dict) and "ok" in value:
            rows.append((key, bool(value["ok"])))
        elif key == "source_guard":
            rows.append((key, bool(value.get("passed"))))
        elif key == "rebuild":
            rows.append((key, bool(value.get("passed"))))
        elif key == "archive":
            rows.append((key, bool(value.get("match"))))
    if rows:
        lines.extend(["| Check | Result |", "|---|---|"])
        lines.extend(f"| `{key}` | {'✅' if ok else '❌'} |" for key, ok in rows)
        lines.append("")
    lines.append(
        "This workflow performs no publishing side effects: it creates no tag, release, upload, "
        "comment, or protected-environment mutation."
    )
    return "\n".join(lines) + "\n"


def _manifest(args):
    return rm.build_manifest(
        args.repo_root,
        args.config,
        args.abi,
        args.rom_size,
        target_sha_override=args.target_sha,
        embedded_short_sha=args.embedded_short_sha,
        release_tag_attestation_path=args.release_tag_attestation,
    )


def cmd_check(args) -> int:
    report = _manifest(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    failures = _technical_failures(report)
    if failures:
        for reason in failures:
            print(f"release-check: {reason}", file=sys.stderr)
        return EXIT_TECHNICAL_FAILURE
    return EXIT_OK


def cmd_summary(args) -> int:
    report = _manifest(args)
    sys.stdout.write(_render_summary(report))
    return EXIT_TECHNICAL_FAILURE if _technical_failures(report) else EXIT_OK


def cmd_candidate_tree(args) -> int:
    target_sha = rm.resolve_target_sha(args.repo_root, args.target_sha)
    if not gs.is_git_repo(args.repo_root):
        raise rm.ManifestError("candidate-tree check requires a Git repository")
    tree = ct.load(args.repo_root, target_sha)
    report = {
        "target_sha": target_sha,
        "entry_count": len(tree.entries),
        "source_count": len(tree.source_entries),
        "gitlinks": [
            {
                "path": entry.path,
                "mode": entry.mode,
                "commit": entry.object_id,
            }
            for entry in tree.gitlink_entries
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_rehearse(args) -> int:
    target_sha = rm.resolve_target_sha(args.repo_root, args.target_sha)
    if not gs.is_git_repo(args.repo_root):
        raise rm.ManifestError("archive rehearsal requires a Git candidate tree")
    tree = ct.load(args.repo_root, target_sha)
    exceptions_path = args.repo_root / "docs" / "release_data" / "map_hex_exceptions.json"
    exceptions = sg.load_map_hex_exceptions(exceptions_path) if exceptions_path.is_file() else frozenset()
    archive = ar.rehearse_archive_twice(
        args.repo_root, tree.source_paths, target_sha=target_sha, map_hex_exceptions=exceptions
    )
    report = _manifest(args)
    report["archive"] = archive
    failures = _technical_failures(report)
    if not archive["match"]:
        mismatch = "deterministic archive hashes differ"
        failures.append(mismatch)
        report.setdefault("reasons", []).append(mismatch)
    print(json.dumps(report, indent=2, sort_keys=True))
    for reason in failures:
        print(f"release-rehearse: {reason}", file=sys.stderr)
    return EXIT_TECHNICAL_FAILURE if failures else EXIT_OK


def _add_common(parser) -> None:
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--config", default="release", choices=("debug", "release"))
    parser.add_argument("--abi", default="aapcs", choices=("aapcs", "apcs-gnu"))
    parser.add_argument("--rom-size", default="16M")
    parser.add_argument("--target-sha", default=None)
    parser.add_argument("--embedded-short-sha", default=None)
    parser.add_argument("--release-tag-attestation", type=Path, default=None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidate-tree", "check", "rehearse", "summary"):
        command = sub.add_parser(name)
        _add_common(command)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return cmd_check(args)
        if args.command == "candidate-tree":
            return cmd_candidate_tree(args)
        if args.command == "rehearse":
            return cmd_rehearse(args)
        if args.command == "summary":
            return cmd_summary(args)
    except EXPECTED_ERRORS as error:
        print(f"{args.command}: error: {error}", file=sys.stderr)
        return EXIT_TOOLING_ERROR


if __name__ == "__main__":
    sys.exit(main())
