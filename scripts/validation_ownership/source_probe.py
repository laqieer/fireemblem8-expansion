"""Generated-source discovery by admitted-filesystem confinement."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.validation_ownership.budget import ProbeBudget, ProbeBudgetError
from scripts.validation_ownership.sandbox import (
    PYTHON,
    ExecutionSnapshot,
    Mount,
    ProbeSandboxError,
    SandboxRunner,
    runtime_dependency_mounts,
    run_bounded_process,
    strict_utf8,
)
from scripts.validation_ownership.candidate_runtime import (
    OMISSION_EXIT,
    OMISSION_PREFIX,
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
    admitted_imports: tuple[str, ...] = (
        "glob",
        "json",
        "mmap",
        "os",
        "pathlib",
        "sys",
    )

    def resolve(
        self,
        candidate_root: Path,
        budget: ProbeBudget,
    ) -> tuple[str, ...]:
        def inspect_components(relative: str, *, directory: bool) -> Path:
            current = candidate_root
            parts = Path(relative).parts
            for index, part in enumerate(parts):
                budget.remaining("source contract")
                budget.charge_count("snapshot_ops")
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
                children = sorted(source_root.iterdir())
                budget.charge_count("snapshot_ops", len(children) * 3)
                expected = {
                    path.relative_to(candidate_root).as_posix()
                    for path in children
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
    runtime_sha256: str


def _python_runtime_mounts(
    admitted_imports: Iterable[str],
    budget: ProbeBudget,
    destination: Path,
) -> tuple[list[Mount], str]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib = Path(f"/usr/lib/{version}").resolve(strict=True)
    helper = (
        "import importlib,json,sys;"
        f"sys.path.insert(0,{str(Path(__file__).resolve().parents[2])!r});"
        "import scripts.validation_ownership.candidate_runtime;"
        "[importlib.import_module(name) for name in sys.argv[1:]];"
        "print(json.dumps(sorted({"
        "module.__file__ for module in sys.modules.values() "
        "if getattr(module,'__file__',None)})))"
    )
    completed = run_bounded_process(
        [
            str(PYTHON),
            "-I",
            "-S",
            "-B",
            "-c",
            helper,
            *sorted(admitted_imports),
        ],
        budget,
        environment={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0 or completed.stderr:
        raise SourceProbeError(
            "cannot resolve trusted candidate Python import closure: "
            + strict_utf8(completed.stderr, "Python closure stderr")
        )
    module_paths = _json_output(
        completed.stdout,
        "trusted candidate Python import closure",
    )
    if (
        not isinstance(module_paths, list)
        or module_paths != sorted(set(module_paths))
        or not all(isinstance(path, str) for path in module_paths)
    ):
        raise SourceProbeError("trusted candidate Python import closure is invalid")
    destination.mkdir(parents=True, exist_ok=False)
    extensions = []
    copied = 0
    digest = hashlib.sha256(b"validation-ownership-python-runtime-v1\0")
    for path_text in module_paths:
        source = Path(path_text)
        try:
            relative = source.relative_to(stdlib)
        except ValueError:
            continue
        metadata = os.lstat(source)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SourceProbeError(
                f"trusted Python module {source} is not a stable regular file"
            )
        budget.charge_count("snapshot_files")
        budget.charge_count("snapshot_ops", 3)
        budget.charge_bytes("snapshot", metadata.st_size)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded_path = relative.as_posix().encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "little"))
        digest.update(encoded_path)
        digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(4, "little"))
        digest.update(metadata.st_size.to_bytes(8, "little"))
        digest.update(metadata.st_mtime_ns.to_bytes(8, "little", signed=True))
        with source.open("rb") as input_stream, target.open("wb") as output_stream:
            while True:
                budget.remaining("Python runtime closure")
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                output_stream.write(chunk)
                digest.update(chunk)
        target.chmod(stat.S_IMODE(metadata.st_mode))
        os.utime(target, ns=(metadata.st_mtime_ns, metadata.st_mtime_ns))
        copied += 1
        if source.suffix == ".so":
            extensions.append(source.resolve(strict=True))
    if copied == 0:
        raise SourceProbeError("trusted candidate Python closure is empty")
    dependency_mounts, dependency_authority = runtime_dependency_mounts(
        extensions,
        budget,
    )
    digest.update(
        json.dumps(
            dependency_authority,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    )
    return (
        [
            Mount(destination, stdlib.as_posix(), noexec=False),
            *dependency_mounts,
        ],
        digest.hexdigest(),
    )


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
    if (
        not contract.admitted_imports
        or tuple(sorted(set(contract.admitted_imports)))
        != contract.admitted_imports
        or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            for name in contract.admitted_imports
        )
        or any(name in {"ctypes", "_ctypes"} for name in contract.admitted_imports)
    ):
        raise SourceProbeError("trusted admitted import set is invalid")
    permitted = contract.resolve(candidate_root, budget)
    budget.preflight_variants(2 + len(permitted))
    complete_snapshot = ExecutionSnapshot.capture(
        candidate_root,
        budget,
        [*selected_programs, *permitted],
    )
    scratch_root = scratch_root.resolve(strict=True)
    runner = SandboxRunner(scratch_root, budget)
    with tempfile.TemporaryDirectory(
        prefix="source-probe-",
        dir=scratch_root,
    ) as temporary:
        base = Path(temporary)
        python_runtime_mounts, runtime_sha256 = _python_runtime_mounts(
            contract.admitted_imports,
            budget,
            base / "python-runtime",
        )
        tree = base / "tree"
        complete_snapshot.materialize(tree, budget)
        work = base / "work"
        work.mkdir()
        runtime_script = (
            Path(__file__).resolve().parent / "candidate_runtime.py"
        )
        root_entry = complete_snapshot.entry(".")
        metadata_records = {
            ".": {
                "gid": root_entry.gid,
                "kind": "directory",
                "mode": stat.S_IFDIR | root_entry.mode,
                "mtime_ns": root_entry.mtime_ns,
                "size": root_entry.size,
                "uid": root_entry.uid,
            },
            **{
                entry.path: {
                    "gid": entry.gid,
                    "kind": entry.kind,
                    "mode": (
                        stat.S_IFREG if entry.kind == "file" else stat.S_IFDIR
                    )
                    | entry.mode,
                    "mtime_ns": entry.mtime_ns,
                    "size": entry.size,
                    "uid": entry.uid,
                }
                for entry in complete_snapshot.entries
            },
        }

        def execute(
            arguments: Iterable[str],
            *,
            admitted_sources: Iterable[str],
            omitted_source: str | None,
        ) -> tuple[subprocess.CompletedProcess[bytes], str]:
            nonce = secrets.token_hex(32)
            config_path = base / f"runtime-{secrets.token_hex(8)}.json"
            config_path.write_text(
                json.dumps(
                    {
                        "admitted_imports": sorted(set(contract.admitted_imports)),
                        "admitted_paths": sorted(set(admitted_sources)),
                        "metadata": metadata_records,
                        "nonce": nonce,
                        "omitted_source": omitted_source,
                        "program_paths": list(selected_programs),
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="ascii",
            )
            config_path.chmod(0o400)
            omission_mask = None
            if omitted_source is not None:
                omission_mask = base / f"omitted-{secrets.token_hex(8)}"
                omission_mask.write_bytes(b"")
                omission_mask.chmod(0)
            try:
                read_only = [
                    Mount(tree, "/repo", noexec=True),
                    Mount(
                        runtime_script,
                        "/trusted/candidate_runtime.py",
                        noexec=True,
                    ),
                    *python_runtime_mounts,
                ]
                if omission_mask is not None:
                    read_only.append(
                        Mount(
                            omission_mask,
                            f"/repo/{omitted_source}",
                            noexec=True,
                        )
                    )
                try:
                    completed, _ = runner.run(
                        PYTHON,
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-B",
                            "/trusted/candidate_runtime.py",
                            f"/repo/{entrypoint}",
                            *arguments,
                        ],
                        read_only=read_only,
                        writable=[Mount(work, "/work")],
                        bootstrap_config=config_path,
                    )
                except (ProbeBudgetError, ProbeSandboxError) as error:
                    raise SourceProbeError(
                        f"candidate controlled execution failed: {error}"
                    ) from error
                return completed, nonce
            finally:
                config_path.unlink(missing_ok=True)
                if omission_mask is not None:
                    omission_mask.unlink(missing_ok=True)

        metadata_completed, _ = execute(
            metadata_arguments,
            admitted_sources=(),
            omitted_source=None,
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

        load_completed, _ = execute(
            load_arguments,
            admitted_sources=permitted,
            omitted_source=None,
        )
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
            mutation, nonce = execute(
                load_arguments,
                admitted_sources=permitted,
                omitted_source=source,
            )
            expected = f"{OMISSION_PREFIX} {nonce} {source}\n".encode("ascii")
            if (
                mutation.returncode != OMISSION_EXIT
                or mutation.stdout
                or mutation.stderr != expected
            ):
                raise SourceProbeError(
                    f"candidate omission replay for {source!r} did not "
                    "produce the exact authenticated missing-source outcome "
                    f"(returncode={mutation.returncode}, "
                    f"stdout={mutation.stdout!r}, stderr={mutation.stderr!r})"
                )
        return SourceObservation(
            permitted_sources=permitted,
            reported_sources=reported_tuple,
            raw_metadata=metadata_completed.stdout,
            raw_report=load_completed.stdout,
            execution_snapshot_sha256=complete_snapshot.digest,
            runtime_sha256=runtime_sha256,
        )
