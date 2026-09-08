"""A real report-only consumer of Make and generated-registry observations."""

from __future__ import annotations

import argparse
from pathlib import Path

from .authority import AuthorityLoader, ENVIRONMENT, GitlinkSource, encoded, git_tree_entries
from .budget import MakeProbeError, ProbeBudget
from .make_probe import Command, ProbeSession, TRUSTED_ROOT, probe_generated_registry


def registry_entries(root, revision, budget):
    entries = git_tree_entries(root, revision, budget=budget)
    gitlinks = []
    for name, entry in entries.items():
        if entry.mode != "160000":
            continue
        located = budget.run(
            ["/usr/bin/git", "-C", str(root), "rev-parse", "--git-common-dir"],
            env=ENVIRONMENT,
        )
        if located.returncode:
            raise MakeProbeError("registry root enumeration requires its captured gitlink database")
        directory = Path(located.stdout.decode("utf-8", "strict").strip())
        directory = directory if directory.is_absolute() else root / directory
        gitlinks.append(GitlinkSource(name, directory / "modules" / name))
    return git_tree_entries(root, revision, budget=budget, gitlinks=tuple(gitlinks)) if gitlinks else entries


def check(root: Path, revision: str | None):
    budget = ProbeBudget()
    entries = registry_entries(root, revision or "HEAD", budget)
    loader = AuthorityLoader(root, entries, revision, budget=budget)
    with ProbeSession(
        loader, scratch_root=root / "build/test-artifacts/ownership-probe", budget=budget,
    ) as session:
        make = session.make(
            "localization-check", makefile="localization.mk",
            variables=("LOCALIZATION_OUT_DIR",), owner_inputs=("localization.mk",),
        )
        code = tuple(sorted(
            path for path in session.snapshot.files
            if path.endswith(".py") and path.startswith(("scripts/generated_data/", "scripts/assets/"))
        ))
        directories = tuple(sorted({".", "src/data", *(
            parent.as_posix() for name in code for parent in Path(name).parents
        )}))
        registry = probe_generated_registry(loader, session=session, command=Command(
            ("/usr/bin/python3", "-I", "-S", "-B", "-c",
             (TRUSTED_ROOT / "generated_registry_probe.py").read_text(encoding="utf-8"),
             "chapterbundle", "src/data"),
            code=code, sources=("src/data/*_bundle.json",), directories=directories,
        ))
        return {
            "scope": "ownership-probe-foundation",
            "execution_snapshot": session.snapshot.digest,
            "make": {
                "target": make.target, "semantic_digest": make.semantic_digest,
                "semantics": make.semantics,
            },
            "generated_registry": registry,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--worktree", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = check(arguments.repository_root.resolve(strict=True),
                       None if arguments.worktree else arguments.revision)
    except (MakeProbeError, OSError) as error:
        parser.exit(1, f"ownership-probe-check: {error}\n")
    print(encoded(result).decode("ascii"))
    return 0
