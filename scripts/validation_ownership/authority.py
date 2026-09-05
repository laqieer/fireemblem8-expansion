"""Exact-tree API extracted from PR186, without graph/reporter dependencies."""

from __future__ import annotations

import hashlib
import json
import os
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


def git(root: Path, budget: ProbeBudget, *args: str):
    result = budget.run(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
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


def git_tree_entries(root: Path, revision: str = "HEAD", *, budget=None):
    budget = ProbeBudget() if budget is None else budget
    result = {}
    for row in git(root, budget, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, kind, oid = text(header, "Git entry", "ascii").split()
        name = relative_path(text(path, "Git path"))
        if name in result:
            raise MakeProbeError("Git tree has a duplicate path")
        result[name] = GitTreeEntry(name, mode, kind, oid)
    if not result:
        raise MakeProbeError("empty authority tree")
    return result


class AuthorityLoader:
    """PR186-compatible entry/read API; live reads reject every symlink component."""

    def __init__(self, root, entries, revision=None, scratch_root=None):
        self.root = Path(os.path.abspath(root))
        self.entries = entries
        self.revision = revision
        self.scratch_root = scratch_root
        self.budget = None
        self.live_modes = {}
        if self.root.is_symlink() or not self.root.is_dir():
            raise MakeProbeError("authority root must be a non-symlink directory")

    def entry(self, relative, label):
        name = relative_path(str(relative))
        entry = self.entries.get(name)
        if entry is None or entry.path != name or entry.mode not in {"100644", "100755"} or entry.object_type != "blob":
            raise MakeProbeError(f"{label}: {name!r} is not an admitted regular Git blob")
        return entry

    def read_blob(self, relative, label):
        entry = self.entry(relative, label)
        if self.revision is not None:
            if self.budget is None:
                raise MakeProbeError("immutable reads require the caller's aggregate budget")
            return git(self.root, self.budget, "cat-file", "blob", entry.object_id)
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
                if self.budget and before.st_size > self.budget.limits.file_bytes:
                    self.budget.reject("authority blob exceeds byte bound")
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
        parts = relative_path(relative).split("/")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = following
            return os.fsencode(os.readlink(parts[-1], dir_fd=descriptor))
        except OSError as error:
            raise MakeProbeError("live symlink changed type or has an unsafe ancestor") from error
        finally:
            os.close(descriptor)


class Snapshot:
    """An immutable in-memory execution view, not semantic owner identity."""

    def __init__(self, loader: AuthorityLoader, budget: ProbeBudget):
        self.files = {}
        self.modes = {}
        records = []
        loader.budget = budget
        if not 1 <= len(loader.entries) <= budget.limits.entries:
            budget.reject("snapshot entry count exceeds aggregate bound")
        immutable = {}
        if loader.revision is not None:
            entries = [
                entry for _, entry in sorted(loader.entries.items())
                if entry.mode in {"100644", "100755"} and entry.object_type == "blob"
            ]
            result = budget.run(
                ["/usr/bin/git", "--no-replace-objects", "-C", str(loader.root), "cat-file", "--batch"],
                env=ENVIRONMENT,
                input_data="".join(entry.object_id + "\n" for entry in entries).encode("ascii"),
                output_limit=budget.limits.snapshot_bytes,
                category="snapshot",
            )
            if result.returncode:
                raise MakeProbeError(f"immutable blob stream failed: {result.stderr!r}")
            offset = 0
            for entry in entries:
                end = result.stdout.find(b"\n", offset)
                if end < 0:
                    raise MakeProbeError("truncated immutable blob header")
                header = text(result.stdout[offset:end], "Git blob header", "ascii").split()
                if len(header) != 3 or header[:2] != [entry.object_id, "blob"] or not header[2].isdigit():
                    raise MakeProbeError("unexpected immutable blob identity/type")
                size = int(header[2])
                if size > budget.limits.file_bytes:
                    raise MakeProbeError("immutable blob exceeds file bound")
                offset = end + 1
                immutable[entry.path] = result.stdout[offset:offset + size]
                offset += size
                if result.stdout[offset:offset + 1] != b"\n":
                    raise MakeProbeError("truncated immutable blob payload")
                offset += 1
            if offset != len(result.stdout):
                raise MakeProbeError("trailing immutable blob stream")
        for name, entry in sorted(loader.entries.items()):
            budget.remaining()
            relative_path(name)
            if entry.mode in {"100644", "100755"} and entry.object_type == "blob":
                data = immutable[name] if loader.revision is not None else loader.read_blob(name, "execution snapshot")
                budget.charge("snapshot", len(data) + len(name.encode("utf-8")) + 64)
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
                identity = entry.object_id
            records.append((name, self.modes.get(name, entry.mode), entry.object_type, identity))
        self.digest = hashlib.sha256(encoded(records)).hexdigest()

    def materialize(self, destination: Path, paths, budget: ProbeBudget):
        for name in sorted(paths):
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
            if name not in self.files:
                raise MakeProbeError(f"missing declared owner input {name!r}")
            result.append((name, self.modes[name], hashlib.sha256(self.files[name]).hexdigest()))
        return result
