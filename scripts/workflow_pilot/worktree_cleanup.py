"""Conservative, opt-in cleanup after the existing master completion gate.

Run from a source checkout with ``python3 -m scripts.workflow_pilot.worktree_cleanup``.
GitHub observations are cached only during one planning/revalidation pass.
Only normal ``git worktree remove`` can mutate a selected workspace.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
from urllib.parse import urlencode


BUILD_PATH = ".github/workflows/build.yml"
PAGE_SIZE = 100
MAX_RECORDS = 1000
MAX_OUTPUT = 16 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
SOURCE_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_REFS = ("refs/worktree", "refs/bisect", "refs/rewritten")


class Retain(ValueError):
    """Evidence is insufficient; preserve the workspace."""


def require(condition, reason):
    if not condition:
        raise Retain(reason)


def positive(value):
    return type(value) is int and value > 0


def commit(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "invalid commit identity")
    return value


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"), "missing CI/PR timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Retain("invalid CI/PR timestamp") from error


def execute(command, cwd, *, input=None, returncodes=(0,)):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_NO_LAZY_FETCH="1")
    require(input is None or len(input) <= MAX_OUTPUT, "command input exceeds safety bound")
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, input=input, capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Retain(f"{command[0]} unavailable or timed out") from error
    require(len(result.stdout) <= MAX_OUTPUT, f"{command[0]} output exceeds safety bound")
    detail = os.fsdecode(result.stderr[:1000]).strip() or "no stderr output"
    require(result.returncode in returncodes,
            f"{command[0]} failed (exit {result.returncode}): {detail}")
    return os.fsdecode(result.stdout)


def git(root, *arguments, input=None):
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
               "-c", "core.hooksPath=" + os.devnull, "-C", str(root)]
    # Git 2.43 ignores GIT_NO_LAZY_FETCH. Config reads do not access objects;
    # reject partial/promisor configuration before *every* other Git command.
    promisor = execute(
        command + ["config", "--includes", "--null", "--name-only", "--get-regexp",
                   r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$"],
        root, returncodes=(0, 1),
    )
    require(not promisor, "partial/promisor Git configuration requires preservation; "
            "cleanup cannot guarantee an inert object lookup on all supported Git versions")
    return execute(command + list(arguments), root, input=input)


class GitHub:
    """Small gh-only read adapter, not an HTTP client or durable ledger."""

    def __init__(self, root):
        self.root = root
        self.cache = {}

    def clear(self):
        self.cache.clear()

    def request(self, endpoint):
        try:
            return json.loads(execute(
                ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
                self.root,
            ))
        except (ValueError, RecursionError) as error:
            raise Retain("invalid GitHub JSON response") from error

    def get(self, endpoint):
        if endpoint not in self.cache:
            self.cache[endpoint] = self.request(endpoint)
        return self.cache[endpoint]

    def pages(self, endpoint, key=None):
        result, ids, total = [], set(), None
        for page in range(1, MAX_RECORDS // PAGE_SIZE + 2):
            separator = "&" if "?" in endpoint else "?"
            data = self.get(f"{endpoint}{separator}per_page={PAGE_SIZE}&page={page}")
            if key:
                require(isinstance(data, dict), "malformed paginated GitHub object")
                count = data.get("total_count")
                require(type(count) is int and 0 <= count <= MAX_RECORDS,
                        "missing or excessive GitHub total_count")
                require(total is None or total == count, "GitHub pagination changed")
                total = count
                rows = data.get(key)
            else:
                rows = data
            require(isinstance(rows, list) and len(rows) <= PAGE_SIZE,
                    "malformed GitHub page")
            if total is not None:
                require(len(rows) == min(PAGE_SIZE, max(0, total - len(result))),
                        "incomplete GitHub pagination")
            for row in rows:
                require(isinstance(row, dict) and positive(row.get("id")),
                        "missing GitHub record identity")
                require(row["id"] not in ids, "duplicate GitHub record/page")
                ids.add(row["id"])
                result.append(row)
            require(len(result) <= MAX_RECORDS, "GitHub result exceeds safety bound")
            if (total is not None and len(result) == total) or len(rows) < PAGE_SIZE:
                return result
        raise Retain("GitHub pagination exceeds safety bound")


def inventory(root):
    records, current = {}, {}
    for field in git(root, "worktree", "list", "--porcelain", "-z").split("\0"):
        if not field:
            if current:
                require("worktree" in current, "malformed Git worktree registration")
                path = current["worktree"]
                require(path not in records, "duplicate Git worktree registration")
                records[path] = current
                current = {}
            continue
        key, _, value = field.partition(" ")
        require(key in {"worktree", "HEAD", "branch", "bare", "detached", "locked", "prunable"}
                and key not in current, "ambiguous Git worktree registration")
        current[key] = value
    require(not current, "incomplete Git worktree inventory")
    return records


def inside(path, directory):
    return path == directory or directory in path.parents


def require_procfs():
    require(sys.platform == "linux" and Path("/proc/self/mountinfo").is_file(),
            "active-process and mount checks require Linux /proc")


def process_cwds():
    """Same-owner agents must be inspectable; preserve assigned paths as well."""
    require_procfs()
    proc = Path("/proc")
    result = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        owner = None
        try:
            owner = entry.stat().st_uid
            result.append((int(entry.name), (entry / "cwd").readlink()))
        except FileNotFoundError:
            continue  # Exited processes and kernel threads have no cwd.
        except PermissionError as error:
            if owner is None or owner == os.getuid():
                raise Retain(f"cannot inspect same-owner process {entry.name}") from error
    return result


def mount_paths():
    # stat/is_mount cannot distinguish a same-device bind mount from an ordinary directory.
    require_procfs()
    data = Path("/proc/self/mountinfo").read_bytes()
    require(len(data) <= MAX_OUTPUT, "mount inventory exceeds safety bound")
    result = []
    for line in data.splitlines():
        fields = line.split()
        require(len(fields) >= 10 and b"-" in fields[6:], "incomplete Linux mount inventory")
        require(re.search(rb"\\(?!040|011|012|134)", fields[4]) is None,
                "invalid Linux mount inventory escape")
        raw = re.sub(rb"\\(040|011|012|134)",
                     lambda match: bytes([int(match[1], 8)]), fields[4])
        require(raw.startswith(b"/") and b"\0" not in raw, "invalid Linux mount inventory path")
        result.append(Path(os.fsdecode(raw)))
    return result


def empty_gitlink_directory(directory):
    """Observe emptiness without opening any submodule Git/configuration or following links."""
    def identity(info):
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns

    try:
        require(directory == directory.resolve(), "symlinked gitlink directory")
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode), "gitlink path is not a real directory")
        before = identity(info)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            require(identity(os.fstat(descriptor)) == before, "gitlink directory changed")
            with os.scandir(descriptor) as entries:
                require(next(entries, None) is None,
                        f"nonempty submodule/gitlink directory requires preservation: {directory}")
        finally:
            os.close(descriptor)
        require(directory == directory.resolve() and identity(directory.lstat()) == before,
                "gitlink directory changed during observation")
    except (FileNotFoundError, RuntimeError) as error:
        raise Retain(f"missing or ambiguous gitlink directory: {directory}") from error
    return before


def allocated_size(path, gitlinks=()):
    """Observe allocated blocks, not physical bytes freed; never follow links."""
    seen, pending, size = set(), [path], 0
    empty_directories = {path / row[0]: tuple(row[3:]) for row in gitlinks}
    device = path.lstat().st_dev
    while pending:
        item = pending.pop()
        info = item.lstat()
        require(info.st_dev == device, "nested filesystem requires manual preservation")
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode),
                "special file requires preservation")
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        require(len(seen) <= 1_000_000, "workspace size scan exceeds safety bound")
        size += info.st_blocks * 512
        if item in empty_directories:
            require(not item.is_mount(), "nested mount requires preservation")
            require(empty_gitlink_directory(item) == empty_directories[item],
                    "gitlink directory changed since observation")
            continue
        if stat.S_ISDIR(info.st_mode):
            require(item == path or not item.is_mount(), "nested mount requires preservation")
            with os.scandir(item) as entries:
                entries = list(entries)
                names = {entry.name for entry in entries}
                require(".git" not in names or item == path,
                        "nested Git repository/submodule requires preservation")
                # Bare and separated Git directories need not contain a .git
                # entry or have a .git suffix. Incomplete metadata is held too.
                require(not (
                    {"objects", "refs"} <= names
                    or {"gitdir", "commondir"} <= names
                    or ("HEAD" in names and names.intersection(
                        {"objects", "refs", "packed-refs", "reftable", "config", "commondir", "gitdir"}
                    ))
                ), "nested Git repository/bare metadata requires preservation")
                for entry in entries:
                    pending.append(Path(entry.path))
    return size


def generated_ignored(name, tracked):
    """Unknown ignored user files (including saves and baseroms) are not trash."""
    path = Path(name)
    if path.parts[0] in {"build", ".dep", ".deps"} or "__pycache__" in path.parts:
        return True
    if name in {"fireemblem8.gba", "fireemblem8.elf", "fireemblem8.map", "fireemblem8.sym",
                "objects.lst", "src/msg_data.c", "include/msg_data.h"}:
        return True
    if len(path.parts) == 3 and path.parts[0] == "tools":
        tool = path.parts[1]
        if (tool in {"aif2pcm", "bin2c", "gbagfx", "jsonproc", "mid2agb", "preproc", "scaninc", "textencode"}
                and path.name in {tool, tool + ".exe"} and str(path.parent / "Makefile") in tracked):
            return True
    if path.suffix == ".o":
        return any(str(path.with_suffix(ext)) in tracked for ext in (".c", ".cc", ".cpp", ".s", ".S"))
    if path.suffix == ".s":
        return str(path.with_suffix(".c")) in tracked
    source = name.removesuffix(".lz")
    if name.endswith(".lz") and source in tracked:
        return True
    source = source.removesuffix(".fk")
    image = re.fullmatch(r"(.+)\.(?:feimg|fetsa)[1-4]\.bin", source)
    if image:
        return image[1] + ".png" in tracked
    for suffix, inputs in (
        (".4bpp", (".png",)),
        (".8bpp", (".png",)),
        (".gbapal", (".png", ".pal")),
        (".4bpp.h", (".png", ".4bpp")),
    ):
        if source.endswith(suffix):
            stem = source[:-len(suffix)]
            return any(stem + ext in tracked for ext in inputs)
    return False


def index_extensions(data):
    """Bound and classify the private DIRC format; unknown extensions can hold recovery data."""
    require(len(data) >= 32, "incomplete private Git index")
    signature, version, count = struct.unpack_from(">4sII", data)
    require(signature == b"DIRC" and version in {2, 3, 4}, "unsupported private Git index format")
    limit = len(data) - 20
    require(hashlib.sha1(data[:limit], usedforsecurity=False).digest() == data[limit:],
            "invalid private Git index checksum")
    require(count <= limit // 62, "invalid private Git index entry count")
    offset, previous_length = 12, 0
    for _ in range(count):
        start = offset
        require(offset + 62 <= limit, "truncated private Git index entry")
        flags = int.from_bytes(data[offset + 60:offset + 62], "big")
        offset += 62
        if flags & 0x4000:
            require(version >= 3 and offset + 2 <= limit, "invalid private Git index flags")
            offset += 2
        remove = 0
        if version == 4:
            for size in range(5):
                require(offset < limit, "truncated private Git index path prefix")
                value = data[offset]
                offset += 1
                remove = ((remove + 1) << 7 if size else 0) + (value & 0x7f)
                if not value & 0x80:
                    break
            else:
                raise Retain("over-bound private Git index path prefix")
            require(remove <= previous_length, "invalid private Git index path prefix")
        end = data.find(b"\0", offset, limit)
        require(end != -1, "unterminated private Git index path")
        previous_length = previous_length - remove + end - offset if version == 4 else end - offset
        offset = end + 1 if version == 4 else start + ((end + 1 - start + 7) // 8) * 8
        require(offset <= limit, "truncated private Git index padding")
    extensions = {}
    while offset < limit:
        require(offset + 8 <= limit, "truncated private Git index extension")
        name, size = struct.unpack_from(">4sI", data, offset)
        offset += 8
        require(offset + size <= limit, "truncated private Git index extension data")
        # TREE/UNTR/FSMN and entry-offset tables are reconstructible caches.
        # Split/sparse indexes and unfamiliar optional extensions are not assumed disposable.
        require(name in {b"TREE", b"REUC", b"UNTR", b"FSMN", b"EOIE", b"IEOT"},
                f"private Git index extension {name!r} requires preservation")
        require(name not in extensions, "duplicate private Git index extension")
        extensions[name] = data[offset:offset + size]
        offset += size
    return extensions


def resolve_undo_entries(path, data):
    """Cross-check binary REUC records with Git so no silently ignored record is lost."""
    expected, offset = [], 0

    def field():
        nonlocal offset
        end = data.find(b"\0", offset)
        require(end != -1, "truncated private Git resolve-undo field")
        value, offset = data[offset:end], end + 1
        return value

    while offset < len(data):
        name = field()
        require(name, "empty private Git resolve-undo path")
        modes = [field() for _ in range(3)]
        for stage, mode in enumerate(modes, 1):
            require(re.fullmatch(rb"[0-7]{1,6}", mode), "invalid private Git resolve-undo mode")
            value = int(mode, 8)
            if not value:
                continue
            require(value in {0o100644, 0o100755, 0o120000, 0o160000},
                    "unsupported private Git resolve-undo mode")
            require(offset + 20 <= len(data), "truncated private Git resolve-undo object")
            oid = data[offset:offset + 20].hex()
            offset += 20
            expected.append((os.fsdecode(name), f"{value:06o}", oid, stage))
    observed = []
    raw = git(path, "ls-files", "--resolve-undo", "-z")
    require(not raw or raw.endswith("\0"), "incomplete Git resolve-undo inventory")
    for record in raw.split("\0")[:-1]:
        fields, separator, name = record.partition("\t")
        fields = fields.split(" ")
        require(separator and name and len(fields) == 3
                and re.fullmatch(r"[0-7]{6}", fields[0]) and fields[2] in {"1", "2", "3"},
                "malformed Git resolve-undo inventory")
        observed.append((name, fields[0], commit(fields[1]), int(fields[2])))
    require(len({(row[0], row[3]) for row in expected}) == len(expected)
            and sorted(expected) == sorted(observed),
            "private Git resolve-undo records differ from Git inventory")
    return sorted(observed)


def private_recovery(path, gitdir):
    """Account for every object whose last recovery record removal could erase."""
    objects, budget = set(), MAX_OUTPUT

    def read_record(file):
        nonlocal budget
        require(stat.S_ISREG(file.lstat().st_mode),
                f"private Git recovery metadata is not a regular file: {file.name}")
        with os.fdopen(os.open(file, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            data = source.read(budget + 1)
        budget -= len(data)
        require(budget >= 0, "private Git recovery metadata exceeds safety bound")
        return data

    def add(value, *, null=False):
        value = commit(value)
        if not null or value != "0" * 40:
            objects.add(value)

    files = list(gitdir.iterdir())
    require(len(files) <= MAX_RECORDS, "private Git metadata inventory exceeds safety bound")
    names = {file.name for file in files}
    require({"HEAD", "index", "gitdir", "commondir"} <= names, "incomplete private Git metadata")
    structural = {"index", "gitdir", "commondir"}
    messages = {"MERGE_MSG", "SQUASH_MSG", "TAG_EDITMSG", "NOTES_EDITMSG"}
    extensions = {}
    for file in files:
        require(file.name not in messages
                and (file.name in structural | {"logs", "refs", "COMMIT_EDITMSG"}
                     or re.fullmatch(r"[A-Z_]+", file.name)),
                f"private Git metadata {file.name!r} requires preservation")
        if file.name == "logs":
            continue
        if file.name == "refs":
            # Newer Git versions initialize empty ref namespaces in linked worktrees.
            pending, visited = [file], 0
            while pending:
                directory = pending.pop()
                visited += 1
                require(stat.S_ISDIR(directory.lstat().st_mode),
                        "private Git reference data or symlink requires preservation")
                for entry in directory.iterdir():
                    pending.append(entry)
                    require(visited + len(pending) <= MAX_RECORDS,
                            "private Git reference inventory exceeds safety bound")
            continue
        data = read_record(file)
        if file.name in structural:
            if file.name == "index":
                extensions = index_extensions(data)
            continue
        if file.name == "COMMIT_EDITMSG":
            head = git(path, "cat-file", "commit", "HEAD")
            require(os.fsdecode(data) == head.partition("\n\n")[2],
                    "private Git recovery COMMIT_EDITMSG contains an unpreserved edit buffer")
            continue
        lines = os.fsdecode(data).splitlines()
        if file.name == "FETCH_HEAD":
            for line in lines:
                fields = line.split("\t", 2)
                require(len(fields) == 3 and fields[1] in {"", "not-for-merge"},
                        "malformed private Git recovery FETCH_HEAD")
                add(fields[0])
        else:
            require(len(lines) == 1, "malformed private Git recovery pseudoref")
            if lines[0].startswith("ref: "):
                add(git(path, "rev-parse", "--verify", file.name).strip())
            else:
                add(lines[0])

    undo = resolve_undo_entries(path, extensions.get(b"REUC", b""))
    objects.update(row[2] for row in undo)
    logs = gitdir / "logs"
    pending = [logs] if os.path.lexists(logs) else []
    visited = 0
    while pending:
        file = pending.pop()
        visited += 1
        require(visited <= MAX_RECORDS, "private Git recovery log inventory exceeds safety bound")
        mode = file.lstat().st_mode
        require(not stat.S_ISLNK(mode), "private Git recovery logs are symlinked")
        if stat.S_ISDIR(mode):
            pending.extend(file.iterdir())
            continue
        for line in os.fsdecode(read_record(file)).splitlines():
            fields = line.split(" ", 2)
            require(len(fields) == 3, "malformed private Git recovery reflog")
            # Both sides matter, including an old object absent from every
            # other entry after a reflog expiration or rewrite.
            add(fields[0], null=True)
            add(fields[1], null=True)

    shared = set()
    for row in git(path, "for-each-ref", "--format=%(refname) %(objectname)").splitlines():
        name, value = row.split(" ", 1)
        if not any(name == prefix or name.startswith(prefix + "/") for prefix in PRIVATE_REFS):
            shared.add(commit(value))
    require(shared, "private Git recovery has no durable shared refs")
    revisions = "\n".join(sorted(objects) + ["^" + value for value in sorted(shared)]) + "\n"
    try:
        unretained = git(path, "rev-list", "--objects", "--no-object-names", "--stdin",
                         input=revisions.encode("ascii"))
        if unretained:
            kinds = git(path, "cat-file", "--batch-check=%(objecttype)",
                        input=unretained.encode("ascii")).splitlines()
            require(len(kinds) == len(unretained.splitlines())
                    and set(kinds) <= {"blob", "tree", "tag"},
                    "private Git recovery objects are not durably reachable from shared refs")
            # Explicit non-commit roots can be emitted even when a negative
            # commit reaches them. Prove their membership in the shared graph.
            reachable = git(path, "rev-list", "--objects", "--no-object-names", "--stdin",
                            input=("\n".join(sorted(shared)) + "\n").encode("ascii"))
            unretained = set(map(commit, unretained.splitlines())) - set(
                map(commit, reachable.splitlines())
            )
    except Retain as error:
        raise Retain(f"private Git recovery history is incomplete: {error}") from error
    require(not unretained, "private Git recovery objects are not durably reachable from shared refs")
    return {"private_objects": sorted(objects), "index_resolve_undo": undo}


def gitlink_state(path, head):
    """Compare live index/HEAD identities and observe only empty, unpopulated directories."""
    inventories = []
    for indexed, arguments in (
        (True, ("ls-files", "--stage", "-z")),
        (False, ("ls-tree", "-r", "--full-tree", "-z", head)),
    ):
        raw = git(path, *arguments)
        require(not raw or raw.endswith("\0"), "incomplete Git index/tree inventory")
        links, seen = {}, set()
        for record in raw.split("\0")[:-1]:
            fields, separator, name = record.partition("\t")
            fields = fields.split(" ")
            require(separator and name and name not in seen and len(fields) == 3,
                    "ambiguous Git index/tree entry")
            seen.add(name)
            mode = fields[0]
            require(mode in {"100644", "100755", "120000", "160000"},
                    "unsupported Git index/tree mode")
            if indexed:
                require(fields[2] == "0", "unmerged Git index entries require preservation")
                oid = commit(fields[1])
            else:
                require(fields[1] == ("commit" if mode == "160000" else "blob"),
                        "ambiguous Git tree object type")
                oid = commit(fields[2])
            if mode == "160000":
                links[name] = (mode, oid)
        inventories.append(links)
    require(inventories[0] == inventories[1],
            "gitlink index/HEAD identities differ; staged submodule work requires preservation")
    require(len(inventories[0]) <= MAX_RECORDS, "gitlink inventory exceeds safety bound")

    observed = []
    for name, (mode, oid) in sorted(inventories[0].items()):
        relative = Path(name)
        require(not relative.is_absolute() and str(relative) == name
                and not any(part in {"..", ".git"} for part in relative.parts),
                "noncanonical gitlink path")
        observed.append((name, mode, oid, *empty_gitlink_directory(path / relative)))
    return observed


class Repository:
    def __init__(self, root, preserve=()):
        self.root = Path(root).resolve(strict=True)
        require(Path(git(self.root, "rev-parse", "--show-toplevel").removesuffix("\n")) == self.root,
                "repository-root must be an exact Git worktree root")
        self.common = Path(git(self.root, "rev-parse", "--path-format=absolute",
                               "--git-common-dir").removesuffix("\n")).resolve(strict=True)
        info = self.common.stat()
        self.common_identity = (info.st_dev, info.st_ino)
        self.preserve = tuple(Path(value).absolute().resolve() for value in preserve)
        self.name = self.remote_name()

    def remote_name(self):
        url = git(self.root, "remote", "get-url", "--all", "origin").strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
            r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", url
        )
        require(match is not None, "origin must identify one unambiguous github.com repository")
        return match[1]

    def local_state(self, target):
        path = Path(target)
        require(path.is_absolute() and path == path.resolve(), "noncanonical or symlink target")
        current_common = Path(git(self.root, "rev-parse", "--path-format=absolute",
                                  "--git-common-dir").removesuffix("\n")).resolve(strict=True)
        info = current_common.stat()
        require(current_common == self.common and (info.st_dev, info.st_ino) == self.common_identity,
                "coordinator Git common directory changed")
        records = inventory(self.root)
        require(str(path) in records, "not an exact registered worktree (no pruning)")
        record = records[str(path)]
        automatic = (Path.home(), self.root, self.common, SOURCE_ROOT, Path.cwd().resolve())
        require(not any(inside(protected, path) for protected in automatic),
                "current, source, repository, home, or ancestor path is protected")
        parts = path.parts
        if "session-state" in parts:
            require(len(parts) > parts.index("session-state") + 3,
                    "broad session root is protected")
        require(not any(inside(other, path) for other in map(Path, records) if other != path),
                "ancestor of another registered worktree is protected")
        require(not any(inside(path, active) or inside(active, path) for active in self.preserve),
                "explicitly preserved active workspace")
        require("locked" not in record, "locked worktree")
        require("prunable" not in record and path.is_dir(), "missing/prunable registration retained")
        mounts = mount_paths()
        require(not any(inside(mount, path) for mount in mounts),
                "mounted workspace or nested bind mount requires preservation")
        require("bare" not in record and "branch" in record and "detached" not in record,
                "bare/detached/ambiguous worktree")
        require(record["branch"] != "refs/heads/master", "master worktree is protected")
        require(record["branch"].startswith("refs/heads/"), "unknown local branch identity")
        require((path / ".git").is_file() and not (path / ".git").is_symlink(),
                "main or ambiguous worktree is protected")
        require(path.stat().st_uid == os.getuid(), "foreign-owner workspace")
        common = Path(git(path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir").removesuffix("\n")).resolve(strict=True)
        require(common == self.common, "foreign Git common directory")
        gitdir = Path(git(path, "rev-parse", "--absolute-git-dir").removesuffix("\n")).resolve(strict=True)
        require(gitdir.parent == self.common / "worktrees", "foreign Git worktree metadata")
        require(not any(inside(mount, gitdir) for mount in mounts),
                "mounted private Git metadata requires preservation")
        backlink = Path(os.fsdecode((gitdir / "gitdir").read_bytes()).removesuffix("\n"))
        require(backlink == path / ".git", "Git metadata backlink does not identify target")
        require(Path(git(path, "rev-parse", "--show-toplevel").removesuffix("\n")) == path,
                "Git top-level differs from registered path")
        require(self.remote_name() == self.name, "origin changed during cleanup")
        head = commit(git(path, "rev-parse", "HEAD").strip())
        branch = git(path, "symbolic-ref", "HEAD").strip()
        require(head == record.get("HEAD") and branch == record["branch"],
                "registered branch/HEAD changed")
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                       "rebase-apply", "sequencer", "BISECT_LOG"):
            require(not (gitdir / marker).exists(), "unfinished Git operation")
        require(not (self.common / "info" / "grafts").exists(), "local Git grafts are ambiguous")
        require(not git(path, "for-each-ref", *PRIVATE_REFS),
                "private worktree references require preservation")
        flags = git(path, "ls-files", "-v", "-z").split("\0")
        require(all(not entry or (entry[0].isupper() and entry[0] != "S") for entry in flags),
                "assume-unchanged or skip-worktree index entries hide local work")
        gitlinks = gitlink_state(path, head)
        require(not git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                        "--ignore-submodules=all"), "dirty tracked or untracked work")
        tracked = set(git(path, "ls-files", "-z").split("\0"))
        ignored = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        unknown = next((name for name in ignored.split("\0")
                        if name and not generated_ignored(name, tracked)), None)
        require(unknown is None, f"ignored non-build/local data: {unknown}")
        upstream = git(path, "for-each-ref", "--format=%(upstream)", branch).strip()
        if upstream:
            require(upstream == "refs/remotes/origin/" + branch.removeprefix("refs/heads/"),
                    "ambiguous upstream association")
            tips = git(path, "for-each-ref", "--format=%(objectname)", upstream).splitlines()
            require(not tips or tips == [head], "unpushed or divergent upstream work")
        for pid, cwd in process_cwds():
            require(not inside(cwd, path), f"active process {pid} has cwd in workspace")
        recovery = private_recovery(path, gitdir)
        info = path.stat()
        return {"head": head, "branch": branch, "device": info.st_dev, "inode": info.st_ino,
                "gitlinks": gitlinks, **recovery}

    def ancestor(self, older, newer):
        # The live GitHub master ref anchors the proof; stale remote-tracking refs do not.
        for sha in (older, newer):
            require(git(self.root, "cat-file", "-t", commit(sha)).strip() == "commit",
                    "proof history missing locally; coordinator must fetch first")
        git(self.root, "merge-base", "--is-ancestor", older, newer)


def repository_identity(data, name):
    require(isinstance(data, dict) and data.get("full_name") == name and positive(data.get("id")),
            "foreign or malformed GitHub repository identity")
    return data["id"]


def run_identity(run, name, repo_id, proof):
    require(isinstance(run, dict), "malformed workflow run")
    require(repository_identity(run.get("repository"), name) == repo_id,
            "workflow run repository mismatch")
    require(repository_identity(run.get("head_repository"), name) == repo_id,
            "workflow head repository mismatch")
    require(run.get("head_sha") == proof, "stale workflow commit")
    for field in ("id", "workflow_id", "run_number", "run_attempt", "check_suite_id"):
        require(positive(run.get(field)), f"missing workflow {field}")
    require(isinstance(run.get("head_branch"), str) and isinstance(run.get("event"), str),
            "missing workflow event/branch")
    require(isinstance(run.get("path"), str), "missing workflow path")
    require(timestamp(run.get("created_at")) <= timestamp(run.get("updated_at")),
            "invalid workflow chronology")
    return tuple(run.get(key) for key in (
        "id", "workflow_id", "head_sha", "head_branch", "event", "path", "run_number",
        "run_attempt", "check_suite_id", "status", "conclusion", "updated_at",
    ))


def ci_proof(api, prefix, name, repo_id, proof, merged_at):
    workflow = api.get(prefix + "/actions/workflows/build.yml")
    require(isinstance(workflow, dict) and positive(workflow.get("id"))
            and workflow.get("path") == BUILD_PATH, "missing authoritative Build workflow")
    runs = api.pages(prefix + "/actions/runs?" + urlencode({"head_sha": proof}), "workflow_runs")
    latest, suites, sequences = {}, set(), set()
    for run in runs:
        run_identity(run, name, repo_id, proof)
        sequence = (run["workflow_id"], run["run_number"])
        require(sequence not in sequences, "ambiguous workflow sequence")
        sequences.add(sequence)
        suites.add(run["check_suite_id"])
        if run["head_branch"] != "master" or run["event"].startswith("pull_request"):
            continue
        order = (timestamp(run["updated_at"]), run["run_number"], run["run_attempt"])
        previous = latest.get(run["workflow_id"])
        if previous is None or order > previous[0]:
            latest[run["workflow_id"]] = (order, run)
    selected = [item[1] for item in latest.values()]
    automatic = [run for run in runs if run["workflow_id"] == workflow["id"]
                 and run["event"] == "push" and run["head_branch"] == "master"]
    build = max(automatic, key=lambda run: (timestamp(run["updated_at"]), run["run_number"],
                                           run["run_attempt"]), default=None)
    require(build is not None and build["path"] == BUILD_PATH,
            "missing successful automatic master Build (candidate/manual CI is not proof)")
    if all(run["id"] != build["id"] for run in selected):
        selected.append(build)
    require(timestamp(build["created_at"]) >= timestamp(merged_at), "Build predates PR merge")
    identities = []
    for run in selected:
        require(run.get("status") == "completed" and run.get("conclusion") == "success",
                f"latest master workflow {run['id']} attempt {run['run_attempt']} is "
                f"{run.get('status')}/{run.get('conclusion')}")
        detail = api.get(prefix + f"/actions/runs/{run['id']}")
        identity = run_identity(run, name, repo_id, proof)
        require(run_identity(detail, name, repo_id, proof) == identity,
                "workflow changed while collecting proof")
        identities.append(identity)
    selected_suites = {run["check_suite_id"] for run in selected}
    checks = api.pages(prefix + f"/commits/{proof}/check-runs?filter=latest", "check_runs")
    contexts, present = {}, set()
    for check in checks:
        require(check.get("head_sha") == proof, "stale check commit")
        app, suite = check.get("app"), check.get("check_suite")
        require(isinstance(app, dict) and positive(app.get("id"))
                and isinstance(suite, dict) and positive(suite.get("id"))
                and isinstance(check.get("name"), str) and check["name"], "malformed check identity")
        if suite["id"] in suites and suite["id"] not in selected_suites:
            continue  # Superseded attempts/workflows and candidate-only runs are not master proof.
        if app.get("slug") == "github-actions":
            require(suite["id"] in selected_suites, "unassociated Actions check suite")
            require(check.get("status") == "completed" and check.get("conclusion") in {"success", "skipped"},
                    f"master Actions check {check['name']} is not successful/skipped")
            if check["conclusion"] == "success":
                present.add(suite["id"])
        key = (app["id"], check["name"])
        if key not in contexts or contexts[key]["id"] < check["id"]:
            contexts[key] = check
    require(selected_suites <= present, "missing exact master workflow checks")
    for check in contexts.values():
        allowed = {"success", "skipped"} if check["app"].get("slug") == "github-actions" else {"success"}
        require(check.get("status") == "completed" and check.get("conclusion") in allowed,
                f"latest check {check['name']} is {check.get('status')}/{check.get('conclusion')}")
    statuses = api.pages(prefix + f"/commits/{proof}/statuses")
    latest_status = {}
    for status in statuses:
        require(status.get("url") == f"https://api.github.com/{prefix}/statuses/{proof}"
                and isinstance(status.get("context"), str) and status["context"],
                "stale or malformed commit status identity")
        key = status["context"]
        if key not in latest_status or latest_status[key]["id"] < status["id"]:
            latest_status[key] = status
    require(all(row.get("state") == "success" for row in latest_status.values()),
            "latest external commit status is failed or pending")
    return {
        "sha": proof, "build_run": build["id"], "build_attempt": build["run_attempt"],
        "runs": sorted(identities),
        "checks": sorted((row["id"], row["status"], row["conclusion"]) for row in contexts.values()),
        "statuses": sorted((row["id"], row["state"]) for row in latest_status.values()),
    }


def remote_proof(repo, api, local, proof_sha=None):
    prefix = "repos/" + repo.name
    metadata = api.get(prefix)
    repo_id = repository_identity(metadata, repo.name)
    require(metadata.get("default_branch") == "master", "default branch is not master")
    master = api.get(prefix + "/git/ref/heads/master")
    require(isinstance(master, dict) and master.get("ref") == "refs/heads/master"
            and isinstance(master.get("object"), dict) and master["object"].get("type") == "commit",
            "missing authoritative master ref")
    master_sha = commit(master["object"].get("sha"))
    branch = local["branch"].removeprefix("refs/heads/")
    pulls = api.pages(prefix + "/pulls?state=all&sort=created&direction=desc")
    matches = []
    for pr in pulls:
        head, base = pr.get("head"), pr.get("base")
        require(positive(pr.get("number")) and pr.get("state") in {"open", "closed"}
                and isinstance(head, dict) and isinstance(base, dict), "malformed PR identity")
        commit(head.get("sha"))
        require(isinstance(head.get("ref"), str), "missing PR branch")
        require(repository_identity(base.get("repo"), repo.name) == repo_id,
                "foreign PR base repository")
        same_branch = isinstance(head.get("repo"), dict) and (
            head["repo"].get("full_name") == repo.name and head["ref"] == branch
        )
        related = same_branch or head["sha"] == local["head"]
        require(not (related and pr["state"] == "open"), "open PR work is protected")
        if same_branch:
            matches.append(pr)
    require(matches, "no merged PR for this branch; unknown/unpushed work")
    newest = max(matches, key=lambda pr: pr["number"])
    require(newest["head"]["sha"] == local["head"], "branch reused or unique/unpushed local HEAD")
    require(sum(pr["head"]["sha"] == local["head"] for pr in matches) == 1,
            "ambiguous PR branch/head association")
    pr = api.get(prefix + f"/pulls/{newest['number']}")
    require(isinstance(pr, dict) and pr.get("id") == newest["id"]
            and pr.get("number") == newest["number"] and pr.get("state") == "closed"
            and pr.get("merged") is True and pr.get("head") == newest["head"]
            and pr.get("base") == newest["base"], "PR changed or is not merged")
    require(pr["base"].get("ref") == "master", "PR was not merged into master")
    require(repository_identity(pr["head"].get("repo"), repo.name) == repo_id,
            "foreign PR head repository")
    merge = commit(pr.get("merge_commit_sha"))
    timestamp(pr.get("merged_at"))
    proof = commit(proof_sha) if proof_sha else merge
    try:
        repo.ancestor(local["head"], merge)
        repo.ancestor(merge, proof)
        repo.ancestor(proof, master_sha)
    except Retain as error:
        raise Retain("unique/unmerged work or proof not on master; "
                     f"squash/rebase or missing local history requires preservation: {error}") from error
    return {"pr": pr["number"], "merge": merge,
            "ci": ci_proof(api, prefix, repo.name, repo_id, proof, pr["merged_at"])}


def assess(repo, api, target, proof_sha=None):
    row = {"path": str(target), "decision": "retained", "reasons": [], "allocated_bytes": None}
    try:
        row["local"] = repo.local_state(target)
        row["proof"] = remote_proof(repo, api, row["local"], proof_sha)
        row["allocated_bytes"] = allocated_size(Path(target), row["local"]["gitlinks"])
        row["decision"] = "eligible"
    except (Retain, OSError, KeyError, TypeError) as error:
        row["reasons"] = [str(error) or type(error).__name__]
    return row


def cleanup(repo, api, targets=(), *, apply=False, proof_sha=None):
    require(not apply or (targets and repo.preserve),
            "apply requires explicit --target and --preserve for all assigned workspaces")
    if proof_sha:
        commit(proof_sha)
    paths = [Path(path).absolute() for path in targets] if targets else list(map(Path, inventory(repo.root)))
    require(len(paths) == len(set(paths)), "duplicate cleanup target")
    api.clear()
    rows = [assess(repo, api, path, proof_sha) for path in paths]
    if apply:
        for index, planned in enumerate(rows):
            if planned["decision"] != "eligible":
                continue
            api.clear()  # Never authorize removal from the plan or another target's mutable snapshot.
            fresh = assess(repo, api, planned["path"], proof_sha)
            rows[index] = fresh
            if fresh["decision"] != "eligible":
                continue
            try:
                require(fresh["local"] == planned["local"] and fresh["proof"] == planned["proof"],
                        "local/PR/CI evidence changed since planning; run a fresh plan")
                require(repo.local_state(fresh["path"]) == fresh["local"],
                        "last-moment local identity drift")
                require(allocated_size(Path(fresh["path"]), fresh["local"]["gitlinks"])
                        == fresh["allocated_bytes"],
                        "workspace contents changed immediately before removal")
                git(repo.root, "worktree", "remove", "--", fresh["path"])
                fresh["decision"] = "removed"
            except (Retain, OSError) as error:
                fresh["decision"], fresh["reasons"] = "retained", [str(error)]
    return {"mode": "apply" if apply else "dry-run", "repository": repo.name,
            "git_common_dir": str(repo.common), "results": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=".", help="source Git worktree (never removed)")
    parser.add_argument("--target", action="append", default=[], help="exact registered path; repeatable")
    parser.add_argument("--preserve", action="append", default=[], help="all assigned/active paths; repeatable")
    parser.add_argument("--proof-sha", help="optional historical master descendant containing the PR merge")
    parser.add_argument("--apply", action="store_true", help="revalidate and normally remove explicit targets")
    args = parser.parse_args(argv)
    try:
        repo = Repository(args.repository_root, args.preserve)
        proof = args.proof_sha.lower() if args.proof_sha is not None else None
        report = cleanup(repo, GitHub(repo.root), args.target, apply=args.apply, proof_sha=proof)
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
        return int(args.apply and any(row["decision"] != "removed" for row in report["results"]))
    except (Retain, OSError) as error:
        parser.exit(2, f"worktree cleanup retained all unprocessed paths: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
