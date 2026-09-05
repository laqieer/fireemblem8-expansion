"""Protected broker journal and bounded Git publication, used by the real service."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import resource
import selectors
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from scripts.workflow_pilot.git_broker_protocol import (
    MAX_EXPANDED, MAX_JOURNAL_ROWS, MAX_LIFETIME, MAX_OBJECT, MAX_OBJECTS, MAX_PACK,
    MAX_PROCESS_OUTPUT, PROCESS_SECONDS, Policy, expected_refs, plan_digest,
    validate_authority_records, validate_plan,
)
from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, format_utc, oid, parse_utc, strict_json, utc_now,
)


class ProcessError(RecordError):
    """A subprocess failed; never include its potentially credentialed output."""


def child_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (PROCESS_SECONDS, PROCESS_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_EXPANDED, MAX_EXPANDED))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def run_bounded(
    arguments: list[str], *, cwd: Path, environment: dict[str, str],
    deadline: datetime, input_bytes: bytes = b"", allowed_codes: tuple[int, ...] = (0,),
    maximum_output: int = MAX_PROCESS_OUTPUT, pass_fds: tuple[int, ...] = (),
    capture_stderr: bool = False,
) -> bytes:
    seconds = min(PROCESS_SECONDS, (deadline - utc_now()).total_seconds())
    if seconds <= 0:
        raise ProcessError("process deadline expired")
    # The independent timeout survives a SIGKILL of the broker. Deployed
    # systemd supervision additionally kills the complete service cgroup.
    command = [
        "/usr/bin/timeout", "--signal=KILL", f"{seconds:.6f}s",
        *arguments,
    ]
    end = time.monotonic() + seconds
    process = subprocess.Popen(
        command, cwd=cwd, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        close_fds=True, pass_fds=pass_fds, preexec_fn=child_limits,
    )
    output, count, offset = bytearray(), 0, 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            if input_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map():
                left = min(end - time.monotonic(), (deadline - utc_now()).total_seconds())
                if left <= 0:
                    raise ProcessError("process deadline expired")
                for event, _ in selector.select(min(left, 0.1)):
                    stream = event.fileobj
                    if stream is process.stdin:
                        try:
                            offset += os.write(stream.fileno(), input_bytes[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(input_bytes)
                        if offset == len(input_bytes):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        count += len(chunk)
                        if count > maximum_output:
                            raise ProcessError("process output bound")
                        if stream is process.stdout or capture_stderr:
                            output.extend(chunk)
            left = min(end - time.monotonic(), (deadline - utc_now()).total_seconds())
            if left <= 0:
                raise ProcessError("process deadline expired")
            if process.wait(timeout=left) not in allowed_codes:
                raise ProcessError("protected subprocess rejected operation")
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProcessError("bounded subprocess failed") from error
    finally:
        # Successful commands may also have background descendants holding FDs.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def clean_environment(home: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_EXEC_PATH": "/usr/lib/git-core",
    }


class PublicationStore:
    """Not a caller-facing authority; construct only after service preflight."""

    def __init__(self, policy: Policy, state: Path, installation: Path | None = None, transport: dict | None = None):
        self.policy = policy
        self.state = state
        self.installation = installation
        self.transport = transport or {"kind": "local"}
        self._request_end = None
        self.home = state / "home"
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.work = state / "work"
        self.work.mkdir(mode=0o700, exist_ok=True)
        self.lock_fd = os.open(
            state / "broker.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
        )
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = state / "nonces.sqlite3"
            if journal.is_symlink():
                raise RecordError("journal must not be a link")
            self.db = sqlite3.connect(journal, timeout=1, isolation_level=None)
            os.chmod(journal, 0o600)
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS installation (identity BLOB NOT NULL)")
            identity = canonical_json(policy.__dict__)
            installed = self.db.execute("SELECT identity FROM installation").fetchall()
            if not installed:
                self.db.execute("INSERT INTO installation VALUES (?)", (identity,))
            elif installed != [(identity,)]:
                raise RecordError("journal belongs to another installed authority")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS operations ("
                "nonce TEXT PRIMARY KEY, digest TEXT NOT NULL, sequence INTEGER NOT NULL,"
                "status TEXT NOT NULL, old_refs BLOB NOT NULL, new_refs BLOB NOT NULL,"
                "completed_at TEXT, expires_at TEXT NOT NULL)"
            )
            self.db.execute(
                "UPDATE operations SET status='uncertain' WHERE status IN ('reserved','executing')"
            )
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            os.close(self.lock_fd)
            raise

    def close(self) -> None:
        self.db.close()
        os.close(self.lock_fd)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reserve(self, plan: dict, peer: str) -> None:
        validate_plan(plan, self.policy, peer, utc_now())
        with self.transaction():
            if self.db.execute(
                "SELECT 1 FROM operations WHERE status IN ('reserved','executing','uncertain') LIMIT 1"
            ).fetchone():
                raise RecordError("previous operation needs protected reconciliation")
            if self.db.execute("SELECT count(*) FROM operations").fetchone()[0] >= MAX_JOURNAL_ROWS:
                raise RecordError("nonce journal capacity reached")
            latest = self.db.execute(
                "SELECT sequence,new_refs FROM operations WHERE status='published' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                if plan["sequence"] != 0 or plan["operation"] != "bootstrap":
                    raise RecordError("new journal requires a protected genesis, not copied history")
            elif (
                plan["sequence"] != latest[0] + 1
                or canonical_json(expected_refs(plan, "old")) != latest[1]
            ):
                raise RecordError("publication rolls back or skips the journal's exact authority/anchor head")
            try:
                self.db.execute(
                    "INSERT INTO operations VALUES (?,?,?,'reserved',?,?,NULL,?)",
                    (
                        plan["nonce"], plan_digest(plan), plan["sequence"],
                        canonical_json(expected_refs(plan, "old")),
                        canonical_json(expected_refs(plan, "new")), plan["expires_at"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RecordError("publication capability already consumed") from error

    def abort(self, plan: dict) -> None:
        self.db.execute(
            "UPDATE operations SET status='rejected' WHERE nonce=? AND digest=? AND status='reserved'",
            (plan["nonce"], plan_digest(plan)),
        )

    @contextmanager
    def request_deadline(self, deadline, monotonic_end=None):
        end = time.monotonic() + min(MAX_LIFETIME, (deadline - utc_now()).total_seconds())
        self._request_end = end if monotonic_end is None else min(end, monotonic_end)
        try:
            yield
        finally:
            self._request_end = None

    def _git(
        self, repository: Path | None, *arguments: str, deadline: datetime,
        input_bytes: bytes = b"", remote: bool = False,
    ) -> bytes:
        if self._request_end is not None:
            deadline = min(
                deadline, utc_now() + timedelta(seconds=self._request_end - time.monotonic()),
            )
        environment = clean_environment(self.home)
        command = [
            "/usr/bin/git", "-c", "core.fsmonitor=false",
            "-c", "core.attributesFile=/dev/null", "-c", "gc.auto=0",
            "-c", "maintenance.auto=false", "-c", "pack.threads=1",
            "-c", "protocol.allow=never", "-c", "protocol.ext.allow=never",
        ]
        if repository is not None:
            command.append(f"--git-dir={repository}")
        with ExitStack() as credentials:
            if remote:
                kind = self.transport["kind"]
                if kind == "local":
                    command.extend(["-c", "protocol.file.allow=always"])
                elif kind in {"https", "ssh"}:
                    from scripts.workflow_pilot import git_broker as broker

                    environment.update(credentials.enter_context(broker.verified_credentials(
                        self.installation, self.policy, self.transport, self.state, deadline,
                    )))
                    if kind == "https":
                        helper = self.transport["helper"]
                        command.extend([
                            "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
                            "-c", "http.sslVerify=true", "-c", f"http.sslCAInfo={broker.GITHUB_CA}",
                            "-c", "credential.helper=",
                            "-c", f"credential.helper=!/usr/bin/python3 -I {shlex.quote(helper)} credential",
                            "-c", "credential.useHttpPath=true",
                        ])
                    else:
                        command.extend(["-c", "protocol.ssh.allow=always"])
                        environment["GIT_SSH_VARIANT"] = "ssh"
                        environment["GIT_SSH_COMMAND"] = shlex.join(broker.ssh_arguments(
                            environment["FE8_BROKER_CREDENTIAL"], environment["FE8_BROKER_KNOWN_HOSTS"],
                        ))
                else:
                    raise RecordError("unknown installed transport")
            if self._request_end is not None:
                deadline = min(deadline, utc_now() + timedelta(seconds=self._request_end - time.monotonic()))
            return run_bounded(
                [*command, *arguments], cwd=self.state, environment=environment,
                deadline=deadline, input_bytes=input_bytes,
            )

    def remote_refs(self, deadline: datetime) -> dict[str, str | None]:
        raw = self._git(
            None, "ls-remote", "--refs", self.policy.endpoint, *self.policy.refs,
            deadline=deadline, remote=True,
        )
        result = dict.fromkeys(self.policy.refs)
        seen = set()
        try:
            for line in raw.decode("ascii").splitlines():
                object_id, ref = line.split("\t")
                oid(object_id)
                if ref not in result or ref in seen:
                    raise RecordError("remote returned unexpected/duplicate ref")
                seen.add(ref)
                result[ref] = object_id
        except (UnicodeError, ValueError) as error:
            raise RecordError("invalid exact remote readback") from error
        return result

    def local_protection(self, deadline: datetime) -> None:
        if self.transport["kind"] != "local":
            return
        remote = Path(self.policy.endpoint[7:])
        allowed = {
            "core.repositoryformatversion", "core.filemode", "core.bare",
            "core.logallrefupdates", "receive.denynonfastforwards",
            "receive.denydeletes", "receive.fsckobjects", "transfer.fsckobjects",
            "receive.advertiseatomic",
        }
        configuration = self._git(
            None, "config", "--file", str(remote / "config"), "--null", "--list", deadline=deadline,
        )
        seen = set()
        for entry in configuration.rstrip(b"\0").split(b"\0"):
            key = entry.split(b"\n", 1)[0].decode("ascii").lower()
            if key not in allowed or key in seen:
                raise RecordError("protected local Git config has an external/duplicate execution seam")
            seen.add(key)
        if self._git(
            None, "config", "--file", str(remote / "config"), "--get", "core.repositoryformatversion",
            deadline=deadline,
        ) != b"0\n":
            raise RecordError("only SHA-1 bare repository format zero is supported")
        for name in ("receive.denyNonFastForwards", "receive.denyDeletes", "core.bare"):
            value = self._git(
                None, "config", "--file", str(remote / "config"), "--type=bool", "--get", name,
                deadline=deadline,
            )
            if value != b"true\n":
                raise RecordError("local remote does not enforce protected history")

    def _load_pack(self, repository: Path, pack: bytes, plan: dict, deadline: datetime) -> None:
        if (
            len(pack) != plan["pack"]["size"] or len(pack) > MAX_PACK
            or hashlib.sha256(pack).hexdigest() != plan["pack"]["sha256"]
            or pack[:4] != b"PACK" or len(pack) < 32
        ):
            raise RecordError("pack bytes do not match signed plan")
        version, count = struct.unpack(">II", pack[4:12])
        if version not in (2, 3) or not 1 <= count <= MAX_OBJECTS or count != len(plan["pack"]["objects"]):
            raise RecordError("pack count/version bound")
        self._git(None, "init", "--bare", "--quiet", "--template=", str(repository), deadline=deadline)
        result = self._git(
            repository, "index-pack", "--stdin", "--strict", "--threads=1",
            f"--max-input-size={MAX_PACK}", deadline=deadline, input_bytes=pack,
        )
        if re.fullmatch(rb"pack\t[0-9a-f]{40}\n", result) is None:
            raise RecordError("invalid index-pack result")
        pack_id = result[5:-1].decode("ascii")
        index = repository / "objects" / "pack" / f"pack-{pack_id}.idx"
        description = self._git(repository, "verify-pack", "-v", str(index), deadline=deadline)
        objects = []
        for line in description.splitlines():
            parts = line.split()
            if parts and re.fullmatch(rb"[0-9a-f]{40}", parts[0]):
                if len(parts) < 5 or parts[1] not in {b"commit", b"tree", b"blob"}:
                    raise RecordError("unsupported pack object")
                objects.append(parts[0].decode("ascii"))
        if sorted(objects) != plan["pack"]["objects"]:
            raise RecordError("exact pack object set mismatch")
        # verify-pack reports instruction bytes for deltas, not reconstructed
        # sizes. Query every verified OID in this isolated object database.
        resolved = self._git(
            repository, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            deadline=deadline, input_bytes=("\n".join(objects) + "\n").encode("ascii"),
        ).splitlines()
        if len(resolved) != len(objects):
            raise RecordError("resolved object count mismatch")
        expanded = 0
        for object_id, line in zip(objects, resolved):
            match = re.fullmatch(rb"([0-9a-f]{40}) (commit|tree|blob) ([0-9]{1,20})", line)
            if match is None or match[1].decode("ascii") != object_id:
                raise RecordError("invalid resolved object metadata")
            size = int(match[3])
            if size > MAX_OBJECT:
                raise RecordError("individual expanded object bound")
            expanded += size
            if expanded > MAX_EXPANDED:
                raise RecordError("total expanded object bound")
        closure = self._git(
            repository, "rev-list", "--objects", "--no-object-names",
            *(update["new"] for update in plan["updates"]), deadline=deadline,
        ).decode("ascii").splitlines()
        if sorted(closure) != plan["pack"]["objects"]:
            raise RecordError("pack must contain precisely the complete reachable closure")

    def _record(self, repository: Path, object_id: str, name: str, deadline: datetime) -> tuple[dict, list[str]]:
        kind = self._git(repository, "cat-file", "-t", object_id, deadline=deadline)
        if kind != b"commit\n":
            raise RecordError("authority and anchor must be commits")
        commit = self._git(repository, "cat-file", "commit", object_id, deadline=deadline)
        header = commit.split(b"\n\n", 1)[0].splitlines()
        parents = [line[7:].decode("ascii") for line in header if line.startswith(b"parent ")]
        trees = [line[5:].decode("ascii") for line in header if line.startswith(b"tree ")]
        if len(trees) != 1:
            raise RecordError("invalid authority commit tree")
        tree = self._git(repository, "ls-tree", "-z", trees[0], deadline=deadline)
        match = re.fullmatch(rb"100644 blob ([0-9a-f]{40})\t" + re.escape(name.encode("ascii")) + rb"\x00", tree)
        if match is None:
            raise RecordError("authority tree must contain only its regular canonical JSON record")
        raw = self._git(repository, "cat-file", "blob", match[1].decode("ascii"), deadline=deadline)
        return strict_json(raw, MAX_OBJECT), parents

    def publish_reserved(
        self, plan: dict, pack: bytes, deadline: datetime, *, monotonic_end=None,
    ) -> tuple[str, dict, str | None]:
        with self.request_deadline(deadline, monotonic_end):
            return self._publish_reserved(plan, pack, deadline)

    def _publish_reserved(self, plan: dict, pack: bytes, deadline: datetime) -> tuple[str, dict, str | None]:
        deadline = min(deadline, parse_utc(plan["expires_at"]))
        with self.transaction():
            cursor = self.db.execute(
                "UPDATE operations SET status='executing' WHERE nonce=? AND digest=? AND status='reserved'",
                (plan["nonce"], plan_digest(plan)),
            )
            if cursor.rowcount != 1:
                raise RecordError("no unused reservation for exact plan")
        repository = self.work / uuid.uuid4().hex
        status, refs, completed, push_started = "rejected", dict.fromkeys(self.policy.refs), None, False
        try:
            self._load_pack(repository, pack, plan, deadline)
            records, previous = [], []
            by_ref = {update["ref"]: update for update in plan["updates"]}
            for ref, name in zip(self.policy.refs, ("authority.json", "anchor.json")):
                update = by_ref[ref]
                record, parents = self._record(repository, update["new"], name, deadline)
                if parents != ([] if update["old"] is None else [update["old"]]):
                    raise RecordError("new commit is not the exact single-parent fast-forward")
                records.append(record)
                previous.append(
                    None if update["old"] is None
                    else self._record(repository, update["old"], name, deadline)[0]
                )
            validate_authority_records(self.policy, plan, *records, *previous)
            self.local_protection(deadline)
            refs = self.remote_refs(deadline)
            if refs != expected_refs(plan, "old"):
                raise RecordError("remote compare-and-swap or sequence is stale")
            validate_plan(plan, self.policy, self.policy.client_certificate_sha256, utc_now())
            push_started = True
            self._git(
                repository, "push", "--atomic", "--porcelain", "--no-verify",
                *(f"--force-with-lease={update['ref']}:{update['old'] or ''}" for update in plan["updates"]),
                self.policy.endpoint,
                *(f"{update['new']}:{update['ref']}" for update in plan["updates"]),
                deadline=deadline, remote=True,
            )
            refs = self.remote_refs(deadline)
            completed_time = utc_now()
            if (
                refs != expected_refs(plan, "new")
                or not parse_utc(plan["issued_at"]) <= completed_time < deadline
                or (self._request_end is not None and time.monotonic() >= self._request_end)
            ):
                raise RecordError("atomic publication lacks timely exact remote readback")
            completed = format_utc(completed_time)
            status = "published"
        except (RecordError, OSError, UnicodeError, ValueError, TypeError, KeyError):
            status = "uncertain" if push_started else "rejected"
            if push_started:
                try:
                    refs = self.remote_refs(deadline)
                    if refs == expected_refs(plan, "old"):
                        status = "rejected"
                except (RecordError, OSError):
                    pass
        except BaseException:
            status = "uncertain" if push_started else "rejected"
            raise
        finally:
            if repository.exists():
                shutil.rmtree(repository)
            self.db.execute(
                "UPDATE operations SET status=?,completed_at=? WHERE nonce=? AND digest=?",
                (status, completed, plan["nonce"], plan_digest(plan)),
            )
        return status, refs, completed

    def readback(
        self, plan: dict, peer: str, deadline: datetime, *, monotonic_end=None,
    ) -> tuple[str, dict, str | None]:
        with self.request_deadline(deadline, monotonic_end):
            return self._readback(plan, peer, deadline)

    def _readback(self, plan: dict, peer: str, deadline: datetime) -> tuple[str, dict, str | None]:
        # Historical queries cannot publish. Validate signatures/identity at the
        # signed issuance time, then use a new authenticated bounded session.
        validate_plan(plan, self.policy, peer, parse_utc(plan["issued_at"]))
        row = self.db.execute(
            "SELECT digest,status,completed_at FROM operations WHERE nonce=?", (plan["nonce"],),
        ).fetchone()
        refs = self.remote_refs(deadline)
        if row is None:
            return "not_found", refs, None
        if row[0] != plan_digest(plan):
            raise RecordError("nonce is bound to another exact plan")
        status = row[1] if row[1] in {"published", "rejected", "uncertain"} else "uncertain"
        if status == "published" and refs != expected_refs(plan, "new"):
            return "uncertain", refs, None
        return status, refs, row[2] if status == "published" else None
