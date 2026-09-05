"""Generated-source discovery by admitted-filesystem confinement."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.sandbox import (
    PYTHON,
    ExecutionSnapshot,
    Mount,
    ProbeSandboxError,
    SandboxRunner,
    strict_utf8,
)


class SourceProbeError(ProbeSandboxError):
    """Raised when generated-source authority is incomplete or unconfined."""


def _canonical_path(path: str, label: str) -> str:
    candidate = Path(path)
    if (
        not path
        or candidate.is_absolute()
        or candidate.as_posix() != path
        or ".." in candidate.parts
    ):
        raise SourceProbeError(f"{label} must be a canonical relative path")
    return path


@dataclass(frozen=True)
class SourceContract:
    """Trusted graph/schema declaration, independent of candidate output."""

    source_root: str | None
    source_pattern: str | None
    exact_sources: tuple[str, ...]
    metadata: dict[str, object]

    def resolve(self, candidate_root: Path) -> tuple[str, ...]:
        def inspect_components(relative: str, *, directory: bool) -> Path:
            current = candidate_root
            parts = Path(relative).parts
            for index, part in enumerate(parts):
                current /= part
                metadata = current.lstat()
                if current.is_symlink():
                    raise SourceProbeError(
                        f"declared generated source {relative!r} uses a symlink"
                    )
                last = index + 1 == len(parts)
                if not last or directory:
                    if not current.is_dir():
                        raise SourceProbeError(
                            f"declared generated source {relative!r} "
                            "has a non-directory parent"
                        )
                elif not current.is_file():
                    raise SourceProbeError(
                        f"declared generated source {relative!r} "
                        "is not a regular file"
                    )
                del metadata
            return current

        exact = tuple(
            sorted(
                {
                    _canonical_path(path, "declared generated source")
                    for path in self.exact_sources
                }
            )
        )
        if len(exact) != len(self.exact_sources):
            raise SourceProbeError("declared generated sources are duplicated")
        root = self.source_root
        pattern = self.source_pattern
        if root is None:
            if pattern is not None:
                raise SourceProbeError("source pattern requires a source root")
        else:
            root = _canonical_path(root, "declared generated source root")
            source_root = candidate_root / root
            if pattern is None:
                if root not in exact:
                    raise SourceProbeError(
                        "file source root must be present in exact sources"
                    )
            else:
                source_root = inspect_components(root, directory=True)
                if (
                    not pattern
                    or "/" in pattern
                    or "\\" in pattern
                    or "**" in pattern
                    or re.fullmatch(r"[A-Za-z0-9_.?*\-\[\]]+", pattern) is None
                    or not any(character in pattern for character in "*?[")
                ):
                    raise SourceProbeError("declared source pattern is invalid")
                expected = {
                    path.relative_to(candidate_root).as_posix()
                    for path in sorted(source_root.iterdir())
                    if fnmatch.fnmatchcase(path.name, pattern)
                    and path.is_file()
                    and not path.is_symlink()
                }
                actual = {
                    path for path in exact if Path(path).parent.as_posix() == root
                }
                if actual != expected:
                    raise SourceProbeError(
                        "trusted directory/glob contract differs from candidate tree"
                    )
        for path in exact:
            inspect_components(path, directory=False)
        return exact


@dataclass(frozen=True)
class SourceObservation:
    permitted_sources: tuple[str, ...]
    reported_sources: tuple[str, ...]
    raw_metadata: bytes
    raw_report: bytes
    execution_snapshot_sha256: str


def _python_runtime_mounts() -> list[Mount]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    roots = [Path(f"/usr/lib/{version}")]
    return [
        Mount(root.resolve(strict=True), root.as_posix(), noexec=False)
        for root in roots
        if root.exists()
    ]


def _json_output(data: bytes, label: str) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant {value!r}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"duplicate JSON key {name!r}")
            result[name] = value
        return result

    try:
        return json.loads(
            strict_utf8(data, label),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ProbeSandboxError as error:
        raise SourceProbeError(str(error)) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise SourceProbeError(f"{label} is not valid JSON") from error


def probe_generated_sources(
    candidate_root: Path,
    *,
    program_paths: Iterable[str],
    entrypoint: str,
    metadata_arguments: Iterable[str],
    load_arguments: Iterable[str],
    contract: SourceContract,
    scratch_root: Path,
    budget: ProbeBudget,
) -> SourceObservation:
    """Require candidate metadata, reported, permitted, and consumed sets to agree."""
    if candidate_root.is_symlink():
        raise SourceProbeError("candidate source root is a symlink")
    candidate_root = candidate_root.resolve(strict=True)
    entrypoint = _canonical_path(entrypoint, "generated-source entrypoint")
    selected_programs = tuple(
        sorted(
            {
                _canonical_path(path, "generated-source program path")
                for path in program_paths
            }
        )
    )
    if entrypoint not in selected_programs:
        raise SourceProbeError("generated-source entrypoint is not admitted code")
    try:
        json.dumps(
            contract.metadata,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise SourceProbeError(
            "trusted graph/schema metadata is not strict JSON"
        ) from error
    permitted = contract.resolve(candidate_root)
    budget.preflight_variants(2 + len(permitted))
    complete_snapshot = ExecutionSnapshot.capture(
        candidate_root,
        [*selected_programs, *permitted],
    )
    scratch_root = scratch_root.resolve(strict=True)
    runner = SandboxRunner(scratch_root, budget)

    def execute(
        arguments: Iterable[str],
        *,
        omit: set[str],
    ) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory(
            prefix="source-probe-",
            dir=scratch_root,
        ) as temporary:
            base = Path(temporary)
            tree = base / "tree"
            complete_snapshot.materialize(tree, omit=omit)
            work = base / "work"
            work.mkdir()
            completed, _ = runner.run(
                PYTHON,
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    f"/repo/{entrypoint}",
                    *arguments,
                ],
                read_only=[
                    Mount(tree, "/repo", noexec=True),
                    *_python_runtime_mounts(),
                ],
                writable=[Mount(work, "/work")],
            )
            return completed

    metadata_completed = execute(
        metadata_arguments,
        omit=set(permitted),
    )
    if metadata_completed.returncode != 0:
        raise SourceProbeError(
            "candidate source metadata failed in code-only confinement: "
            + strict_utf8(
                metadata_completed.stderr,
                "candidate source metadata stderr",
            )
        )
    metadata = _json_output(
        metadata_completed.stdout,
        "candidate source metadata",
    )
    if metadata != contract.metadata:
        raise SourceProbeError(
            "candidate source metadata differs from trusted graph/schema contract"
        )

    load_completed = execute(load_arguments, omit=set())
    if load_completed.returncode != 0:
        raise SourceProbeError(
            "candidate source load failed in admitted-source confinement: "
            + strict_utf8(load_completed.stderr, "candidate source load stderr")
        )
    reported = _json_output(
        load_completed.stdout,
        "candidate generated-source report",
    )
    if (
        not isinstance(reported, list)
        or reported != sorted(reported)
        or len(reported) != len(set(reported))
        or not all(isinstance(path, str) for path in reported)
    ):
        raise SourceProbeError("candidate generated-source report is invalid")
    reported_tuple = tuple(reported)
    if reported_tuple != permitted:
        raise SourceProbeError(
            "declared/reported/permitted generated-source sets differ"
        )

    for source in permitted:
        mutation = execute(load_arguments, omit={source})
        if mutation.returncode != 0:
            continue
        mutation_report = _json_output(
            mutation.stdout,
            f"candidate generated-source mutation {source!r}",
        )
        if mutation_report == reported:
            raise SourceProbeError(
                f"candidate reported source {source!r} is not consumed"
            )
    return SourceObservation(
        permitted_sources=permitted,
        reported_sources=reported_tuple,
        raw_metadata=metadata_completed.stdout,
        raw_report=load_completed.stdout,
        execution_snapshot_sha256=complete_snapshot.digest,
    )
