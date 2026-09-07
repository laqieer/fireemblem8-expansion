"""One report lifetime for CURRENT, grouped BASE observations and lifecycle."""

from __future__ import annotations

from pathlib import Path

from .authority import AuthorityLoader, GitlinkSource, git, git_tree_entries
from .budget import ProbeBudget
from .graph_commands import ROOT_RUNTIME_FILES
from .make_probe import ProbeSession


def capture(root: Path, revision: str, budget: ProbeBudget, *, scratch_root=None):
    entries = git_tree_entries(root, revision, budget=budget)
    if "mgfembp" in entries and entries["mgfembp"].mode == "160000":
        common = git(root, budget, "rev-parse", "--path-format=absolute", "--git-common-dir")
        database = Path(common.decode("utf-8").strip()) / "modules/mgfembp"
        entries = git_tree_entries(root, revision, budget=budget, gitlinks=(
            GitlinkSource("mgfembp", database),
        ))
    return AuthorityLoader(root, entries, revision, scratch_root=scratch_root, budget=budget)


def inventory(loader: AuthorityLoader):
    return {
        name: entry for name, entry in loader.entries.items()
        if entry.git_dir is None or entry.mode == "160000"
    }


def documents(loader):
    from . import reporter

    return (
        loader.read_json(reporter.GRAPH_PATH, "ownership graph"),
        loader.read_json(reporter.SCHEMA_PATH, "ownership schema"),
        loader.read_json(reporter.PROBE_ORACLE_PATH, "ownership oracle"),
    )


def check(root, *, budget: ProbeBudget, revision="HEAD", base_revision=None, changed_paths=(),
          runtime_files=ROOT_RUNTIME_FILES, lifecycle=True):
    from . import reporter

    root = reporter.validate_repository_root(Path(root), budget=budget)
    loader = capture(root, revision, budget)
    base_loader = None if base_revision is None else capture(root, base_revision, budget)
    entries = inventory(loader)
    with ProbeSession(
        loader, scratch_root=root / "build/test-artifacts/validation-ownership",
        budget=budget, runtime_files=runtime_files,
    ) as session:
        before = reporter.repository_status(root, budget=budget)
        graph, schema, oracle = documents(loader)
        model = reporter.validate_graph(graph, schema, loader, entries, session=session)
        prior, base_model, base_entries = None, None, None
        if base_loader is not None:
            base_entries = inventory(base_loader)
            with session.select_view(base_loader):
                prior = reporter._prior_graph(base_loader)
                if prior is not None:
                    base_schema = base_loader.read_json(reporter.SCHEMA_PATH, "BASE ownership schema")
                    base_model = reporter.validate_graph(
                        prior, base_schema, base_loader, base_entries, session=session,
                    )
                elif any(path not in entries and path in base_entries for path in changed_paths):
                    base_model = reporter.introduction_base_model(
                        graph, base_loader, base_entries, changed_paths=changed_paths, session=session,
                    )
        changed_edges = set()
        if base_model is not None and prior is not None:
            changed_owners = {
                name for name in model["authorities"].keys() | base_model["authorities"].keys()
                if model["authorities"].get(name) != base_model["authorities"].get(name)
            }
            changed_edges = {
                edge["id"] for candidate in (graph, prior) for edge in candidate["edges"]
                if edge["target"] in changed_owners
            }
        result = reporter.build_report(
            graph, schema, oracle, loader, entries, changed_paths,
            prior_graph=prior, review_comparison_requested=base_revision is not None,
            authority_changed_edge_ids=changed_edges, base_entries=base_entries,
            model=model, session=session, base_model=base_model,
        )
        if lifecycle:
            result["artifact"]["executable_lifecycle"] = reporter.validate_executable_lifecycle(
                root, graph, session=session, schema=schema, oracle=oracle, model=model,
            )
        if reporter.repository_status(root, budget=budget) != before:
            raise reporter.OwnershipError("reporter changed the repository worktree")
        result["execution"] = {
            "revision": revision, "base_revision": base_revision,
            "runs": budget.runs, "states": budget.states, "bytes": dict(budget.bytes),
            "processes": session.processes_used, "live_process_peak": session.live_process_peak,
            "syscalls": session.syscalls_used,
        }
        return result
