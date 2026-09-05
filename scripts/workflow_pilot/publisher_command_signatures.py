#!/usr/bin/env python3
"""Typed closed command authority for the trusted patch publisher builder."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import posixpath
import re
import shlex
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(os.path.abspath(__file__)).parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"
REGISTRY_PATH = (
    ROOT
    / "scripts"
    / "workflow_pilot"
    / "publisher_command_signatures.json"
)
REGISTRY_SCHEMA_VERSION = 1
REVIEWED_SIGNATURE_REGISTRY_SHA256 = (
    "0ec719cc59214af79ab8a27497c441ed3507b9c4f4ff46fcd6bfce16bb295dc1"
)
BUILDER_STEP_NAME = "Build candidate in isolated namespace and stage public inputs"
_TOP_LEVEL_HEREDOC_LANGUAGES = {
    "BUILDER_ISOLATION": "shell",
    "CANDIDATE_BUILD": "shell",
    "CANDIDATE_LAUNCHER": "python",
    "SUPERVISOR_LAUNCHER": "python",
}
_FUNCTION_RE = re.compile(
    r"^(?:function[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:[ \t]*\([ \t]*\))?[ \t]*\{$"
)
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_ASSIGNMENT_PREFIX_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^]]+\])?=")
_REDIRECTION_RE = re.compile(
    r"^(?P<fd>[0-9]*)(?P<operator><<<|<<-|<<|&>>|>>|<>|>\||<&|>&|&>|<|>)"
    r"(?P<target>.*)$"
)
_WRITE_EXECUTABLES = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "install",
        "ln",
        "mkdir",
        "mknod",
        "mount",
        "rm",
        "rmdir",
        "tee",
        "umount",
    }
)
_READ_EXECUTABLES = frozenset(
    {
        "awk",
        "cat",
        "find",
        "findmnt",
        "getent",
        "grep",
        "id",
        "mountpoint",
        "ps",
        "sort",
        "stat",
        "test",
    }
)
_CONTROL_OPENERS = {
    "case": "case",
    "for": "loop",
    "if": "if",
    "select": "loop",
    "until": "loop",
    "while": "loop",
}
_CONTROL_CLOSERS = {
    "done": "loop",
    "esac": "case",
    "fi": "if",
}
_SHELL_PREFIXES = frozenset({"!", "do", "elif", "else", "if", "then", "while", "until"})
_SHELL_STRUCTURE_WORDS = frozenset(
    {"case", "do", "done", "else", "esac", "fi", "for", "in", "then", "{", "}"}
)
_REVIEWED_EXECUTABLE_ALIASES = {
    "$HOME/venv/bin/python3": "/mnt/home/venv/bin/python3",
}
_AUTHORITY_PATHS = (
    ".github/workflows/build.yml",
    "scripts/workflow_pilot/__init__.py",
    "scripts/workflow_pilot/publisher_command_signatures.py",
    "scripts/workflow_pilot/publisher_command_signatures.json",
    "scripts/workflow_pilot/publisher_shell_contract.py",
    "scripts/upstream_port/__init__.py",
    "scripts/upstream_port/verify.py",
    "tests/workflows/__init__.py",
    "tests/workflows/test_patch_release_workflow.py",
)
_PARSER_AUTHORITY_PATH = (
    "scripts/workflow_pilot/publisher_shell_contract.py"
)
_REGISTRY_AUTHORITY_PATH = (
    "scripts/workflow_pilot/publisher_command_signatures.json"
)
_WORKFLOW_AUTHORITY_PATH = ".github/workflows/build.yml"
_MAX_AUTHORITY_FILE_BYTES = 8 * 1024 * 1024
_AUTHORITY_SNAPSHOT = None
if not hasattr(os, "O_NOFOLLOW"):
    raise RuntimeError("publisher authority requires O_NOFOLLOW")
_O_NOFOLLOW = os.O_NOFOLLOW
_TRUSTED_PARSER_IMPORTS = {
    name: sys.modules[name]
    for name in (
        "__future__",
        "ast",
        "dataclasses",
        "hashlib",
        "posixpath",
        "re",
        "shlex",
        "typing",
    )
}


@dataclass(frozen=True)
class _AuthorityFile:
    path: str
    mode: str
    object_id: str
    data: bytes


@dataclass(frozen=True)
class _AuthoritySnapshot:
    root: Path
    revision: str
    tree_id: str
    files: dict[str, _AuthorityFile]
    parser: types.ModuleType


def _file_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_absolute_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise ValueError("publisher authority root must be absolute")
    descriptor = os.open(
        "/",
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
    )
    try:
        for component in absolute.parts[1:]:
            if component in {"", ".", ".."}:
                raise ValueError("publisher authority root path differs")
            next_descriptor = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | _O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _relative_parts(path: str) -> tuple[str, ...]:
    if not path or path.startswith("/"):
        raise ValueError("publisher authority path must be relative")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("publisher authority path escapes its root")
    return parts


def _secure_directory_metadata(
    descriptor: int,
    *,
    root_metadata: os.stat_result,
    label: str,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"publisher authority {label} must be a directory")
    if (metadata.st_uid, metadata.st_gid) != (
        root_metadata.st_uid,
        root_metadata.st_gid,
    ):
        raise ValueError(f"publisher authority {label} owner differs")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(
            f"publisher authority {label} mode is writable by group or other"
        )
    return metadata


def _open_authority_path(
    root_descriptor: int,
    relative_path: str,
    *,
    root_metadata: os.stat_result,
) -> tuple[int, tuple[tuple[int, ...], ...]]:
    parts = _relative_parts(relative_path)
    descriptor = os.dup(root_descriptor)
    signatures = [_file_signature(root_metadata)]
    try:
        for index, component in enumerate(parts[:-1]):
            next_descriptor = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | _O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = _secure_directory_metadata(
                descriptor,
                root_metadata=root_metadata,
                label="/".join(parts[: index + 1]),
            )
            signatures.append(_file_signature(metadata))
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY
            | os.O_NONBLOCK
            | os.O_CLOEXEC
            | _O_NOFOLLOW,
            dir_fd=descriptor,
        )
        return file_descriptor, tuple(signatures)
    finally:
        os.close(descriptor)


def _read_all_from_fd(descriptor: int, *, expected_size: int) -> bytes:
    if expected_size > _MAX_AUTHORITY_FILE_BYTES:
        raise ValueError("publisher authority file exceeds size limit")
    chunks: list[bytes] = []
    remaining = expected_size + 1
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _secure_read_authority_file(
    root: Path,
    relative_path: str,
    *,
    expected_data: bytes | None,
    expected_mode: str,
) -> bytes:
    try:
        root_descriptor = _open_absolute_directory_nofollow(root)
    except OSError as error:
        raise ValueError("publisher authority root path differs") from error
    try:
        root_metadata = _secure_directory_metadata(
            root_descriptor,
            root_metadata=os.fstat(root_descriptor),
            label="root",
        )
        file_descriptor, parent_signatures = _open_authority_path(
            root_descriptor,
            relative_path,
            root_metadata=root_metadata,
        )
        try:
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(
                    f"publisher authority {relative_path} must be a regular file"
                )
            expected_permissions = {
                "100644": 0o644,
                "100755": 0o755,
            }.get(expected_mode)
            if expected_permissions is None:
                raise ValueError(
                    f"publisher authority {relative_path} Git mode differs"
                )
            if (before.st_uid, before.st_gid) != (
                root_metadata.st_uid,
                root_metadata.st_gid,
            ):
                raise ValueError(
                    f"publisher authority {relative_path} owner differs"
                )
            if stat.S_IMODE(before.st_mode) != expected_permissions:
                raise ValueError(
                    f"publisher authority {relative_path} mode differs"
                )
            if before.st_nlink != 1:
                raise ValueError(
                    f"publisher authority {relative_path} link count differs"
                )
            data = _read_all_from_fd(
                file_descriptor,
                expected_size=before.st_size,
            )
            after = os.fstat(file_descriptor)
            if _file_signature(after) != _file_signature(before):
                raise ValueError(
                    f"publisher authority {relative_path} changed while read"
                )
            if len(data) != before.st_size or (
                expected_data is not None and data != expected_data
            ):
                raise ValueError(
                    f"publisher authority {relative_path} content differs"
                )
        finally:
            os.close(file_descriptor)

        current_root_descriptor = _open_absolute_directory_nofollow(root)
        try:
            current_root = os.fstat(current_root_descriptor)
            current_file_descriptor, current_parent_signatures = (
                _open_authority_path(
                    current_root_descriptor,
                    relative_path,
                    root_metadata=current_root,
                )
            )
            try:
                current_file = os.fstat(current_file_descriptor)
            finally:
                os.close(current_file_descriptor)
        finally:
            os.close(current_root_descriptor)
        if (
            current_parent_signatures != parent_signatures
            or _file_signature(current_file) != _file_signature(before)
        ):
            raise ValueError(
                f"publisher authority {relative_path} path changed while read"
            )
        return data
    except OSError as error:
        raise ValueError(
            f"publisher authority {relative_path} path differs"
        ) from error
    finally:
        os.close(root_descriptor)


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "HOME": "/",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _run_git(
    root_descriptor: int,
    arguments: list[str],
) -> bytes:
    completed = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=f"/proc/self/fd/{root_descriptor}",
        env=_git_environment(),
        pass_fds=(root_descriptor,),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"publisher authority Git query failed: {detail}")
    return completed.stdout


def _authority_revision(
    root_descriptor: int,
    *,
    revision: str | None,
) -> tuple[str, str]:
    expected = globals().get("_BOOTSTRAP_REVISION")
    if expected is None:
        expected = os.environ.get("EXPECTED_BUILD_SHA")
    if revision is None:
        revision = expected or "HEAD"
    resolved = _run_git(
        root_descriptor,
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
    ).decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", resolved) is None:
        raise ValueError("publisher authority revision differs")
    head = _run_git(
        root_descriptor,
        ["rev-parse", "--verify", "HEAD^{commit}"],
    ).decode("ascii").strip()
    if expected is not None and (resolved != expected or head != expected):
        raise ValueError("publisher authority checkout revision differs")
    commit = _verified_git_object(
        root_descriptor,
        object_id=resolved,
        object_type="commit",
    )
    first_line = commit.split(b"\n", 1)[0]
    if not first_line.startswith(b"tree "):
        raise ValueError("publisher authority tree identity differs")
    tree_id = first_line.removeprefix(b"tree ").decode("ascii")
    if re.fullmatch(r"[0-9a-f]{40}", tree_id) is None:
        raise ValueError("publisher authority tree identity differs")
    return resolved, tree_id


def _git_object_id(object_type: str, data: bytes) -> str:
    return hashlib.sha1(
        f"{object_type} {len(data)}\0".encode("ascii") + data
    ).hexdigest()


def _verified_git_object(
    root_descriptor: int,
    *,
    object_id: str,
    object_type: str,
) -> bytes:
    if (
        re.fullmatch(r"[0-9a-f]{40}", object_id) is None
        or object_type not in {"blob", "commit", "tree"}
    ):
        raise ValueError("publisher authority Git object request differs")
    size = _run_git(
        root_descriptor,
        ["cat-file", "-s", object_id],
    ).decode("ascii").strip()
    if not size.isdigit() or int(size) > _MAX_AUTHORITY_FILE_BYTES:
        raise ValueError("publisher authority Git object size differs")
    data = _run_git(
        root_descriptor,
        ["cat-file", object_type, object_id],
    )
    if len(data) != int(size) or _git_object_id(object_type, data) != object_id:
        raise ValueError("publisher authority Git object identity differs")
    return data


def _tree_entries(data: bytes) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    offset = 0
    while offset < len(data):
        separator = data.find(b" ", offset)
        terminator = data.find(b"\0", separator + 1)
        if separator <= offset or terminator < 0 or terminator + 21 > len(data):
            raise ValueError("publisher authority Git tree is malformed")
        mode = data[offset:separator].decode("ascii")
        name = data[separator + 1 : terminator].decode("utf-8", "strict")
        object_id = data[terminator + 1 : terminator + 21].hex()
        if (
            name in {"", ".", ".."}
            or "/" in name
            or name in entries
            or re.fullmatch(r"[0-9a-f]{40}", object_id) is None
        ):
            raise ValueError("publisher authority Git tree entry differs")
        entries[name] = (mode, object_id)
        offset = terminator + 21
    return entries


def _git_authority_file(
    root_descriptor: int,
    *,
    tree_id: str,
    path: str,
) -> _AuthorityFile:
    parts = _relative_parts(path)
    object_id = tree_id
    mode = ""
    for index, part in enumerate(parts):
        tree = _verified_git_object(
            root_descriptor,
            object_id=object_id,
            object_type="tree",
        )
        entry = _tree_entries(tree).get(part)
        if entry is None:
            raise ValueError(f"publisher authority {path} tree entry differs")
        mode, object_id = entry
        if index + 1 < len(parts):
            if mode not in {"40000", "040000"}:
                raise ValueError(
                    f"publisher authority {path} parent tree mode differs"
                )
        elif mode not in {"100644", "100755"}:
            raise ValueError(f"publisher authority {path} Git mode differs")
    blob = _verified_git_object(
        root_descriptor,
        object_id=object_id,
        object_type="blob",
    )
    return _AuthorityFile(
        path=path,
        mode=mode,
        object_id=object_id,
        data=blob,
    )


def _parser_from_authority(
    authority_file: _AuthorityFile,
) -> types.ModuleType:
    module_name = (
        "_publisher_shell_contract_authority_"
        + authority_file.object_id
    )
    module = types.ModuleType(module_name)
    module.__file__ = authority_file.path
    module.__package__ = ""
    module.__authority_object_id__ = authority_file.object_id
    module.__authority_sha256__ = hashlib.sha256(authority_file.data).hexdigest()
    sys.modules[module_name] = module
    previous_modules = {
        name: sys.modules.get(name)
        for name in _TRUSTED_PARSER_IMPORTS
    }
    previous_path = sys.path[:]
    try:
        sys.modules.update(_TRUSTED_PARSER_IMPORTS)
        sys.path[:] = []
        exec(
            compile(
                authority_file.data,
                authority_file.path,
                "exec",
            ),
            module.__dict__,
        )
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.path[:] = previous_path
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


def _load_authority_snapshot(
    *,
    root: Path = ROOT,
    revision: str | None = None,
) -> _AuthoritySnapshot:
    root_descriptor = _open_absolute_directory_nofollow(root)
    try:
        resolved_revision, tree_id = _authority_revision(
            root_descriptor,
            revision=revision,
        )
        files = {
            path: _git_authority_file(
                root_descriptor,
                tree_id=tree_id,
                path=path,
            )
            for path in _AUTHORITY_PATHS
        }
    finally:
        os.close(root_descriptor)
    for path, authority_file in files.items():
        _secure_read_authority_file(
            root,
            path,
            expected_data=authority_file.data,
            expected_mode=authority_file.mode,
        )
    parser = _parser_from_authority(files[_PARSER_AUTHORITY_PATH])
    return _AuthoritySnapshot(
        root=Path(os.path.abspath(root)),
        revision=resolved_revision,
        tree_id=tree_id,
        files=files,
        parser=parser,
    )


def _authority_snapshot() -> _AuthoritySnapshot:
    global _AUTHORITY_SNAPSHOT
    if _AUTHORITY_SNAPSHOT is None:
        _AUTHORITY_SNAPSHOT = _load_authority_snapshot()
    return _AUTHORITY_SNAPSHOT


def authority_file_bytes(path: str) -> bytes:
    try:
        return _authority_snapshot().files[path].data
    except KeyError as error:
        raise ValueError(
            f"publisher authority path is not in the closed inventory: {path}"
        ) from error


_WRITING_REGISTRY = (
    __name__ == "__main__" and sys.argv[1:] == ["--write-registry"]
)
if _WRITING_REGISTRY:
    _live_parser_data = _secure_read_authority_file(
        ROOT,
        _PARSER_AUTHORITY_PATH,
        expected_data=None,
        expected_mode="100644",
    )
    publisher_shell_contract = _parser_from_authority(
        _AuthorityFile(
            path=_PARSER_AUTHORITY_PATH,
            mode="100644",
            object_id=hashlib.sha256(_live_parser_data).hexdigest(),
            data=_live_parser_data,
        )
    )
else:
    publisher_shell_contract = _authority_snapshot().parser


@dataclass(frozen=True)
class CommandSignature:
    signature_id: str
    kind: str
    layer: str
    owner: str
    control_context: tuple[str, ...]
    preceding_operator: str | None
    following_operator: str | None
    command: str
    executable: str
    wrappers: tuple[str, ...]
    argv: tuple[str, ...]
    stdin: str
    stdout: str
    stderr: str
    redirections: tuple[tuple[str, str], ...]
    accesses: tuple[tuple[str, str], ...]
    writes: tuple[str, ...]
    events: tuple[str, ...]
    program_sha256: str | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: Any, *, label: str) -> CommandSignature:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError(f"{label} fields differ")
        scalar_fields = (
            "signature_id",
            "kind",
            "layer",
            "owner",
            "command",
            "executable",
            "stdin",
            "stdout",
            "stderr",
        )
        for field in scalar_fields:
            if not isinstance(value[field], str) or not value[field]:
                raise ValueError(f"{label}.{field} must be a nonempty string")
        if value["preceding_operator"] is not None and not isinstance(
            value["preceding_operator"], str
        ):
            raise ValueError(f"{label}.preceding_operator differs")
        if value["following_operator"] is not None and not isinstance(
            value["following_operator"], str
        ):
            raise ValueError(f"{label}.following_operator differs")
        if value["program_sha256"] is not None and (
            not isinstance(value["program_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["program_sha256"]) is None
        ):
            raise ValueError(f"{label}.program_sha256 differs")
        tuple_fields = (
            "control_context",
            "wrappers",
            "argv",
            "writes",
            "events",
        )
        pair_fields = ("redirections", "accesses")
        normalized = dict(value)
        for field in tuple_fields:
            items = value[field]
            if not isinstance(items, list) or not all(
                isinstance(item, str) for item in items
            ):
                raise ValueError(f"{label}.{field} differs")
            normalized[field] = tuple(items)
        for field in pair_fields:
            items = value[field]
            if not isinstance(items, list) or not all(
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(part, str) for part in item)
                for item in items
            ):
                raise ValueError(f"{label}.{field} differs")
            normalized[field] = tuple(tuple(item) for item in items)
        return cls(**normalized)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _registry_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _publisher_job(workflow: str) -> str:
    match = re.search(
        r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if match is None:
        raise ValueError("workflow must define one patch-release job")
    return match.group("body")


def _publisher_step_blocks(workflow: str) -> tuple[str, ...]:
    job = _publisher_job(workflow)
    sections = job.split("\n    steps:\n", 1)
    if len(sections) != 2:
        raise ValueError("patch-release job must define one steps sequence")
    lines = sections[1].splitlines(keepends=True)
    starts = [
        index for index, line in enumerate(lines) if re.match(r"^    - ", line)
    ]
    return tuple(
        "".join(
            lines[
                start : starts[index + 1] if index + 1 < len(starts) else len(lines)
            ]
        )
        for index, start in enumerate(starts)
    )


def publisher_builder_run_script(workflow: str) -> str:
    matches = [
        block
        for block in _publisher_step_blocks(workflow)
        if f"    - name: {BUILDER_STEP_NAME}\n" in block
    ]
    if len(matches) != 1:
        raise ValueError("publisher builder step count differs")
    return publisher_shell_contract.literal_run_script_from_step_block(
        matches[0],
        label="publisher builder step",
    )


def _extract_quoted_heredoc(
    script: str,
    *,
    delimiter: str,
    label: str,
) -> tuple[str, str]:
    pattern = re.compile(
        rf"(?ms)^(?P<introducer>[^\n]*<<'{re.escape(delimiter)}'[^\n]*)\n"
        rf"(?P<body>.*?)"
        rf"^{re.escape(delimiter)}$"
    )
    matches = list(pattern.finditer(script))
    if len(matches) != 1:
        raise ValueError(f"{label} {delimiter} heredoc count differs")
    match = matches[0]
    stripped = (
        script[: match.start()]
        + match.group("introducer")
        + "\n"
        + script[match.end() :]
    )
    return match.group("body") + "\n", stripped


def _builder_sources(
    run_script: str,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    outer = run_script
    bodies: dict[str, str] = {}
    for delimiter in _TOP_LEVEL_HEREDOC_LANGUAGES:
        body, outer = _extract_quoted_heredoc(
            outer,
            delimiter=delimiter,
            label="publisher builder run script",
        )
        bodies[delimiter] = body

    builder_shell = bodies["BUILDER_ISOLATION"]
    parser_sources = dict(
        publisher_shell_contract.raw_patch_release_parser_sources(builder_shell)
    )
    membership_name, membership_source = (
        publisher_shell_contract.raw_patch_release_membership_checker_source(
            builder_shell
        )
    )
    stripped_builder = (
        publisher_shell_contract._strip_patch_release_parser_heredoc_bodies(
            builder_shell
        )
    )
    shell_sources = {
        "publisher-host": outer,
        "builder-isolation": stripped_builder,
        "candidate-build": bodies["CANDIDATE_BUILD"],
    }
    python_sources = {
        "candidate-launcher": ("CANDIDATE_LAUNCHER", bodies["CANDIDATE_LAUNCHER"]),
        "supervisor-launcher": (
            "SUPERVISOR_LAUNCHER",
            bodies["SUPERVISOR_LAUNCHER"],
        ),
        **{
            f"builder-parser:{name}": (name, source)
            for name, source in parser_sources.items()
        },
        "builder-membership-checker": (membership_name, membership_source),
    }
    return shell_sources, python_sources


def _redirections(
    tokens: tuple[publisher_shell_contract._ShellToken, ...],
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    redirections: list[tuple[str, str]] = []
    command_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].text
        match = _REDIRECTION_RE.fullmatch(token)
        if match is None:
            command_tokens.append(token)
            index += 1
            continue
        operator = (match.group("fd") or "") + match.group("operator")
        target = match.group("target")
        if not target:
            if index + 1 >= len(tokens):
                raise ValueError("publisher command has a redirection without a target")
            target = tokens[index + 1].text
            index += 1
        redirections.append((operator, target))
        index += 1
    return tuple(redirections), tuple(command_tokens)


def _normalized_command(
    command_tokens: tuple[str, ...],
    *,
    owner: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    tokens = list(command_tokens)
    wrappers: list[str] = []
    while tokens and tokens[0] in _SHELL_PREFIXES:
        wrappers.append(tokens.pop(0))
    while tokens and (
        _ASSIGNMENT_RE.fullmatch(tokens[0])
        or _ASSIGNMENT_PREFIX_RE.match(tokens[0])
    ):
        wrappers.append(tokens.pop(0))
    if not tokens:
        return "@shell-state", tuple(wrappers), ()
    if tokens[-1] in {"]", "]]"}:
        return "@expression", tuple(wrappers), tuple(tokens)
    if tokens[0] in _SHELL_STRUCTURE_WORDS or (
        len(tokens) == 1 and tokens[0].endswith(")")
    ):
        return "@control", tuple(wrappers), tuple(tokens)
    if tokens[0] == "/usr/bin/sudo":
        wrappers.append(tokens.pop(0))
        if not tokens:
            raise ValueError("publisher sudo wrapper has no executable")
    if tokens[0] == "/usr/bin/env":
        wrappers.append(tokens.pop(0))
        while tokens and (
            tokens[0] == "-i"
            or tokens[0] == "--ignore-environment"
            or _ASSIGNMENT_RE.fullmatch(tokens[0])
        ):
            wrappers.append(tokens.pop(0))
        if tokens and tokens[0] == "--":
            wrappers.append(tokens.pop(0))
        if not tokens:
            raise ValueError("publisher env wrapper has no executable")
    executable = tokens.pop(0)
    if executable in _REVIEWED_EXECUTABLE_ALIASES:
        executable = _REVIEWED_EXECUTABLE_ALIASES[executable]
    if executable == "command":
        raise ValueError("publisher command wrapper is not authorized")
    if executable in {"[", "[[", "(("}:
        return executable, tuple(wrappers), tuple(tokens)
    if executable.startswith(("$", "`")) or any(
        marker in executable for marker in ("${", "$(", "<(", ">(", "*", "?", "[")
    ):
        raise ValueError("publisher executable identity is dynamic")
    if executable == owner:
        executable = f"helper:{owner}"
    elif executable.startswith("/") and not executable.startswith("//"):
        executable = posixpath.normpath(executable)
    return executable, tuple(wrappers), tuple(tokens)


def _stdio_and_writes(
    redirections: tuple[tuple[str, str], ...],
) -> tuple[str, str, str, tuple[str, ...]]:
    stdin = "inherit"
    stdout = "inherit"
    stderr = "inherit"
    writes: list[str] = []
    for operator, target in redirections:
        if operator.endswith(("<<", "<<-", "<<<")):
            stdin = f"heredoc:{target}"
        elif operator.endswith(("<", "<&", "<>")):
            stdin = target
        if operator.startswith("2") or operator in {"&>", "&>>"}:
            stderr = target
        if not operator.startswith("2") and operator.endswith(
            (">", ">>", ">|", ">&", "<>")
        ):
            stdout = target
        if operator.endswith((">", ">>", ">|", "<>")) and target not in {"&1", "&2"}:
            writes.append(target)
    return stdin, stdout, stderr, tuple(dict.fromkeys(writes))


def _resource_tokens(values: Iterable[str]) -> tuple[str, ...]:
    resources: list[str] = []
    for value in values:
        candidate = value.rsplit("=", 1)[-1].strip(" \t'\"(),")
        if not candidate:
            continue
        starts_like_resource = candidate.startswith(("/", "./", "../"))
        if candidate.startswith("$"):
            variable = candidate.lstrip("${").split("}", 1)[0].split("/", 1)[0]
            starts_like_resource = "/" in candidate or any(
                marker in variable.lower()
                for marker in (
                    "artifact",
                    "cgroup",
                    "dir",
                    "file",
                    "handoff",
                    "home",
                    "image",
                    "input",
                    "output",
                    "path",
                    "root",
                    "source",
                    "temp",
                    "wheelhouse",
                    "workspace",
                )
            )
        if not starts_like_resource:
            continue
        if candidate not in resources:
            resources.append(candidate)
    return tuple(resources)


def _semantic_metadata(
    executable: str,
    argv: tuple[str, ...],
    redirections: tuple[tuple[str, str], ...],
    explicit_writes: tuple[str, ...],
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...], tuple[str, ...]]:
    basename = posixpath.basename(executable.removeprefix("helper:"))
    resource_values = _resource_tokens(
        (*argv, *(target for _operator, target in redirections))
    )
    accesses: list[tuple[str, str]] = []
    if basename == "mount":
        for index, resource in enumerate(resource_values):
            mode = "mount-target" if index + 1 == len(resource_values) else "mount-source"
            accesses.append((mode, resource))
    elif basename in {"cp", "install", "ln"}:
        for index, resource in enumerate(resource_values):
            mode = "write" if index + 1 == len(resource_values) else "read"
            accesses.append((mode, resource))
    elif basename in _WRITE_EXECUTABLES:
        accesses.extend(("write", resource) for resource in resource_values)
    elif basename == "kill":
        accesses.extend(("signal-target", resource) for resource in resource_values)
    elif basename in {"useradd", "userdel"}:
        accesses.append(("user-state", argv[-1] if argv else "<missing>"))
    elif basename in _READ_EXECUTABLES:
        accesses.extend(("read", resource) for resource in resource_values)
    else:
        accesses.extend(("read", resource) for resource in resource_values)
    writes = list(explicit_writes)
    writes.extend(
        resource
        for mode, resource in accesses
        if mode in {"mount-target", "write"}
    )
    if basename == "make":
        accesses.append(("write", "$GITHUB_WORKSPACE/build"))
        writes.append("$GITHUB_WORKSPACE/build")
    if executable == "./build_tools.sh":
        accesses.append(("write", "$GITHUB_WORKSPACE/tools"))
        writes.append("$GITHUB_WORKSPACE/tools")
    events: list[str] = []
    if executable.startswith("helper:"):
        events.append("helper-call")
    if basename in {"mount", "umount"}:
        events.append("mount-namespace-change")
    if basename in {"kill"}:
        events.append("process-signal")
    if basename in {"useradd", "userdel"}:
        events.append("builder-user-lifecycle")
    if any("cgroup" in resource for _mode, resource in accesses):
        events.append("cgroup-access")
    if basename in {"python3", "python"}:
        events.append("python-invocation")
    return (
        tuple(accesses),
        tuple(dict.fromkeys(writes)),
        tuple(dict.fromkeys(events)),
    )


def _signature_id(
    *,
    kind: str,
    layer: str,
    owner: str,
    command: str,
    control_context: tuple[str, ...],
    preceding_operator: str | None,
    following_operator: str | None,
) -> str:
    digest = _sha256_text(
        "\0".join(
            (
                kind,
                layer,
                owner,
                command,
                "\x1f".join(control_context),
                preceding_operator or "",
                following_operator or "",
            )
        )
    )[:16]
    return f"{layer}:{owner}:{digest}"


def _shell_signatures(layer: str, script: str) -> tuple[CommandSignature, ...]:
    return _shell_signatures_in_context(
        layer,
        script,
        initial_owner="<main>",
        initial_control_context=(),
    )


def _shell_signatures_in_context(
    layer: str,
    script: str,
    *,
    initial_owner: str,
    initial_control_context: tuple[str, ...],
) -> tuple[CommandSignature, ...]:
    records = publisher_shell_contract.split_bash_command_records(
        script,
        label=f"{layer} command inventory",
    )
    signatures: list[CommandSignature] = []
    helper_stack: list[str] = []
    control_stack = list(initial_control_context)
    initial_control_depth = len(control_stack)
    for ordinal, record in enumerate(records):
        command = record.text.strip()
        owner = helper_stack[-1] if helper_stack else initial_owner
        function_match = _FUNCTION_RE.fullmatch(command)
        first_word = command.split(maxsplit=1)[0] if command else ""
        context = (*control_stack, *record.execution_scopes)
        if function_match is not None:
            executable = "@function"
            wrappers: tuple[str, ...] = ()
            argv = (function_match.group("name"),)
            redirections: tuple[tuple[str, str], ...] = ()
            stdin = stdout = stderr = "inherit"
            writes: tuple[str, ...] = ()
            accesses: tuple[tuple[str, str], ...] = ()
            events = ("helper-definition",)
        else:
            tokens = publisher_shell_contract._parse_shell_tokens(
                command,
                label=f"{layer} command {ordinal}",
            )
            compound_assignment = (
                bool(tokens)
                and _ASSIGNMENT_PREFIX_RE.match(tokens[0].text) is not None
                and ("$(" in command or "`" in command)
            )
            if compound_assignment:
                executable = "@shell-state"
                wrappers = (tokens[0].text.split("=", 1)[0] + "=",)
                argv = ()
                redirections = ()
                stdin = stdout = stderr = "inherit"
                writes = ()
                accesses = ()
                events = ("compound-assignment",)
            else:
                if not tokens or _ASSIGNMENT_PREFIX_RE.match(tokens[0].text) is None:
                    tokens = publisher_shell_contract._semantic_surface_tokens(
                        tokens,
                        label=f"{layer} command {ordinal}",
                    )
                redirections, command_tokens = _redirections(tokens)
                executable, wrappers, argv = _normalized_command(
                    command_tokens,
                    owner=owner,
                )
                stdin, stdout, stderr, explicit_writes = _stdio_and_writes(
                    redirections
                )
                accesses, writes, events = _semantic_metadata(
                    executable,
                    argv,
                    redirections,
                    explicit_writes,
                )
        signatures.append(
            CommandSignature(
                signature_id=_signature_id(
                    kind="shell",
                    layer=layer,
                    owner=owner,
                    command=command,
                    control_context=context,
                    preceding_operator=record.preceding_operator,
                    following_operator=record.following_operator,
                ),
                kind="shell",
                layer=layer,
                owner=owner,
                control_context=context,
                preceding_operator=record.preceding_operator,
                following_operator=record.following_operator,
                command=command,
                executable=executable,
                wrappers=wrappers,
                argv=argv,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                redirections=redirections,
                accesses=accesses,
                writes=writes,
                events=events,
                program_sha256=None,
            )
        )
        if function_match is not None and not record.execution_scopes:
            helper_stack.append(function_match.group("name"))
            continue
        if record.execution_scopes:
            continue
        if first_word in _CONTROL_OPENERS:
            control_stack.append(_CONTROL_OPENERS[first_word])
            continue
        if first_word in _CONTROL_CLOSERS:
            expected = _CONTROL_CLOSERS[first_word]
            if not control_stack or control_stack[-1] != expected:
                raise ValueError(f"{layer} command control frame differs")
            control_stack.pop()
            continue
        if command == "}" and helper_stack:
            helper_stack.pop()
    if helper_stack:
        raise ValueError(f"{layer} helper definition is unterminated")
    if len(control_stack) != initial_control_depth:
        raise ValueError(f"{layer} control frame is unterminated")
    return tuple(signatures)


def _python_accesses(tree: ast.AST) -> tuple[tuple[str, str], ...]:
    accesses: list[tuple[str, str]] = []
    constants = {
        target.id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance((target := node.targets[0]), ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return None

    def add(mode: str, resource: str) -> None:
        item = (mode, resource)
        if item not in accesses:
            accesses.append(item)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = None
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if function_name == "open" and node.args:
            resource = literal(node.args[0])
            if resource is None:
                continue
            mode = "r"
            if len(node.args) >= 2:
                mode = literal(node.args[1]) or mode
            for keyword in node.keywords:
                if keyword.arg == "mode":
                    mode = literal(keyword.value) or mode
            add("write" if any(marker in mode for marker in "wax+") else "read", resource)
            continue
        if function_name in {"execve", "run"} and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.List):
                values = [literal(item) for item in argument.elts]
                if values and values[0]:
                    add("execute", values[0])
                for value in values[1:]:
                    if value and value.startswith("/"):
                        add("read", value)
            else:
                resource = literal(argument)
                if resource and resource.startswith("/"):
                    add("execute", resource)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("/") and not any(
                resource == node.value for _mode, resource in accesses
            ):
                add("read", node.value)
    return tuple(accesses)


def _python_signature(
    shell_signature: CommandSignature,
    *,
    source: str,
    source_kind: str,
) -> CommandSignature:
    tree = (
        ast.parse(source, filename=f"<{shell_signature.layer}>")
        if source_kind == "source"
        else None
    )
    accesses = list(shell_signature.accesses)
    if tree is not None:
        for access in _python_accesses(tree):
            if access not in accesses:
                accesses.append(access)
    writes: list[str] = []
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "open":
                continue
            path = node.args[0]
            if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
                continue
            mode = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if isinstance(mode, str) and any(marker in mode for marker in "wax+"):
                writes.append(path.value)
    events = ["python-invocation", f"python-{source_kind}"]
    if any(path == "/mnt/supervisor/cgroup/cgroup.procs" for _mode, path in accesses):
        events.append("cgroup-membership-check")
    command = f"python:{shell_signature.signature_id}"
    return CommandSignature(
        signature_id=_signature_id(
            kind="python",
            layer=shell_signature.layer,
            owner=shell_signature.owner,
            command=command,
            control_context=shell_signature.control_context,
            preceding_operator=shell_signature.preceding_operator,
            following_operator=shell_signature.following_operator,
        ),
        kind="python",
        layer=shell_signature.layer,
        owner=shell_signature.owner,
        control_context=shell_signature.control_context,
        preceding_operator=shell_signature.preceding_operator,
        following_operator=shell_signature.following_operator,
        command=command,
        executable=shell_signature.executable,
        wrappers=shell_signature.wrappers,
        argv=shell_signature.argv,
        stdin=shell_signature.stdin,
        stdout=shell_signature.stdout,
        stderr=shell_signature.stderr,
        redirections=shell_signature.redirections,
        accesses=tuple(accesses),
        writes=tuple(dict.fromkeys((*shell_signature.writes, *writes))),
        events=tuple(events),
        program_sha256=_sha256_text(source),
    )


def build_command_signatures(run_script: str) -> tuple[CommandSignature, ...]:
    shell_sources, python_sources = _builder_sources(run_script)
    shell_signatures = [
        signature
        for layer, source in shell_sources.items()
        for signature in _shell_signatures(layer, source)
    ]
    signatures = list(shell_signatures)
    for shell_signature in (
        signature
        for signature in shell_signatures
        if posixpath.basename(signature.executable) == "python3"
    ):
        argv = shell_signature.argv
        program_index = 0
        while program_index < len(argv) and argv[program_index] in {"-I", "-S"}:
            program_index += 1
        program = argv[program_index] if program_index < len(argv) else None
        if program == "-c":
            source_index = program_index + 1
            if source_index >= len(argv):
                raise ValueError("reviewed Python -c invocation has no program")
            source = argv[source_index]
            source_kind = "source"
        elif program == "-m":
            module_index = program_index + 1
            if module_index >= len(argv):
                raise ValueError("reviewed Python -m invocation has no module")
            source = "module:" + argv[module_index]
            source_kind = "module"
        elif any("supervisor-launcher.py" in argument for argument in argv):
            source = python_sources["supervisor-launcher"][1]
            source_kind = "source"
        elif any("candidate-launcher.py" in argument for argument in argv):
            source = python_sources["candidate-launcher"][1]
            source_kind = "source"
        elif shell_signature.owner == "list_dev_mount_targets":
            source = python_sources[
                "builder-parser:list_dev_mount_targets"
            ][1]
            source_kind = "source"
        elif shell_signature.owner == "list_writable_mount_records":
            source = python_sources[
                "builder-parser:list_writable_mount_records"
            ][1]
            source_kind = "source"
        elif (
            shell_signature.owner == "builder_main"
            and argv[:4] == ("-I", "-S", "-", "$$")
        ):
            source = python_sources["builder-membership-checker"][1]
            source_kind = "source"
        else:
            raise ValueError(
                "reviewed Python invocation has no exact program authority"
            )
        signatures.append(
            _python_signature(
                shell_signature,
                source=source,
                source_kind=source_kind,
            )
        )
    return tuple(signatures)


def registry_document(signatures: Iterable[CommandSignature]) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "authority": {
            "builder_run_sha256": (
                publisher_shell_contract.REVIEWED_PATCH_RELEASE_RUN_SHA256
            ),
            "builder_shell_sha256": (
                publisher_shell_contract.REVIEWED_BUILDER_ISOLATION_SHA256
            ),
            "membership_checker_sha256": _sha256_text(
                publisher_shell_contract._PATCH_RELEASE_MEMBERSHIP_CHECKER_SOURCE
            ),
            "authority_source": "immutable-git-tree",
            "closure": list(_AUTHORITY_PATHS),
            "parser_api": [
                "literal_run_script_from_step_block",
                "split_bash_command_records",
                "_parse_shell_tokens",
                "_semantic_surface_tokens",
            ],
            "workflow": ".github/workflows/build.yml",
            "step": BUILDER_STEP_NAME,
            "parser": "scripts/workflow_pilot/publisher_shell_contract.py",
            "validator": "scripts/workflow_pilot/publisher_command_signatures.py",
        },
        "signatures": [signature.to_json() for signature in signatures],
    }


def render_registry(document: dict[str, Any]) -> bytes:
    header = {
        "schema_version": document["schema_version"],
        "authority": document["authority"],
    }
    lines = ["{"]
    lines.append(
        '  "schema_version": '
        + json.dumps(header["schema_version"], ensure_ascii=True)
        + ","
    )
    lines.append(
        '  "authority": '
        + json.dumps(
            header["authority"],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + ","
    )
    lines.append('  "signatures": [')
    signatures = document["signatures"]
    for index, signature in enumerate(signatures):
        suffix = "," if index + 1 < len(signatures) else ""
        lines.append(
            "    "
            + json.dumps(
                signature,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + suffix
        )
    lines.extend(("  ]", "}", ""))
    return "\n".join(lines).encode("ascii")


def load_registry(
    path: Path = REGISTRY_PATH,
    *,
    require_authority_path: bool = True,
    require_reviewed_digest: bool = True,
) -> tuple[CommandSignature, ...]:
    snapshot = _authority_snapshot()
    requested = Path(os.path.abspath(path))
    canonical = Path(os.path.abspath(REGISTRY_PATH))
    if require_authority_path and requested != canonical:
        raise ValueError("publisher signature registry path differs")
    if requested == canonical:
        data = snapshot.files[_REGISTRY_AUTHORITY_PATH].data
    else:
        data = requested.read_bytes()
    if require_reviewed_digest and _registry_sha256(data) != (
        REVIEWED_SIGNATURE_REGISTRY_SHA256
    ):
        raise ValueError("publisher signature registry identity differs")
    try:
        document = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("publisher signature registry JSON differs") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "authority",
        "signatures",
    }:
        raise ValueError("publisher signature registry root differs")
    if document["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ValueError("publisher signature registry schema differs")
    if document["authority"] != registry_document(())["authority"]:
        raise ValueError("publisher signature registry authority differs")
    if not isinstance(document["signatures"], list) or not document["signatures"]:
        raise ValueError("publisher signature registry signatures differ")
    signatures = tuple(
        CommandSignature.from_json(value, label=f"signature[{index}]")
        for index, value in enumerate(document["signatures"])
    )
    identities: dict[str, CommandSignature] = {}
    for signature in signatures:
        previous = identities.setdefault(signature.signature_id, signature)
        if previous != signature:
            raise ValueError("publisher signature registry identity is ambiguous")
    return signatures


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate publisher signature registry key {key!r}")
        result[key] = value
    return result


def command_inventory_errors(
    run_script: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    require_authority_path: bool = True,
    require_reviewed_digest: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
            run_script,
            label="publisher isolated candidate build run script",
        )
        builder_shell = publisher_shell_contract.builder_isolation_shell_source(
            run_script,
            label="publisher builder isolation shell",
        )
        publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
            builder_shell,
            label="publisher builder isolation shell",
        )
        publisher_shell_contract.validate_patch_release_parser_heredocs(
            builder_shell,
            label="publisher builder isolation shell",
        )
    except ValueError as error:
        errors.append(str(error))
    errors.extend(
        semantic_command_inventory_errors(
            run_script,
            registry_path=registry_path,
            require_authority_path=require_authority_path,
            require_reviewed_digest=require_reviewed_digest,
        )
    )
    return tuple(dict.fromkeys(errors))


def semantic_command_inventory_errors(
    run_script: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    require_authority_path: bool = True,
    require_reviewed_digest: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        reviewed = load_registry(
            registry_path,
            require_authority_path=require_authority_path,
            require_reviewed_digest=require_reviewed_digest,
        )
    except (OSError, ValueError) as error:
        errors.append(str(error))
        return tuple(errors)
    try:
        actual = build_command_signatures(run_script)
    except ValueError as error:
        errors.append(str(error))
        return tuple(errors)
    if len(actual) != len(reviewed):
        errors.append(
            "publisher command signature completeness differs: "
            f"expected {len(reviewed)}, got {len(actual)}"
        )
    reviewed_counts = Counter(reviewed)
    actual_counts = Counter(actual)
    if reviewed_counts != actual_counts:
        missing = reviewed_counts - actual_counts
        unexpected = actual_counts - reviewed_counts
        if missing:
            signature = next(iter(missing))
            errors.append(
                "publisher command signature is missing: "
                f"{signature.signature_id}"
            )
        if unexpected:
            signature = next(iter(unexpected))
            errors.append(
                "publisher command signature is unexpected: "
                f"{signature.signature_id}"
            )
    identities: dict[str, CommandSignature] = {}
    for signature in actual:
        previous = identities.setdefault(signature.signature_id, signature)
        if previous != signature:
            errors.append("publisher command signature generation is ambiguous")
            break
    return tuple(errors)


def assert_command_inventory(
    run_script: str,
    *,
    registry_path: Path = REGISTRY_PATH,
) -> None:
    errors = command_inventory_errors(run_script, registry_path=registry_path)
    if errors:
        raise ValueError("; ".join(errors))


def _write_registry(path: Path) -> None:
    if Path(os.path.abspath(path)) != Path(os.path.abspath(REGISTRY_PATH)):
        raise ValueError(f"registry output must identify {REGISTRY_PATH}")
    workflow = _secure_read_authority_file(
        ROOT,
        _WORKFLOW_AUTHORITY_PATH,
        expected_data=None,
        expected_mode="100644",
    ).decode("utf-8")
    _secure_read_authority_file(
        ROOT,
        _REGISTRY_AUTHORITY_PATH,
        expected_data=None,
        expected_mode="100644",
    )
    run_script = publisher_builder_run_script(workflow)
    signatures = build_command_signatures(run_script)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_TRUNC
        | os.O_CLOEXEC
        | _O_NOFOLLOW,
    )
    try:
        data = render_registry(registry_document(signatures))
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short publisher registry write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-registry", action="store_true")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.write_registry == arguments.check:
        parser.error("select exactly one of --write-registry or --check")
    try:
        if arguments.write_registry:
            _write_registry(REGISTRY_PATH)
        else:
            workflow = _authority_snapshot().files[
                _WORKFLOW_AUTHORITY_PATH
            ].data.decode("utf-8")
            assert_command_inventory(publisher_builder_run_script(workflow))
    except (OSError, ValueError) as error:
        print(f"publisher-command-signatures: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
