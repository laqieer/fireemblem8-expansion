"""Exact-tree Python execution over sealed descriptors, never reopened paths.

The system Python/Git and the already running caller are the trust anchor.
Candidate code and credentials must not run in that caller. See the public
contract in docs/workflow-pilot.md before adding a capsule consumer.
"""

from __future__ import annotations

import ast
import base64
import builtins
import ctypes
import errno
import hashlib
import hmac
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
    import resource
except ImportError:
    fcntl = None
    resource = None


VERSION = 1
RUNTIME_PATH = "scripts/workflow_pilot/sealed_capsule.py"
PYTHON = "/usr/bin/python3"
GIT = "/usr/bin/git"
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_PROGRAM_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = MAX_OUTPUT_BYTES + 4 * 1024
# The canonical wrapper adds 93 bytes; both encodings have one trailing newline.
MAX_SIGNED_RECEIPT_BYTES = MAX_RECEIPT_BYTES + len(
    b'{"hmac_sha256":"' + b"0" * 64 + b'","receipt":}')
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_ENTRIES = 1024
MAX_SECONDS = 120
MAX_DEPTH = 4
MAX_WORKER_FDS = 64
MAX_WORKER_FILE_BYTES = 1024 * 1024
MAX_PYTHON_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PYTHON_PROBE_BYTES = 4096
PYTHON_PROBE_SECONDS = 5
PYTHON_APIS = {
    "callable": {
        "os": ["memfd_create", "fchmod", "fork", "waitid", "waitpid", "pread", "pipe2",
               "set_blocking", "setpgid", "killpg"],
        "sys": ["addaudithook"],
        "fcntl": ["fcntl"],
        "resource": ["setrlimit"],
        "signal": ["pthread_sigmask", "valid_signals", "getsignal", "signal"],
    },
    "int": {
        "os": ["MFD_CLOEXEC", "MFD_ALLOW_SEALING", "O_CLOEXEC", "P_PID",
               "WEXITED", "WNOHANG", "WNOWAIT"],
        "fcntl": ["F_ADD_SEALS", "F_GET_SEALS"],
        "resource": ["RLIMIT_CORE", "RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_NOFILE", "RLIMIT_FSIZE"],
    },
}
PYTHON_PROBE = """import ctypes,importlib,json,os,sys
available=True
for kind,modules in json.loads(sys.argv[1]).items():
    for name,attributes in modules.items():
        try:
            module=importlib.import_module(name)
        except ImportError:
            module=None
        for attribute in attributes:
            value=getattr(module,attribute,None)
            available=available and (callable(value) if kind=='callable' else type(value) is int)
names=getattr(sys,'stdlib_module_names',None)
print(json.dumps({
    'version':list(sys.version_info[:2]),'platform':sys.platform,
    'machine':os.uname().machine,'pointer_bytes':ctypes.sizeof(ctypes.c_void_p),
    'stdlib_module_names':isinstance(names,frozenset) and {'os','sys','fcntl','resource','signal'}<=names,
    'capabilities':available},sort_keys=True,separators=(',',':')))
"""
SEALS = 0x01 | 0x02 | 0x04 | 0x08
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z", re.ASCII)
RECEIPT_DOMAIN = b"workflow-sealed-capsule-receipt-v1\0"
ENVIRONMENT = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
GIT_ENVIRONMENT = {
    **ENVIRONMENT,
    "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}

# Execute-only ELF permissions deny same-UID inspection before userspace.
# Root-only dump mode 2 is cleared before reading any capsule bytes; mode 1
# already exposed the process and must never be repaired into an admission.
PYTHON_STARTUP = """import os,sys,fcntl,ctypes,json,signal
_capsule_libc=ctypes.CDLL(None)
_capsule_entry_dumpable=_capsule_libc.prctl(3,0,0,0,0)
if (_capsule_entry_dumpable not in (0,2)
    or (_capsule_entry_dumpable==2 and _capsule_libc.prctl(4,0,0,0,0)!=0)
    or _capsule_libc.prctl(3,0,0,0,0)!=0):
    os.write(2,b'CapsuleUnavailable: Python exec permits user inspection or cannot disable dumping')
    raise SystemExit(125)
_capsule_image_fd=int(sys.argv.pop(1))
_capsule_python_identity=tuple(json.loads(sys.argv.pop(1)))
_capsule_signal_mask=json.loads(sys.argv.pop(1))
_image=os.fstat(_capsule_image_fd); _running=os.stat('/proc/self/exe')
if ((_image.st_dev,_image.st_ino)!=(_running.st_dev,_running.st_ino)
    or _image.st_mode&0o7777!=0o111 or fcntl.fcntl(_capsule_image_fd,1034)&15!=15):
    raise SystemExit(125)
signal.pthread_sigmask(signal.SIG_SETMASK,_capsule_signal_mask)
"""

# Only the trusted interpreter reads this constant. Capsule runtime/source
# bytes use pread, never a pathname or /proc/self/fd/N script.
BOOTSTRAP = """import os,sys,fcntl,hashlib,ctypes,types
f=int(sys.argv[1]); n=int(sys.argv[2])
if fcntl.fcntl(f,1034)&15!=15: raise SystemExit(125)
b=os.pread(f,n+1,0)
if len(b)!=n or hashlib.sha256(b).hexdigest()!=sys.argv[3]: raise SystemExit(125)
m=types.ModuleType('__capsule_runtime__')
sys.modules[m.__name__]=m
exec(compile(b,'sealed:runtime','exec',dont_inherit=True),m.__dict__)
m._supervise(sys.argv[4:],_capsule_image_fd,_capsule_python_identity)
"""


class CapsuleError(ValueError):
    """An execution has no admissible receipt."""


class CapsuleUnavailable(CapsuleError):
    """The host cannot provide the required immutable execution boundary."""

    disposition = "sealed-capsule-unavailable"


def _keys(value: Any, names: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise CapsuleError(f"{label}: missing or extra fields")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _finite(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CapsuleError("JSON object keys must be strings")
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise CapsuleError("non-finite JSON number")
    elif value is not None and type(value) not in (str, int, bool):
        raise CapsuleError("non-JSON value")


def canonical(value: Any) -> bytes:
    try:
        _finite(value)
        return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    except (ValueError, TypeError, RecursionError) as error:
        raise CapsuleError(f"invalid canonical JSON: {error}") from error


def parse(raw: bytes, limit: int = MAX_OUTPUT_BYTES) -> Any:
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise CapsuleError("JSON exceeds byte limit")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        if canonical(value) != raw:
            raise CapsuleError("noncanonical JSON")
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CapsuleError(f"invalid canonical JSON: {error}") from error


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _oid(kind: str, raw: bytes) -> str:
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(raw)).encode("ascii")
                        + b"\0" + raw).hexdigest()


def _path(value: Any) -> str:
    if (not isinstance(value, str) or len(value.encode("utf-8")) > 1024
            or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise CapsuleError("artifact path must be canonical and repository-relative")
    return value


def _names(values, pattern, label):
    if not isinstance(values, list) or len(values) > MAX_ENTRIES:
        raise CapsuleError(f"{label}: invalid bounded list")
    if any(not isinstance(v, str) or pattern.fullmatch(v) is None for v in values):
        raise CapsuleError(f"{label}: invalid name")
    if len(set(values)) != len(values):
        raise CapsuleError(f"{label}: duplicate entry")


@dataclass(frozen=True)
class CapsuleSpec:
    """Trusted declarations. Code is from base; other trees supply inert data."""

    trees: dict[str, str]
    programs: dict[str, str]
    modules: tuple[str, ...] = ()
    data: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def record(self) -> dict:
        return _spec({
            "trees": dict(self.trees),
            "programs": dict(self.programs),
            "modules": list(self.modules),
            "data": {slot: list(paths) for slot, paths in self.data.items()},
        })


def _spec(value):
    _keys(value, {"trees", "programs", "modules", "data"}, "capsule spec")
    for label in ("trees", "programs", "data"):
        if not isinstance(value[label], dict):
            raise CapsuleError(f"spec {label} must be an object")
        _names(list(value[label]), NAME, label)
    if "base" not in value["trees"] or not value["programs"]:
        raise CapsuleError("spec requires base and at least one program")
    if len(value["trees"]) > 8 or len(value["programs"]) > 32:
        raise CapsuleError("too many trees or programs")
    for sha in value["trees"].values():
        if not isinstance(sha, str) or SHA1.fullmatch(sha) is None:
            raise CapsuleError("tree authority must be an exact SHA-1 commit")
    if len(set(value["programs"].values())) != len(value["programs"]):
        raise CapsuleError("duplicate program path")
    for path in value["programs"].values():
        _module_name(_path(path))
        if path == RUNTIME_PATH:
            raise CapsuleError("runtime cannot be a program")
    _names(value["modules"], MODULE, "module roots")
    for slot, paths in value["data"].items():
        if slot not in value["trees"] or not isinstance(paths, list):
            raise CapsuleError("data references an undeclared tree")
        if len(paths) > MAX_ENTRIES or len(set(paths)) != len(paths):
            raise CapsuleError("duplicate or excessive data paths")
        for path in paths:
            _path(path)
    return value


def _module_name(path):
    parts = path.removesuffix(".py").split("/")
    if not path.endswith(".py"):
        raise CapsuleError("Python code must have a .py path")
    if parts[-1] == "__init__":
        parts.pop()
    result = ".".join(parts)
    if not MODULE.fullmatch(result):
        raise CapsuleError("Python path has no canonical module name")
    return result


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise CapsuleError("short descriptor write")
        view = view[count:]


@contextmanager
def _defer_handlers():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    missing = object()
    handlers = {}
    pending = {}
    try:
        handlers = {number: signal.getsignal(number) for number in signal.valid_signals()
                    if callable(signal.getsignal(number))}
        signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
        if threading.current_thread() is threading.main_thread():
            # Another thread may receive a signal and queue its Python handler
            # for this thread even while our POSIX mask blocks that signal.
            pending = dict.fromkeys(handlers, missing)

            def defer(number, frame):
                pending[number] = frame

            for number in handlers:
                signal.signal(number, defer)
        yield previous
    finally:
        if pending:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        for number, frame in pending.items():
            if frame is not missing:
                handlers[number](number, frame)


def _owns_child(process):
    if process.returncode is not None:
        return False
    try:
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return False
    return True


def _close_process_streams(process):
    error = None
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except BaseException as failure:
                if error is None:
                    error = failure
    if error is not None:
        raise error


def _close_owned_fd(fd, identity):
    try:
        if _descriptor_identity(fd) == identity:
            os.close(fd)
    except OSError:
        pass


def _stop_worker(pid):
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return
    _kill_group(pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)


def _finish_child(process, abort=None):
    try:
        try:
            if abort is not None:
                abort()
        finally:
            if _owns_child(process):
                try:
                    if abort is not None:
                        process.wait(timeout=3)
                finally:
                    # This sole waiter retains the child PID until group teardown.
                    if _owns_child(process):
                        _kill_group(process.pid)
                        process.wait()
    finally:
        _close_process_streams(process)


class _Child:
    """Own launch, liveness and collection before a child handle can escape."""

    def __init__(self):
        self.process, self.command = None, None
        self.fds, self.identities = (), {}
        self.active = False

    def __enter__(self):
        self.active = True
        return self

    def pipe(self):
        if not self.active or self.fds:
            raise CapsuleError("liveness pipe needs its active single owner")
        with _defer_handlers():
            self.fds = os.pipe2(os.O_CLOEXEC)
            try:
                self.identities = {fd: _descriptor_identity(fd) for fd in self.fds}
            except BaseException:
                for fd in self.fds:
                    os.close(fd)
                self.fds = ()
                raise
        return self.fds

    def close_fd(self, fd):
        expected = self.identities.get(fd)
        if expected is not None:
            _close_owned_fd(fd, expected)

    def abort(self):
        if self.fds:
            self.close_fd(self.fds[1])

    def start(self, command, **options):
        if not self.active or self.process is not None:
            raise CapsuleError("child launch needs its active single owner")
        with _defer_handlers() as mask:
            self.command = command(mask) if callable(command) else command
            self.process = subprocess.Popen(self.command, **options)
        return self.process, self.command

    def __exit__(self, *exc):
        if self.process is None and not self.fds:
            self.active = False
            return
        with _defer_handlers():
            try:
                if self.process is not None:
                    _finish_child(self.process, self.abort if self.fds else None)
            finally:
                for fd in self.fds:
                    self.close_fd(fd)
                self.active = False


def _collect(process, timeout, limit, abort: Callable[[], None] | None = None,
             cancel_fd=None):
    try:
        deadline = time.monotonic() + timeout
        chunks = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
        stdout_fd, stderr_fd = chunks
        with selectors.DefaultSelector() as selector:
            for fd in chunks:
                selector.register(fd, selectors.EVENT_READ)
            if cancel_fd is not None:
                selector.register(cancel_fd, selectors.EVENT_READ)
            while any(fd in selector.get_map() for fd in chunks):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapsuleError("process timeout")
                for key, _ in selector.select(remaining):
                    if key.fd == cancel_fd:
                        raise CapsuleError("capsule ancestor exited or interrupted")
                    raw = os.read(key.fd, 65536)
                    if not raw:
                        selector.unregister(key.fd)
                    else:
                        chunks[key.fd].extend(raw)
                        if len(chunks[key.fd]) > limit:
                            raise CapsuleError("process output exceeds limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapsuleError("process timeout")
        if process.returncode is None and not _owns_child(process):
            raise CapsuleError("execution lost its exclusive child ownership")
        status = process.wait(timeout=remaining)
        return status, bytes(chunks[stdout_fd]), bytes(chunks[stderr_fd])
    except BaseException:
        with _defer_handlers():
            _finish_child(process, abort)
        raise
    finally:
        _close_process_streams(process)


def _git(root, *arguments):
    command = [GIT, "--no-replace-objects", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=/dev/null", "-C", os.fspath(root), *arguments]
    try:
        with _Child() as owner:
            process, _ = owner.start(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=GIT_ENVIRONMENT, start_new_session=True, close_fds=True)
            process.stdin.close()
            status, stdout, stderr = _collect(process, 15, MAX_BUNDLE_BYTES)
    except (OSError, subprocess.SubprocessError) as error:
        raise CapsuleError(f"exact-tree Git read failed: {error}") from error
    if status:
        raise CapsuleError(f"exact-tree Git read rejected: {stderr[:4096]!r}")
    return stdout


def head_commit(root: Path) -> str:
    """Resolve once in the trusted caller, never in the executing capsule."""
    raw = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    value = raw.decode("ascii").strip()
    if SHA1.fullmatch(value) is None:
        raise CapsuleError("HEAD is not an exact SHA-1 commit")
    return value


class _ObjectSource:
    def __init__(self, objects=None):
        self.objects = {} if objects is None else objects
        self.used = set()
        self._parsed_trees = {}

    def get(self, kind, oid):
        self.used.add(oid)
        found = self.objects.get(oid)
        if found is None or found[0] != kind:
            raise CapsuleError(f"missing or wrong-kind Git proof object {oid}")
        return found[1]

    def tree(self, commit):
        raw = self.get("commit", commit)
        first = raw.split(b"\n", 1)[0]
        if not first.startswith(b"tree "):
            raise CapsuleError("commit has no tree identity")
        tree = first[5:].decode("ascii")
        if SHA1.fullmatch(tree) is None:
            raise CapsuleError("invalid commit tree identity")
        return tree

    def entries(self, oid):
        raw = self.get("tree", oid)
        cached = self._parsed_trees.get(oid)
        if cached is not None and cached[0] is raw:
            return cached[1]
        if not isinstance(raw, bytes) or _oid("tree", raw) != oid:
            raise CapsuleError("Git tree bytes do not match requested identity")
        entries = {}
        offset = 0
        while offset < len(raw):
            space, end = raw.find(b" ", offset), raw.find(b"\0", offset)
            if space < offset or end <= space or end + 21 > len(raw):
                raise CapsuleError("malformed tree proof")
            try:
                mode = raw[offset:space].decode("ascii").zfill(6)
            except UnicodeError as error:
                raise CapsuleError("invalid tree mode") from error
            name = raw[space + 1:end]
            if (name in entries or not name or b"/" in name
                    or mode not in {"040000", "100644", "100755", "120000", "160000"}):
                raise CapsuleError("duplicate or invalid tree entry")
            entries[name] = (mode, raw[end + 1:end + 21].hex())
            offset = end + 21
        self._parsed_trees[oid] = (raw, entries)
        return entries

    def lookup(self, commit, path):
        oid = self.tree(commit)
        parts = _path(path).split("/")
        for index, part in enumerate(parts):
            found = self.entries(oid).get(part.encode("utf-8"))
            if found is None:
                return None
            mode, oid = found
            if index == len(parts) - 1:
                return found
            if mode != "040000":
                return None
        raise CapsuleError("empty tree path")


class _GitSource(_ObjectSource):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.byte_count = 0
        if _git(root, "rev-parse", "--show-object-format") != b"sha1\n":
            raise CapsuleUnavailable("sealed capsules require Git SHA-1 object format")

    def get(self, kind, oid):
        if oid not in self.objects:
            if len(self.objects) >= MAX_ENTRIES * 8:
                raise CapsuleError("invalid bounded Git object closure")
            raw = _git(self.root, "cat-file", kind, oid)
            if _oid(kind, raw) != oid:
                raise CapsuleError("Git object bytes do not match requested identity")
            self.byte_count += len(raw)
            if self.byte_count > MAX_BUNDLE_BYTES * 3 // 4:
                raise CapsuleError("aggregate Git artifact bytes exceed bundle bound")
            self.objects[oid] = (kind, raw)
        return super().get(kind, oid)


def _stdlib_spec(name):
    """Inspect only platform roots, without importing a candidate-shadowed parent."""
    search = [os.path.dirname(os.__file__)]
    parts = name.split(".")
    for index in range(len(parts)):
        fullname = ".".join(parts[:index + 1])
        spec = (importlib.machinery.BuiltinImporter.find_spec(fullname)
                or importlib.machinery.FrozenImporter.find_spec(fullname)
                or importlib.machinery.PathFinder.find_spec(fullname, search))
        if spec is None or index == len(parts) - 1:
            return spec
        search = spec.submodule_search_locations
        if search is None:
            return None
    return None


def _assemble(source, spec):
    artifacts, modules, stdlib, scanned = {}, {}, set(), set()
    program_paths = set(spec["programs"].values())
    commits = spec["trees"]
    for commit in commits.values():
        source.tree(commit)

    def add(slot, path, role):
        key = (slot, path)
        if key in artifacts:
            if artifacts[key]["role"] != role:
                raise CapsuleError(f"artifact has conflicting roles: {path}")
            return artifacts[key]
        found = source.lookup(commits[slot], path)
        mode, oid = found if found is not None else (None, None)
        if role == "namespace":
            if found is not None:
                raise CapsuleError("namespace package initializer must be absent")
            raw = None
        elif found is None and role == "data":
            raw = None
        else:
            allowed = {"100644", "100755", "120000"} if role == "data" else {"100644", "100755"}
            if mode not in allowed:
                raise CapsuleError(f"missing or unsafe {role} artifact: {slot}:{path}")
            raw = source.get("blob", oid)
            if role != "data" and len(raw) > MAX_PROGRAM_BYTES:
                raise CapsuleError("program/module exceeds size limit")
        record = {"tree": slot, "path": path, "role": role, "mode": mode,
                  "blob": oid, "sha256": digest(raw) if raw is not None else None,
                  "size": len(raw) if raw is not None else None}
        artifacts[key] = record
        if len(artifacts) > MAX_ENTRIES:
            raise CapsuleError("artifact closure exceeds limit")
        return record

    def resolve(name, required=True):
        if not MODULE.fullmatch(name):
            raise CapsuleError(f"invalid import name: {name}")
        if name.split(".")[0] in sys.stdlib_module_names:
            module_spec = _stdlib_spec(name)
            if module_spec is None and not required:
                return False
            stdlib.add(name)
            return module_spec is not None and module_spec.submodule_search_locations is not None
        if name in modules:
            return modules[name]["package"]
        prefix = name.replace(".", "/")
        package_path, file_path = prefix + "/__init__.py", prefix + ".py"
        package = source.lookup(commits["base"], package_path)
        file = source.lookup(commits["base"], file_path)
        directory = source.lookup(commits["base"], prefix)
        if package is not None and file is not None:
            raise CapsuleError(f"ambiguous module/package: {name}")
        namespace = package is None and file is None and directory is not None and directory[0] == "040000"
        if package is None and file is None and not namespace:
            if required:
                raise CapsuleError(f"import is outside complete trusted closure: {name}")
            return False
        if "." in name:
            if not resolve(name.rsplit(".", 1)[0]):
                raise CapsuleError("module parent is not a package")
        path = package_path if package is not None or namespace else file_path
        role = ("namespace" if namespace else "program" if path in program_paths
                else "runtime" if path == RUNTIME_PATH else "module")
        entry = add("base", path, role)
        modules[name] = {"path": path, "package": package is not None or namespace}
        if namespace or path in scanned:
            return modules[name]["package"]
        scanned.add(path)
        try:
            syntax = ast.parse(source.get("blob", entry["blob"]), filename=path)
        except (SyntaxError, ValueError) as error:
            raise CapsuleError(f"invalid trusted Python source: {path}") from error
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolve(alias.name)
            elif isinstance(node, ast.ImportFrom):
                parent = name if modules[name]["package"] else name.rpartition(".")[0]
                if node.level:
                    try:
                        target = importlib.util.resolve_name("." * node.level + (node.module or ""), parent)
                    except (ImportError, ValueError) as error:
                        raise CapsuleError("import escapes package closure") from error
                else:
                    target = node.module or ""
                is_package = resolve(target)
                if is_package:
                    for alias in node.names:
                        if alias.name != "*":
                            resolve(target + "." + alias.name, required=False)
        return modules[name]["package"]

    runtime = add("base", RUNTIME_PATH, "runtime")
    try:
        runtime_syntax = ast.parse(source.get("blob", runtime["blob"]), filename=RUNTIME_PATH)
    except (SyntaxError, ValueError) as error:
        raise CapsuleError("invalid self-contained capsule runtime") from error
    for node in ast.walk(runtime_syntax):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        if any(name.split(".")[0] not in sys.stdlib_module_names for name in names):
            raise CapsuleError("capsule runtime must have only platform-stdlib imports")
    for path in sorted(program_paths):
        if _module_name(path).split(".")[0] in sys.stdlib_module_names:
            raise CapsuleError("program cannot shadow a platform standard-library module")
        resolve(_module_name(path))
    for module in spec["modules"]:
        resolve(module)
    for slot, paths in spec["data"].items():
        for path in paths:
            add(slot, path, "data")
    return ([artifacts[key] for key in sorted(artifacts)], modules, sorted(stdlib))


def _make_bundle(root, spec):
    source = _GitSource(root)
    artifacts, modules, stdlib = _assemble(source, spec)
    objects = [{"oid": oid, "kind": kind, "bytes": base64.b64encode(raw).decode("ascii")}
               for oid, (kind, raw) in sorted(source.objects.items())]
    return canonical({"version": VERSION, "spec": spec, "artifacts": artifacts,
                      "modules": modules, "stdlib": stdlib, "objects": objects})


class _Bundle:
    def __init__(self, raw, expected=None):
        value = _keys(parse(raw, MAX_BUNDLE_BYTES),
                      {"version", "spec", "artifacts", "modules", "stdlib", "objects"}, "bundle")
        if type(value["version"]) is not int or value["version"] != VERSION:
            raise CapsuleError("unsupported bundle version")
        spec = _spec(value["spec"])
        if expected is not None and canonical(spec) != canonical(expected):
            raise CapsuleError("bundle spec differs from trusted declaration")
        if not isinstance(value["objects"], list) or len(value["objects"]) > MAX_ENTRIES * 8:
            raise CapsuleError("invalid bounded Git object closure")
        objects = {}
        for record in value["objects"]:
            _keys(record, {"oid", "kind", "bytes"}, "Git proof")
            oid, kind = record["oid"], record["kind"]
            if (not isinstance(oid, str) or SHA1.fullmatch(oid) is None
                    or kind not in {"commit", "tree", "blob"} or oid in objects):
                raise CapsuleError("invalid or duplicate Git proof")
            try:
                content = base64.b64decode(record["bytes"], validate=True)
            except (ValueError, TypeError) as error:
                raise CapsuleError("invalid Git proof bytes") from error
            if _oid(kind, content) != oid:
                raise CapsuleError("wrong Git object identity")
            objects[oid] = (kind, content)
        source = _ObjectSource(objects)
        artifacts, modules, stdlib = _assemble(source, spec)
        if (canonical(artifacts) != canonical(value["artifacts"])
                or canonical(modules) != canonical(value["modules"])
                or canonical(stdlib) != canonical(value["stdlib"]) or source.used != set(objects)):
            raise CapsuleError("artifact/module closure differs from exact Git authority")
        self.raw, self.spec, self.objects = raw, spec, objects
        self.artifacts = {(entry["tree"], entry["path"]): entry for entry in artifacts}
        self.modules, self.stdlib = modules, stdlib

    def content(self, slot, path):
        record = self.artifacts.get((slot, path))
        if record is None:
            raise CapsuleError(f"artifact is outside declared closure: {slot}:{path}")
        return None if record["blob"] is None else self.objects[record["blob"]][1]

    def program(self, name):
        if name not in self.spec["programs"]:
            raise CapsuleError("program is not declared by trusted spec")
        return self.content("base", self.spec["programs"][name])


def _prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise CapsuleUnavailable(f"required Linux prctl({option}) failed")


def _runtime_machine():
    if sys.platform != "linux":
        raise CapsuleUnavailable("sealed capsule runtime requires Linux x86-64")
    machine = os.uname().machine
    if machine != "x86_64" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise CapsuleUnavailable(f"sealed capsule runtime requires Linux x86-64: {machine}")
    return machine


def _python_report():
    modules = {"os": os, "sys": sys, "fcntl": fcntl, "resource": resource, "signal": signal}
    names = getattr(sys, "stdlib_module_names", None)
    available = all(
        callable(getattr(modules[name], attribute, None)) if kind == "callable"
        else type(getattr(modules[name], attribute, None)) is int
        for kind, libraries in PYTHON_APIS.items()
        for name, attributes in libraries.items() for attribute in attributes)
    return {
        "version": list(sys.version_info[:2]), "platform": sys.platform,
        "machine": os.uname().machine, "pointer_bytes": ctypes.sizeof(ctypes.c_void_p),
        "stdlib_module_names": isinstance(names, frozenset) and modules.keys() <= names,
        "capabilities": available,
    }


def _require_python(report):
    _keys(report, {"version", "platform", "machine", "pointer_bytes",
                   "stdlib_module_names", "capabilities"}, "Python capability report")
    version = report["version"]
    if (not isinstance(version, list) or len(version) != 2
            or any(type(number) is not int for number in version)
            or version[0] != 3 or version[1] < 10
            or report["platform"] != "linux" or report["machine"] != "x86_64"
            or type(report["pointer_bytes"]) is not int or report["pointer_bytes"] != 8
            or report["stdlib_module_names"] is not True
            or report["capabilities"] is not True):
        raise CapsuleUnavailable(
            "sealed capsules require Linux x86-64 Python 3.10+, sys.stdlib_module_names "
            "and the required process/descriptor APIs")


def _platform():
    _runtime_machine()
    _require_python(_python_report())
    _inherited_fds()
    _prctl(4, 0)  # PR_SET_DUMPABLE: candidate peers cannot open live /proc FDs.


def _interpreter_identity(path):
    try:
        entry = os.stat(path)
    except OSError as error:
        raise CapsuleUnavailable(f"cannot identify Python interpreter: {path}") from error
    if path == PYTHON and (not stat.S_ISREG(entry.st_mode) or entry.st_uid != 0
                           or entry.st_mode & 0o022 or not entry.st_mode & 0o111):
        raise CapsuleUnavailable("capsules require a root-owned, non-writable system Python")
    return (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mode, entry.st_uid,
            entry.st_gid, entry.st_mtime_ns, entry.st_ctime_ns)


def _probe_python(interpreter=None):
    owned = interpreter is None
    if owned:
        interpreter = _ExecutionInterpreter(_probe=False)
    try:
        with _Child() as owner:
            process, _ = interpreter.launch(
                PYTHON_PROBE, [canonical(PYTHON_APIS).decode("ascii")], owner=owner)
            status, stdout, stderr = _collect(process, PYTHON_PROBE_SECONDS, MAX_PYTHON_PROBE_BYTES)
        if status != 0 or stderr:
            raise CapsuleError(f"Python capability probe failed ({status}): {stderr[:4096]!r}")
        report = parse(stdout, MAX_PYTHON_PROBE_BYTES)
        _require_python(report)
        interpreter.check()
        return report
    except (OSError, subprocess.SubprocessError, CapsuleError) as error:
        raise CapsuleUnavailable(f"system execution interpreter unavailable: {error}") from error
    finally:
        if owned:
            interpreter.close()


def _exec_policy():
    try:
        with open("/proc/sys/fs/suid_dumpable", "rb") as stream:
            policy = stream.read(16)
        if policy not in (b"0\n", b"2\n"):
            raise CapsuleUnavailable(
                f"protected Python exec requires fs.suid_dumpable=0 or 2, got {policy!r}")
        return int(policy)
    except OSError as error:
        raise CapsuleUnavailable("cannot establish the kernel exec dumpability policy") from error


class _ExecutionInterpreter:
    """A kernel-protected, sealed system image shared by outer/nested guardians."""

    def __init__(self, *, _probe=True):
        _platform()
        _exec_policy()
        self.identity = _interpreter_identity(PYTHON)
        self.image = None
        try:
            with open(PYTHON, "rb") as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size) != self.identity[:3]:
                    raise CapsuleUnavailable("system Python changed before image preparation")
                if opened.st_size > MAX_PYTHON_IMAGE_BYTES:
                    raise CapsuleUnavailable("system Python exceeds the protected image bound")
                raw = source.read(MAX_PYTHON_IMAGE_BYTES + 1)
            if len(raw) != opened.st_size or _interpreter_identity(PYTHON) != self.identity:
                raise CapsuleUnavailable("system Python changed during image preparation")
            self.image = SealedBytes(raw, "python-image", MAX_PYTHON_IMAGE_BYTES)
            os.fchmod(self.image.fd, 0o111)
            self.check()
            if _probe:
                _probe_python(self)
        except BaseException as error:
            self.close()
            if isinstance(error, OSError):
                raise CapsuleUnavailable("host cannot prepare an execute-only Python memfd") from error
            raise

    @classmethod
    def inherited(cls, fd, identity):
        _platform()
        if (type(fd) is not int or not isinstance(identity, tuple) or len(identity) != 8
                or any(type(value) is not int for value in identity)):
            raise CapsuleError("missing protected interpreter admission")
        result = cls.__new__(cls)
        result.identity = identity
        result.image = SealedBytes.__new__(SealedBytes)
        result.image.fd, result.image.limit = fd, MAX_PYTHON_IMAGE_BYTES
        result.image.identity = _descriptor_identity(fd)
        try:
            if _descriptor_identity(fd) != _descriptor_identity_from_path("/proc/self/exe"):
                raise CapsuleError("inherited image is not the executing interpreter")
            result.check()
            return result
        except BaseException:
            result.close()
            raise

    def check(self):
        _platform()
        _exec_policy()
        if _interpreter_identity(PYTHON) != self.identity:
            raise CapsuleUnavailable("system Python changed; prepare a new capsule")
        image = self.image
        if image is None or image.fd < 0:
            raise CapsuleUnavailable("protected Python image is closed")
        try:
            entry = os.fstat(image.fd)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 0
                    or _descriptor_identity(image.fd) != image.identity
                    or entry.st_size > MAX_PYTHON_IMAGE_BYTES or entry.st_mode & 0o7777 != 0o111
                    or fcntl.fcntl(image.fd, fcntl.F_GET_SEALS) & SEALS != SEALS):
                raise CapsuleUnavailable("Python image is not sealed, anonymous and execute-only")
        except OSError as error:
            raise CapsuleUnavailable("protected Python descriptor is unavailable") from error
        try:
            readable = os.open(f"/proc/self/fd/{image.fd}", os.O_RDONLY | os.O_CLOEXEC)
        except OSError as error:
            if error.errno != errno.EACCES:
                raise CapsuleUnavailable("cannot establish execute-only Python permissions") from error
        else:
            os.close(readable)
            raise CapsuleUnavailable("Python image read permissions are bypassable by this caller")

    def launch(self, source, arguments, *, owner, pass_fds=(), stdin=subprocess.DEVNULL):
        self.check()

        def command(mask):
            return [PYTHON, "-I", "-S", "-c", PYTHON_STARTUP + source, str(self.image.fd),
                    canonical(list(self.identity)).decode("ascii"),
                    canonical(sorted(int(number) for number in mask)).decode("ascii"), *arguments]

        try:
            return owner.start(
                command, executable=f"/proc/self/fd/{self.image.fd}",
                stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=ENVIRONMENT, cwd="/", start_new_session=True, close_fds=True,
                pass_fds=(*pass_fds, self.image.fd))
        except OSError as error:
            raise CapsuleUnavailable("host cannot execute the protected Python image") from error

    def close(self):
        if self.image is not None:
            self.image.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _descriptor_identity_from_path(path):
    try:
        entry = os.stat(path)
    except OSError as error:
        raise CapsuleUnavailable("cannot identify the executing protected interpreter") from error
    return (entry.st_dev, entry.st_ino, entry.st_size)


def _descriptor_identity(fd):
    entry = os.fstat(fd)
    return (entry.st_dev, entry.st_ino, entry.st_size)


def _read_descriptor(fd, limit, expected=None):
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 0
                or before.st_size > limit or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS != SEALS):
            raise CapsuleError("descriptor is not an anonymous fully sealed bounded file")
        identity = _descriptor_identity(fd)
        if expected is not None and identity != expected:
            raise CapsuleError("descriptor identity was reused or replaced")
        raw = os.pread(fd, before.st_size + 1, 0)
        if len(raw) != before.st_size or _descriptor_identity(fd) != identity:
            raise CapsuleError("descriptor changed while reading")
        return raw
    except OSError as error:
        raise CapsuleError(f"invalid sealed descriptor: {error}") from error


class SealedBytes:
    """An owned descriptor, tied to its inode rather than an integer FD."""

    def __init__(self, raw: bytes, label: str, limit: int):
        _platform()
        if not isinstance(raw, bytes) or not raw or len(raw) > limit:
            raise CapsuleError(f"{label} bytes exceed limit or are empty")
        self.fd, self.identity, self.limit = -1, None, limit
        try:
            self.fd = os.memfd_create("workflow-capsule-" + label,
                                      os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            _write_all(self.fd, raw)
            fcntl.fcntl(self.fd, fcntl.F_ADD_SEALS, SEALS)
            self.identity = _descriptor_identity(self.fd)
            if self.read() != raw:
                raise CapsuleError("sealed bytes differ from prepared bytes")
        except BaseException as error:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1
            if isinstance(error, OSError):
                if error.errno in {errno.ENOSYS, errno.EPERM, errno.EINVAL, errno.EOPNOTSUPP}:
                    raise CapsuleUnavailable("host cannot create fully sealed anonymous descriptors") from error
                raise CapsuleError(f"cannot prepare sealed bytes: {error}") from error
            raise

    def read(self):
        if self.fd < 0:
            raise CapsuleError("owned descriptor is closed")
        return _read_descriptor(self.fd, self.limit, self.identity)

    def close(self):
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            try:
                if _descriptor_identity(fd) == self.identity:
                    os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_VERIFIED_RESULT = object()


@dataclass(frozen=True)
class ExecutionResult:
    """Serialized immutable result; the signing key never enters a child."""

    receipt_bytes: bytes
    output_bytes: bytes
    _verification: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        parse(self.receipt_bytes, MAX_RECEIPT_BYTES)
        parse(self.output_bytes, MAX_OUTPUT_BYTES)

    @property
    def value(self):
        return parse(self.output_bytes, MAX_OUTPUT_BYTES)

    @property
    def receipt(self):
        return parse(self.receipt_bytes, MAX_RECEIPT_BYTES)


def sign_receipt(result: ExecutionResult, key: bytes) -> bytes:
    if (not isinstance(result, ExecutionResult) or result._verification is not _VERIFIED_RESULT
            or not isinstance(key, bytes) or len(key) < 32):
        raise CapsuleError("signing requires an execution result and a >=32-byte parent key")
    record = result.receipt
    if record["output_sha256"] != digest(result.output_bytes):
        raise CapsuleError("receipt/output identity mismatch")
    seal = hmac.new(key, RECEIPT_DOMAIN + result.receipt_bytes, hashlib.sha256).hexdigest()
    signed = canonical({"receipt": record, "hmac_sha256": seal})
    if len(signed) > MAX_SIGNED_RECEIPT_BYTES:
        raise CapsuleError("signed receipt exceeds byte limit")
    return signed


def verify_receipt(raw: bytes, key: bytes, expected: ExecutionResult) -> dict:
    value = _keys(parse(raw, MAX_SIGNED_RECEIPT_BYTES),
                  {"receipt", "hmac_sha256"}, "signed receipt")
    if (not isinstance(key, bytes) or len(key) < 32 or not isinstance(expected, ExecutionResult)
            or expected._verification is not _VERIFIED_RESULT):
        raise CapsuleError("receipt verification needs its exact expected execution")
    received = canonical(value["receipt"])
    if len(received) > MAX_RECEIPT_BYTES:
        raise CapsuleError("receipt exceeds byte limit")
    actual = hmac.new(key, RECEIPT_DOMAIN + received, hashlib.sha256).hexdigest()
    if (not isinstance(value["hmac_sha256"], str)
            or not hmac.compare_digest(actual, value["hmac_sha256"])
            or received != expected.receipt_bytes
            or value["receipt"]["output_sha256"] != digest(expected.output_bytes)):
        raise CapsuleError("forged, stale or transplanted capsule receipt")
    return value["receipt"]


class Capsule:
    def __init__(self, raw: bytes, spec: CapsuleSpec, *, _interpreter=None):
        if _interpreter is None:
            _interpreter = _ExecutionInterpreter()
        else:
            _interpreter.check()
        self._interpreter = _interpreter
        try:
            bundle = _Bundle(raw, spec.record())
            self.bundle_fd = SealedBytes(raw, "artifacts", MAX_BUNDLE_BYTES)
            try:
                self.runtime_fd = SealedBytes(bundle.content("base", RUNTIME_PATH),
                                              "runtime", MAX_PROGRAM_BYTES)
            except BaseException:
                self.bundle_fd.close()
                raise
        except BaseException:
            self._interpreter.close()
            raise

    def execute(self, program: str, request: Any, *, timeout: float = 30) -> ExecutionResult:
        return _execute(self.bundle_fd, self.runtime_fd, program, request, timeout, 0,
                        _interpreter=self._interpreter)

    def close(self):
        self.bundle_fd.close()
        self.runtime_fd.close()
        self._interpreter.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def prepare(repository_root: Path, spec: CapsuleSpec) -> Capsule:
    """Read and prove the exact declared Git closure before any execution."""
    interpreter = _ExecutionInterpreter()
    try:
        return Capsule(_make_bundle(repository_root, spec.record()), spec, _interpreter=interpreter)
    except BaseException:
        interpreter.close()
        raise


def _execute(bundle_fd, runtime_fd, program, request, timeout, depth, cancel_fd=None,
             *, _interpreter=None):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_SECONDS or depth > MAX_DEPTH):
        raise CapsuleError("invalid capsule time/depth bound")
    if _interpreter is None:
        with _ExecutionInterpreter() as interpreter:
            return _execute(bundle_fd, runtime_fd, program, request, timeout, depth, cancel_fd,
                            _interpreter=interpreter)
    _interpreter.check()
    bundle_raw, runtime_raw = bundle_fd.read(), runtime_fd.read()
    bundle = _Bundle(bundle_raw)
    if runtime_raw != bundle.content("base", RUNTIME_PATH):
        raise CapsuleError("runtime is not the exact declared artifact")
    payload = canonical(request)
    if len(payload) > MAX_REQUEST_BYTES:
        raise CapsuleError("request exceeds limit")
    envelope = canonical({"version": VERSION, "program": program, "request": request,
                          "nonce": os.urandom(32).hex(), "timeout": timeout, "depth": depth,
                          "bundle_sha256": digest(bundle_raw), "runtime_sha256": digest(runtime_raw)})
    with SealedBytes(bundle.program(program), "program", MAX_PROGRAM_BYTES) as program_fd:
        with SealedBytes(envelope, "request", MAX_REQUEST_BYTES) as request_fd:
            fds = [runtime_fd.fd, program_fd.fd, request_fd.fd, bundle_fd.fd]
            if len({_descriptor_identity(fd)[:2] for fd in fds}) != len(fds):
                raise CapsuleError("descriptor aliasing is not permitted")
            with _Child() as owner:
                life_read, _ = owner.pipe()
                arguments = [str(runtime_fd.fd), str(len(runtime_raw)), digest(runtime_raw),
                             *(str(fd) for fd in fds), str(life_read), digest(envelope),
                             digest(bundle_raw), digest(program_fd.read())]
                try:
                    process, command = _interpreter.launch(
                        BOOTSTRAP, arguments, owner=owner, stdin=subprocess.PIPE,
                        pass_fds=(*fds, life_read))
                    process.stdin.close()
                    owner.close_fd(life_read)
                    status, stdout, stderr = _collect(
                        process, timeout + 5, MAX_OUTPUT_BYTES + MAX_DIAGNOSTIC_BYTES,
                        owner.abort, cancel_fd)
                    if status != 0 or stderr:
                        if stderr.startswith(b"CapsuleUnavailable:"):
                            raise CapsuleUnavailable(stderr[:4096].decode("utf-8", "replace"))
                        raise CapsuleError(f"capsule failed ({status}): {stderr[:4096]!r}")
                    output = _keys(parse(stdout), {"binding", "result", "loaded", "diagnostics"}, "execution")
                    binding = {"version": VERSION, "nonce": parse(envelope, MAX_REQUEST_BYTES)["nonce"],
                               "program": program, "program_sha256": digest(program_fd.read()),
                               "runtime_sha256": digest(runtime_fd.read()),
                               "artifact_sha256": digest(bundle_fd.read()),
                               "request_sha256": digest(request_fd.read()),
                               "payload_sha256": digest(payload)}
                    if canonical(output["binding"]) != canonical(binding):
                        raise CapsuleError("execution output has a forged authority binding")
                    if (not isinstance(output["loaded"], list)
                            or len(output["loaded"]) > MAX_ENTRIES
                            or output["diagnostics"] != {"stdout_sha256": digest(b""), "stderr_sha256": digest(b"")}):
                        raise CapsuleError("invalid loaded-artifact/diagnostic receipt")
                    seen = set()
                    for entry in output["loaded"]:
                        if not isinstance(entry, dict):
                            raise CapsuleError("invalid loaded artifact")
                        key = (entry.get("tree"), entry.get("path"))
                        if (not all(isinstance(value, str) for value in key) or key in seen
                                or canonical(bundle.artifacts.get(key)) != canonical(entry)):
                            raise CapsuleError("loaded artifact differs from exact closure")
                        seen.add(key)
                    raw_result = canonical(output["result"])
                    receipt = {**binding, "argv": command, "exit_code": status,
                               "output_sha256": digest(raw_result), "stdout_sha256": digest(stdout),
                               "loaded": output["loaded"], "diagnostics": output["diagnostics"]}
                    return ExecutionResult(canonical(receipt), raw_result, _VERIFIED_RESULT)
                except (OSError, subprocess.SubprocessError) as error:
                    raise CapsuleError(f"capsule process failed: {error}") from error


class _Guard(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, bundle, program_path, program_raw):
        self.bundle, self.loaded, self.denied = bundle, set(), []
        self.program_path, self.program_raw = program_path, program_raw
        self.stdlib = {name.rsplit(".", level)[0] for name in bundle.stdlib
                       for level in range(name.count(".") + 1)}
        self.imports = self.stdlib | set(bundle.modules)
        self._import = builtins.__import__
        self._import_module = importlib.import_module

    def reject(self, reason):
        self.denied.append(reason)
        raise CapsuleError(reason)

    def check_import(self, name):
        if not isinstance(name, str) or name not in self.imports:
            self.reject(f"import outside sealed closure: {name}")

    def import_builtin(self, name, globals=None, locals=None, fromlist=(), level=0):
        target = (importlib.util.resolve_name("." * level + name,
                                              (globals or {}).get("__package__"))
                  if level else name)
        self.check_import(target)
        module = self._import(name, globals, locals, fromlist, level)
        if fromlist and hasattr(module, "__path__"):
            for item in fromlist:
                exported = getattr(module, item, None)
                if isinstance(exported, type(sys)) and target + "." + item not in self.imports:
                    self.check_import(exported.__name__)
        return module

    def import_module(self, name, package=None):
        self.check_import(importlib.util.resolve_name(name, package))
        return self._import_module(name, package)

    def find_spec(self, fullname, path=None, target=None):
        record = self.bundle.modules.get(fullname)
        if record is None:
            self.reject(f"import outside sealed closure: {fullname}")
        return importlib.util.spec_from_loader(
            fullname, self, origin="sealed:" + record["path"], is_package=record["package"])

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        record = self.bundle.modules[module.__spec__.name]
        module.__file__ = "sealed:" + record["path"]
        if record["package"]:
            module.__path__ = []
        raw = (self.program_raw if record["path"] == self.program_path
               else self.bundle.content("base", record["path"]))
        self.loaded.add(("base", record["path"]))
        if raw is not None:
            exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)

    def audit(self, event, arguments):
        if event == "open":
            self.reject("filesystem/descriptor open is forbidden after capsule validation")
        if event.startswith(("os.", "socket.", "subprocess.", "ctypes.", "resource.")):
            self.reject(f"operation outside capsule capabilities: {event}")
        if event == "import":
            self.check_import(arguments[0])


class CapsuleContext:
    """The only module/data API provided to trusted programs."""

    def __init__(self, bundle, guard, invoke):
        self._bundle, self._guard, self._invoke = bundle, guard, invoke

    def entry(self, tree: str, path: str) -> dict:
        record = self._bundle.artifacts.get((tree, path))
        if record is None:
            self._guard.reject("data entry is outside sealed closure")
        self._guard.loaded.add((tree, path))
        return dict(record)

    def read(self, tree: str, path: str) -> bytes | None:
        self.entry(tree, path)
        return self._bundle.content(tree, path)

    def load_module(self, name: str):
        if name not in self._bundle.modules:
            self._guard.reject("module is outside sealed closure")
        return importlib.import_module(name)

    def invoke(self, program: str, request: Any):
        """Run another declared program through the same descriptor boundary."""
        return self._invoke(program, request)


def _inherited_fds():
    result = set()
    try:
        names = os.listdir("/proc/self/fd")
    except OSError as error:
        raise CapsuleUnavailable("readable /proc/self/fd is required for descriptor supervision") from error
    for name in names:
        try:
            fd = int(name)
            os.fstat(fd)
            result.add(fd)
        except (ValueError, OSError):
            pass
    return result


def _worker_kernel_filter(machine):
    """Build native/prospective mappings without admitting a runtime ABI."""
    abis = {"x86_64": (0xC000003E, 0), "aarch64": (0xC00000B7, 1)}
    if machine not in abis:
        raise CapsuleUnavailable(f"sealed worker syscall ABI is unsupported: {machine}")
    architecture, column = abis[machine]
    # x86-64 / prospective AArch64 Linux syscall numbers. Only anonymous memfds
    # and pipes can be created; only protocol pipes survive worker FD admission.
    calls = {
        "read": (0, 63), "write": (1, 64), "close": (3, 57), "fstat": (5, 80),
        "lseek": (8, 62), "pread64": (17, 67), "pwrite64": (18, 68),
        "readv": (19, 65), "writev": (20, 66),
        "preadv": (295, 69), "pwritev": (296, 70),
        "preadv2": (327, 286), "pwritev2": (328, 287),
        "pipe": (22, None), "pipe2": (293, 59), "memfd_create": (319, 279),
        "dup": (32, 23), "dup2": (33, None), "dup3": (292, 24), "fcntl": (72, 25),
        "poll": (7, None), "select": (23, None),
        "pselect6": (270, 72), "ppoll": (271, 73),
        "epoll_create": (213, None), "epoll_create1": (291, 20),
        "epoll_ctl": (233, 21), "epoll_wait": (232, None),
        "epoll_pwait": (281, 22), "epoll_pwait2": (441, 441),
        # Interpreter allocation and synchronization, without new threads.
        "mmap": (9, 222), "mprotect": (10, 226), "munmap": (11, 215),
        "brk": (12, 214), "mremap": (25, 216), "madvise": (28, 233),
        "futex": (202, 98), "sched_yield": (24, 124),
        # Self/runtime observations, waits, signal handling and shutdown.
        "rt_sigaction": (13, 134), "rt_sigprocmask": (14, 135),
        "rt_sigreturn": (15, 139), "sigaltstack": (131, 132),
        "nanosleep": (35, 101), "clock_nanosleep": (230, 115),
        "clock_gettime": (228, 113), "clock_getres": (229, 114),
        "gettimeofday": (96, 169), "time": (201, None),
        "restart_syscall": (219, 128), "getrandom": (318, 278),
        "getpid": (39, 172), "getppid": (110, 173), "gettid": (186, 178),
        "getuid": (102, 174), "geteuid": (107, 175),
        "getgid": (104, 176), "getegid": (108, 177),
        "getrlimit": (97, 163), "getrusage": (98, 165), "prlimit64": (302, 261),
        "exit": (60, 93), "exit_group": (231, 94),
    }

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    kill_process, allow = 0x80000000, 0x7FFF0000
    instructions = [
        (0x20, 0, 0, 4),                       # seccomp_data.arch
        (0x15, 1, 0, architecture),
        (0x06, 0, 0, kill_process),
        (0x20, 0, 0, 0),                       # seccomp_data.nr
        (0x35, 0, 1, 0x40000000),              # no x32/alternate syscall ABI
        (0x06, 0, 0, kill_process),
    ]
    for name, numbers in calls.items():
        number = numbers[column]
        if number is None:
            continue
        rule = []
        if name == "fcntl":
            # No locks, async-signal ownership, leases or filesystem commands.
            rule.extend(((0x20, 0, 0, 28), (0x15, 1, 0, 0),
                         (0x06, 0, 0, kill_process), (0x20, 0, 0, 24)))
            for command in (0, 1, 2, 3, 1030, 1032, 1033, 1034):
                rule.extend(((0x15, 0, 1, command), (0x06, 0, 0, allow)))
            rule.append((0x06, 0, 0, kill_process))
        else:
            if name == "prlimit64":
                # pid == 0 and new_limit == NULL, including both 32-bit halves.
                for offset in (16, 20, 32, 36):
                    rule.extend(((0x20, 0, 0, offset), (0x15, 1, 0, 0),
                                 (0x06, 0, 0, kill_process)))
            rule.append((0x06, 0, 0, allow))
        instructions.append((0x15, 0, len(rule), number))
        instructions.extend(rule)
    instructions.append((0x06, 0, 0, kill_process))
    filters = (Filter * len(instructions))(*(Filter(*entry) for entry in instructions))
    return Program(len(instructions), filters)


def _lock_worker_kernel():
    """Install the closed capability policy only for an admitted runtime ABI."""
    program = _worker_kernel_filter(_runtime_machine())
    libc = ctypes.CDLL(None, use_errno=True)
    if (libc.prctl(38, 1, 0, 0, 0) != 0     # PR_SET_NO_NEW_PRIVS
            or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0):
        raise CapsuleUnavailable("sealed worker requires unprivileged seccomp-BPF")


def _worker(bundle, envelope, binding, program_raw, reply_fd, invoke_fd, invoke_reply_fd):
    for name in bundle.stdlib:
        importlib.import_module(name)
    _lock_worker_kernel()
    path = bundle.spec["programs"][envelope["program"]]
    guard = _Guard(bundle, path, program_raw)
    # Runtime globals retain private references, not program-visible cache entries.
    for name in tuple(sys.modules):
        if name not in guard.stdlib:
            del sys.modules[name]
    sys.path[:] = []
    sys.path_importer_cache.clear()
    sys.meta_path[:] = [guard]
    sys.dont_write_bytecode = True
    # Cache hits bypass both meta_path and import audits.
    builtins.__import__ = guard.import_builtin
    importlib.__import__ = guard.import_builtin
    importlib.import_module = guard.import_module
    sys.addaudithook(guard.audit)

    def no_file_spec(*args, **kwargs):
        guard.reject("filesystem module specs are forbidden after capsule validation")

    importlib.util.spec_from_file_location = no_file_spec
    importlib._bootstrap_external.spec_from_file_location = no_file_spec

    def invoke(program, request):
        raw = canonical({"program": program, "request": request})
        if len(raw) > MAX_REQUEST_BYTES:
            guard.reject("nested request exceeds limit")
        _write_all(invoke_fd, len(raw).to_bytes(4, "big") + raw)
        response = _read_frame(invoke_reply_fd, MAX_OUTPUT_BYTES)
        value = _keys(parse(response), {"result", "error"}, "nested result")
        if value["error"] is not None:
            guard.reject("nested capsule failed: " + value["error"])
        return value["result"]

    context = CapsuleContext(bundle, guard, invoke)
    if program_raw != bundle.content("base", path):
        guard.reject("program descriptor differs from exact-tree artifact")
    module = context.load_module(_module_name(path))
    function = getattr(module, "capsule_main", None)
    if not callable(function):
        guard.reject("program has no capsule_main entrypoint")
    result = function(envelope["request"], context)
    if guard.denied:
        raise CapsuleError("caught capability violation cannot produce a receipt")
    loaded = [bundle.artifacts[key] for key in sorted(guard.loaded)]
    raw = canonical({"binding": binding, "result": result, "loaded": loaded})
    if len(raw) > MAX_OUTPUT_BYTES:
        raise CapsuleError("program output exceeds limit")
    _write_all(reply_fd, raw)


def _read_frame(fd, limit):
    def exact(size):
        result = bytearray()
        while len(result) < size:
            chunk = os.read(fd, size - len(result))
            if not chunk:
                raise CapsuleError("partial framed message")
            result.extend(chunk)
        return bytes(result)

    size = int.from_bytes(exact(4), "big")
    if not 0 < size <= limit:
        raise CapsuleError("framed message exceeds limit")
    return exact(size)


def _write_before_deadline(fd, raw, deadline, life_fd):
    os.set_blocking(fd, False)
    try:
        view = memoryview(raw)
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            selector.register(life_fd, selectors.EVENT_READ)
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapsuleError("nested reply timeout")
                for ready, _ in selector.select(remaining):
                    if ready.fd == life_fd:
                        raise CapsuleError("capsule parent exited during nested reply")
                    try:
                        count = os.write(fd, view)
                    except BlockingIOError:
                        continue
                    if count <= 0:
                        raise CapsuleError("short nested reply write")
                    view = view[count:]
    finally:
        os.set_blocking(fd, True)


def _supervise(arguments, image_fd=None, interpreter_identity=None):
    worker_pid = None
    open_fds = {}
    interpreter = None

    def pipe():
        with _defer_handlers():
            pair = os.pipe()
            try:
                open_fds.update((fd, _descriptor_identity(fd)) for fd in pair)
            except BaseException:
                for fd in pair:
                    os.close(fd)
                    open_fds.pop(fd, None)
                raise
        return pair

    try:
        if len(arguments) != 8:
            raise CapsuleError("invalid descriptor bootstrap")
        interpreter = _ExecutionInterpreter.inherited(image_fd, interpreter_identity)
        runtime_fd, program_fd, request_fd, bundle_fd, life_fd = map(int, arguments[:5])
        inherited = {image_fd, runtime_fd, program_fd, request_fd, bundle_fd, life_fd}
        if len(inherited) != 6 or _inherited_fds() != {0, 1, 2, *inherited}:
            raise CapsuleError("unexpected inherited descriptor or alias")
        _prctl(36, 1)  # PR_SET_CHILD_SUBREAPER: reap the complete worker group.
        runtime_raw = _read_descriptor(runtime_fd, MAX_PROGRAM_BYTES)
        program_raw = _read_descriptor(program_fd, MAX_PROGRAM_BYTES)
        request_raw = _read_descriptor(request_fd, MAX_REQUEST_BYTES)
        bundle_raw = _read_descriptor(bundle_fd, MAX_BUNDLE_BYTES)
        if len({_descriptor_identity(fd)[:2] for fd in inherited - {life_fd}}) != 5:
            raise CapsuleError("aliased sealed descriptors")
        if [digest(request_raw), digest(bundle_raw), digest(program_raw)] != arguments[5:]:
            raise CapsuleError("descriptor digest differs from launched identity")
        bundle = _Bundle(bundle_raw)
        envelope = _keys(parse(request_raw, MAX_REQUEST_BYTES),
                         {"version", "program", "request", "nonce", "timeout", "depth",
                          "bundle_sha256", "runtime_sha256"}, "request")
        if (type(envelope["version"]) is not int or envelope["version"] != VERSION
                or not isinstance(envelope["nonce"], str)
                or SHA256.fullmatch(envelope["nonce"]) is None
                or envelope["bundle_sha256"] != digest(bundle_raw)
                or envelope["runtime_sha256"] != digest(runtime_raw)
                or runtime_raw != bundle.content("base", RUNTIME_PATH)
                or program_raw != bundle.program(envelope["program"])):
            raise CapsuleError("request does not bind exact runtime/program/artifact bytes")
        timeout, depth = envelope["timeout"], envelope["depth"]
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_SECONDS or type(depth) is not int or not 0 <= depth <= MAX_DEPTH):
            raise CapsuleError("invalid request resource bounds")
        binding = {"version": VERSION, "nonce": envelope["nonce"], "program": envelope["program"],
                   "program_sha256": digest(program_raw), "runtime_sha256": digest(runtime_raw),
                   "artifact_sha256": digest(bundle_raw), "request_sha256": digest(request_raw),
                   "payload_sha256": digest(canonical(envelope["request"]))}
        reply_r, reply_w = pipe()
        stdout_r, stdout_w = pipe()
        stderr_r, stderr_w = pipe()
        invoke_r, invoke_w = pipe()
        invoke_reply_r, invoke_reply_w = pipe()
        guardian_pid = os.getpid()
        with _defer_handlers():
            worker_pid = os.fork()
            if worker_pid:
                os.setpgid(worker_pid, worker_pid)
        if worker_pid == 0:
            try:
                os.setpgid(0, 0)
                _prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG protects guardian crashes too.
                if os.getppid() != guardian_pid:
                    os._exit(125)
                _prctl(4, 0)
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(timeout) + 1, math.ceil(timeout) + 1))
                resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_WORKER_FDS, MAX_WORKER_FDS))
                resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WORKER_FILE_BYTES, MAX_WORKER_FILE_BYTES))
                os.dup2(stdout_w, 1)
                os.dup2(stderr_w, 2)
                keep = {0, 1, 2, reply_w, invoke_w, invoke_reply_r}
                for fd in (inherited | set(open_fds)) - keep:
                    os.close(fd)
                _worker(bundle, envelope, binding, program_raw, reply_w, invoke_w, invoke_reply_r)
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(0)
            except BaseException as error:
                os.write(2, (type(error).__name__ + ": " + str(error))[:4096].encode("utf-8", "replace"))
                os._exit(1)
        for fd in (reply_w, stdout_w, stderr_w, invoke_w, invoke_reply_r):
            with _defer_handlers():
                _close_owned_fd(fd, open_fds[fd])
                del open_fds[fd]
        buffers = {reply_r: bytearray(), stdout_r: bytearray(), stderr_r: bytearray(),
                   invoke_r: bytearray()}
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for fd in (*buffers, life_fd):
                selector.register(fd, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CapsuleError("capsule worker timeout")
                for ready, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(ready.fd, 65536)
                    if ready.fd == life_fd:
                        raise CapsuleError("capsule parent exited or interrupted")
                    if not chunk:
                        selector.unregister(ready.fd)
                        continue
                    buffers[ready.fd].extend(chunk)
                    limit = MAX_OUTPUT_BYTES if ready.fd == reply_r else (
                        MAX_REQUEST_BYTES + 4 if ready.fd == invoke_r else MAX_DIAGNOSTIC_BYTES)
                    if len(buffers[ready.fd]) > limit:
                        raise CapsuleError("worker output exceeds limit")
                pending = buffers[invoke_r]
                if len(pending) >= 4:
                    size = int.from_bytes(pending[:4], "big")
                    if not 0 < size <= MAX_REQUEST_BYTES:
                        raise CapsuleError("nested request exceeds limit")
                    if len(pending) >= size + 4:
                        if len(pending) != size + 4:
                            raise CapsuleError("overlapping nested requests")
                        nested = _keys(parse(bytes(pending[4:]), MAX_REQUEST_BYTES),
                                       {"program", "request"}, "nested request")
                        pending.clear()
                        # Each nested launch has its own guardian. If this parent
                        # dies, its liveness writer closes and that guardian cleans up.
                        interpreter.check()
                        with SealedBytes(bundle_raw, "nested-artifacts", MAX_BUNDLE_BYTES) as artifact:
                            with SealedBytes(runtime_raw, "nested-runtime", MAX_PROGRAM_BYTES) as runtime:
                                result = _execute(artifact, runtime, nested["program"], nested["request"],
                                                  min(MAX_SECONDS, max(0.001, deadline - time.monotonic())),
                                                  depth + 1, life_fd, _interpreter=interpreter)
                        response = canonical({"result": {"value": result.value, "receipt": result.receipt},
                                              "error": None})
                        if len(response) > MAX_OUTPUT_BYTES:
                            raise CapsuleError("nested result exceeds limit")
                        _write_before_deadline(
                            invoke_reply_w, len(response).to_bytes(4, "big") + response,
                            deadline, life_fd)
                exited = os.waitid(os.P_PID, worker_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if exited is not None:
                    # Keep the leader unreaped until group cleanup to prevent
                    # PID reuse from targeting an unrelated process group.
                    with _defer_handlers():
                        _kill_group(worker_pid)
                        _, status = os.waitpid(worker_pid, 0)
                        worker_pid = None
                    if status != 0:
                        if bytes(buffers[stderr_r]).startswith(b"CapsuleUnavailable:"):
                            raise CapsuleUnavailable(bytes(buffers[stderr_r])[:4096].decode("utf-8", "replace"))
                        raise CapsuleError(f"worker crashed: {bytes(buffers[stderr_r])[:4096]!r}")
                    for fd in (reply_r, stdout_r, stderr_r):
                        while fd in selector.get_map():
                            chunk = os.read(fd, 65536)
                            if not chunk:
                                selector.unregister(fd)
                                break
                            buffers[fd].extend(chunk)
                            if len(buffers[fd]) > (MAX_OUTPUT_BYTES if fd == reply_r else MAX_DIAGNOSTIC_BYTES):
                                raise CapsuleError("worker output exceeds limit")
                    if pending or buffers[stdout_r] or buffers[stderr_r]:
                        raise CapsuleError("partial request or unexpected program stdout/stderr")
                    output = _keys(parse(bytes(buffers[reply_r])),
                                   {"binding", "result", "loaded"}, "worker result")
                    if canonical(output["binding"]) != canonical(binding):
                        raise CapsuleError("worker receipt binding mismatch")
                    output["diagnostics"] = {"stdout_sha256": digest(b""), "stderr_sha256": digest(b"")}
                    _write_all(1, canonical(output))
                    return
    except BaseException as error:
        os.write(2, (type(error).__name__ + ": " + str(error))[:4096].encode("utf-8", "replace"))
        raise SystemExit(125)
    finally:
        if interpreter is not None or open_fds or worker_pid is not None:
            with _defer_handlers():
                try:
                    if worker_pid is not None and worker_pid > 0:
                        _stop_worker(worker_pid)
                finally:
                    for fd, identity in open_fds.items():
                        _close_owned_fd(fd, identity)
                    if interpreter is not None:
                        interpreter.close()
                    while True:
                        try:
                            child, _ = os.waitpid(-1, os.WNOHANG)
                            if child == 0:
                                break
                        except ChildProcessError:
                            break
