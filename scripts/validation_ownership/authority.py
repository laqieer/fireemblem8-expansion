"""Exact-tree API extracted from PR186, without graph/reporter dependencies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .budget import MakeProbeError, ProbeBudget, text


ENVIRONMENT = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def relative_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise MakeProbeError("authority path must be canonical and repository-relative")
    try:
        if len(value.encode("utf-8")) > 4096:
            raise MakeProbeError("authority path exceeds its byte bound")
    except UnicodeEncodeError as error:
        raise MakeProbeError("authority path is not strict UTF-8") from error
    return value


def parse_json(data: bytes, boundary: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MakeProbeError(f"{boundary} has duplicate JSON keys")
            result[key] = value
        return result

    def constant(value):
        raise MakeProbeError(f"{boundary} has non-finite JSON")

    try:
        return json.loads(text(data, boundary), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as error:
        raise MakeProbeError(f"{boundary} is malformed JSON") from error


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def git_command(root: Path, git_dir: Path | None = None):
    return [
        "/usr/bin/git", "--no-replace-objects",
        *(["-C", str(root)] if git_dir is None else ["--git-dir", str(git_dir)]),
    ]


def git(root: Path, budget: ProbeBudget, *args: str, git_dir: Path | None = None):
    result = budget.run(
        [*git_command(root, git_dir), *args],
        env=ENVIRONMENT, output_limit=budget.limits.file_bytes,
    )
    if result.returncode:
        raise MakeProbeError(f"trusted Git failed: {result.stderr!r}")
    return result.stdout


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str
    git_dir: Path | None = None


@dataclass(frozen=True)
class GitlinkSource:
    """An explicitly requested gitlink and its local object database, not a pin override."""

    path: str
    git_dir: Path


def _git_directory(path):
    if not isinstance(path, (str, os.PathLike)):
        raise MakeProbeError("gitlink database must be a pathname")
    directory = Path(path)
    if not directory.is_absolute() or ".." in directory.parts:
        raise MakeProbeError("gitlink database must be an absolute canonical directory")
    try:
        for component in (directory, *directory.parents):
            if not stat.S_ISDIR(component.lstat().st_mode):
                raise MakeProbeError("gitlink database has a non-directory/symlink component")
    except OSError as error:
        raise MakeProbeError("gitlink object database is unavailable") from error
    return directory


class GitTreeEntries(dict[str, GitTreeEntry]):
    """An entry map bound to the report budget that captured it."""

    def __init__(self, entries, *, budget: ProbeBudget):
        if not isinstance(budget, ProbeBudget):
            raise MakeProbeError("authority capture requires an explicit report budget")
        budget.remaining()
        super().__init__(entries)
        self.budget = budget
        self.capture: tuple[Path, str] | None = None


def _tree_entries(root, revision, budget, *, git_dir=None):
    result = {}
    for row in git(root, budget, "ls-tree", "-rz", "--full-tree", revision, git_dir=git_dir).split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, kind, oid = text(header, "Git entry", "ascii").split()
        name = relative_path(text(path, "Git path"))
        if name in result:
            raise MakeProbeError("Git tree has a duplicate path")
        if len(result) >= budget.limits.entries:
            budget.reject("captured tree entry bound exceeded")
        result[name] = GitTreeEntry(name, mode, kind, oid, git_dir)
    return result


def git_tree_entries(
    root: Path, revision: str = "HEAD", *, budget: ProbeBudget,
    gitlinks: tuple[GitlinkSource, ...] = (),
):
    if not isinstance(budget, ProbeBudget):
        raise MakeProbeError("authority capture requires an explicit report budget")
    result = _tree_entries(root, revision, budget)
    if not result:
        raise MakeProbeError("empty authority tree")
    requested = set()
    for index, source in enumerate(gitlinks):
        budget.remaining()
        if index >= budget.limits.pending:
            budget.reject("gitlink admission count exceeds report bound")
        if not isinstance(source, GitlinkSource):
            raise MakeProbeError("gitlink admission requires a typed GitlinkSource")
        name = relative_path(source.path)
        entry = result.get(name)
        if name in requested or entry is None or entry.mode != "160000" or entry.object_type != "commit":
            raise MakeProbeError("requested source is not a unique captured gitlink")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", entry.object_id):
            raise MakeProbeError("invalid captured gitlink pin")
        requested.add(name)
        directory = _git_directory(source.git_dir)
        if git(root, budget, "cat-file", "-t", entry.object_id, git_dir=directory) != b"commit\n":
            raise MakeProbeError("captured gitlink pin is not a commit")
        children = _tree_entries(root, entry.object_id, budget, git_dir=directory)
        if len(result) + len(children) > budget.limits.entries:
            budget.reject("expanded source entry bound exceeded")
        for child in children.values():
            if child.mode not in {"100644", "100755"} or child.object_type != "blob":
                raise MakeProbeError("gitlink source contains a nonregular/nested subtree")
            path = relative_path(name + "/" + child.path)
            if path in result:
                raise MakeProbeError("gitlink source path conflicts with captured tree")
            budget.charge("snapshot", len(path.encode("utf-8")) + 128)
            result[path] = GitTreeEntry(path, child.mode, child.object_type, child.object_id, directory)
        result[name] = GitTreeEntry(name, entry.mode, entry.object_type, entry.object_id, directory)
    captured = GitTreeEntries(result, budget=budget)
    captured.capture = (Path(os.path.abspath(root)), revision)
    budget.charge("control", len(encoded([str(captured.capture[0]), revision])))
    return captured


class AuthorityLoader:
    """Report-bound entry/read API; live reads reject every symlink component."""

    def __init__(self, root, entries, revision=None, scratch_root=None, *, budget: ProbeBudget):
        if (
            not isinstance(entries, GitTreeEntries) or not isinstance(budget, ProbeBudget)
            or entries.budget is not budget
        ):
            raise MakeProbeError("authority loader requires its capture's report budget")
        budget.remaining()
        self.root = Path(os.path.abspath(root))
        if entries.capture is not None and (
            self.root != entries.capture[0]
            or revision is not None and revision != entries.capture[1]
        ):
            raise MakeProbeError("authority loader differs from its captured repository/revision")
        self.entries = entries
        self.revision = revision
        self.scratch_root = scratch_root
        self.budget = budget
        self.live_modes = {}
        if any(entry.git_dir is not None for entry in entries.values()) and (
            revision is None or entries.capture != (self.root, revision)
        ):
            raise MakeProbeError("gitlink source admission requires a captured immutable superproject view")
        if self.root.is_symlink() or not self.root.is_dir():
            raise MakeProbeError("authority root must be a non-symlink directory")

    def entry(self, relative, label):
        self.budget.remaining()
        name = relative_path(str(relative))
        entry = self.entries.get(name)
        if entry is None or entry.path != name or entry.mode not in {"100644", "100755"} or entry.object_type != "blob":
            raise MakeProbeError(f"{label}: {name!r} is not an admitted regular Git blob")
        return entry

    def read_blob(self, relative, label):
        entry = self.entry(relative, label)
        if self.revision is not None:
            return git(self.root, self.budget, "cat-file", "blob", entry.object_id, git_dir=entry.git_dir)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = entry.path.split("/")
            for part in parts[:-1]:
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = following
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode):
                    raise MakeProbeError(f"{label}: nonregular live blob")
                if before.st_size > self.budget.limits.file_bytes:
                    self.budget.reject("authority blob exceeds byte bound")
                self.budget.charge("snapshot", before.st_size)
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(before.st_size + 1)
                after = os.fstat(fd)
                if (
                    len(data) != before.st_size
                    or (before.st_mtime_ns, before.st_ctime_ns, before.st_size)
                    != (after.st_mtime_ns, after.st_ctime_ns, after.st_size)
                ):
                    raise MakeProbeError(f"{label}: live blob changed while snapshotting")
                self.live_modes[entry.path] = "100755" if before.st_mode & stat.S_IXUSR else "100644"
                return data
            finally:
                os.close(fd)
        except OSError as error:
            raise MakeProbeError(f"{label}: unsafe/unavailable live input {entry.path}") from error
        finally:
            os.close(descriptor)

    def read_json(self, relative, label):
        return parse_json(self.read_blob(relative, label), label)

    def read_link(self, relative):
        self.budget.remaining()
        parts = relative_path(relative).split("/")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = following
            data = os.fsencode(os.readlink(parts[-1], dir_fd=descriptor))
            self.budget.charge("snapshot", len(data))
            return data
        except OSError as error:
            raise MakeProbeError("live symlink changed type or has an unsafe ancestor") from error
        finally:
            os.close(descriptor)


class Snapshot:
    """An immutable in-memory execution view, not semantic owner identity."""

    def __init__(self, loader: AuthorityLoader, budget: ProbeBudget, *, reuse: Snapshot | None = None):
        if (
            not isinstance(budget, ProbeBudget) or budget is not loader.budget
            or budget is not loader.entries.budget
        ):
            raise MakeProbeError("snapshot requires its authority's report budget")
        budget.remaining()
        if reuse is not None and (
            not isinstance(reuse, Snapshot) or reuse.budget is not budget
            or reuse.loader.root != loader.root
            or loader.revision is None or loader.entries.capture != (loader.root, loader.revision)
        ):
            raise MakeProbeError("snapshot reuse requires same-report repository capture authority")
        self.budget = budget
        self.loader = loader
        self.files = {}
        self.modes = {}
        self.reused_paths = set()
        self.gitlink_roots = {
            name for name, entry in loader.entries.items()
            if entry.mode == "160000" and entry.object_type == "commit" and entry.git_dir is not None
        }
        records = []
        if not 1 <= len(loader.entries) <= budget.limits.entries:
            budget.reject("snapshot entry count exceeds aggregate bound")
        immutable = {}
        if loader.revision is not None:
            entries = [
                entry for _, entry in sorted(loader.entries.items())
                if entry.mode in {"100644", "100755"} and entry.object_type == "blob"
            ]
            if reuse is not None and reuse.loader.revision is not None and (
                reuse.loader.entries.capture == (loader.root, reuse.loader.revision)
            ):
                for entry in entries:
                    if reuse.loader.entries.get(entry.path) == entry and entry.path in reuse.files:
                        if len(reuse.files[entry.path]) > budget.limits.file_bytes:
                            raise MakeProbeError("reused immutable blob exceeds file bound")
                        immutable[entry.path] = reuse.files[entry.path]
                        self.reused_paths.add(entry.path)
            entries = [entry for entry in entries if entry.path not in self.reused_paths]
            for directory in dict.fromkeys(entry.git_dir for entry in entries):
                batch = [entry for entry in entries if entry.git_dir == directory]
                result = budget.run(
                    [*git_command(loader.root, directory), "cat-file", "--batch"],
                    env=ENVIRONMENT,
                    input_data="".join(entry.object_id + "\n" for entry in batch).encode("ascii"),
                    output_limit=budget.limits.snapshot_bytes,
                    category="snapshot",
                )
                if result.returncode:
                    raise MakeProbeError(f"immutable blob stream failed: {result.stderr!r}")
                payload = result.stdout
                offset = 0
                for entry in batch:
                    end = payload.find(b"\n", offset)
                    if end < 0:
                        raise MakeProbeError("truncated immutable blob header")
                    header = text(payload[offset:end], "Git blob header", "ascii").split()
                    if len(header) != 3 or header[:2] != [entry.object_id, "blob"] or not header[2].isdigit():
                        raise MakeProbeError("unexpected immutable blob identity/type")
                    size = int(header[2])
                    if size > budget.limits.file_bytes:
                        raise MakeProbeError("immutable blob exceeds file bound")
                    offset = end + 1
                    immutable[entry.path] = payload[offset:offset + size]
                    offset += size
                    if payload[offset:offset + 1] != b"\n":
                        raise MakeProbeError("truncated immutable blob payload")
                    offset += 1
                if offset != len(payload):
                    raise MakeProbeError("trailing immutable blob stream")
        for name, entry in sorted(loader.entries.items()):
            budget.remaining()
            relative_path(name)
            if entry.mode in {"100644", "100755"} and entry.object_type == "blob":
                data = immutable[name] if loader.revision is not None else loader.read_blob(name, "execution snapshot")
                budget.charge(
                    "snapshot", (0 if name in self.reused_paths else len(data))
                    + len(name.encode("utf-8")) + 64,
                )
                self.files[name] = data
                self.modes[name] = entry.mode if loader.revision is not None else loader.live_modes[name]
                identity = hashlib.sha256(data).hexdigest()
            elif entry.mode == "120000" and loader.revision is None:
                data = loader.read_link(name)
                budget.charge("snapshot", len(data) + len(name.encode("utf-8")) + 64)
                identity = hashlib.sha256(data).hexdigest()
            else:
                # Gitlinks/symlinks participate in integrity but are not executable
                # or silently dereferenced. A consumer must explicitly admit them.
                identity = (entry.object_id, "admitted") if name in self.gitlink_roots else entry.object_id
            records.append((name, self.modes.get(name, entry.mode), entry.object_type, identity))
        self.digest = hashlib.sha256(encoded(records)).hexdigest()

    def materialize(self, destination: Path, paths, budget: ProbeBudget):
        if budget is not self.budget:
            raise MakeProbeError("materialization requires its snapshot's report budget")
        budget.remaining()
        selected = set(paths)
        for name in sorted(selected):
            budget.remaining()
            relative_path(name)
            if name not in self.files:
                raise MakeProbeError(f"selected input {name!r} is not a regular snapshot blob")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.files[name])
            # Preserve observable Git executable bits; the mount is still noexec
            # and all source writes/dispatch are independently denied.
            target.chmod(0o755 if self.modes[name] == "100755" else 0o644)
    def owners(self, paths):
        result = []
        for name in sorted(set(paths)):
            relative_path(name)
            if name in self.gitlink_roots:
                result.append((name, "160000", self.loader.entries[name].object_id))
                continue
            if name not in self.files:
                raise MakeProbeError(f"missing declared owner input {name!r}")
            result.append((name, self.modes[name], hashlib.sha256(self.files[name]).hexdigest()))
        return result
