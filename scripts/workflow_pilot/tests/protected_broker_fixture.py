#!/usr/bin/env python3
"""Required local authority acceptance; fails rather than simulating missing UIDs.

Run only in a disposable root-owned Linux test installation. This creates no
users, changes no system settings, contacts no network remote and signals only
the exact processes it starts. All artifacts remain below this checkout.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if __name__ == "__main__":
    if not sys.flags.isolated:
        raise SystemExit("protected broker fixture requires /usr/bin/python3 -I")
    sys.path.insert(0, str(ROOT))

from scripts.workflow_pilot import git_broker as broker
from scripts.workflow_pilot import git_broker_protocol as protocol
from scripts.workflow_pilot.git_broker_store import clean_environment
from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, format_utc, strict_json, utc_now,
)
from scripts.workflow_pilot.tests.broker_test_support import Fixture, Keys, artifact_directory


def private_copy(source, destination, owner):
    shutil.copyfile(source, destination)
    os.chown(destination, owner, owner)
    destination.chmod(0o600)


def recursive_owner(root, uid):
    for directory, children, files in os.walk(root):
        os.chown(directory, uid, uid)
        for name in children + files:
            os.chown(Path(directory) / name, uid, uid)


def wait_for_socket(process, endpoint):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RecordError("installed broker failed its production preflight")
        if endpoint.exists():
            return
        time.sleep(0.02)
    raise RecordError("broker did not expose its bounded ready endpoint")


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def candidate_probe(uid, paths, endpoint, server_pid):
    program = """
import json, os, socket, sys
paths, endpoint, server_pid = json.loads(sys.argv[1])
result = {}
for path in paths:
    for mode, flag in (("read", os.O_RDONLY), ("write", os.O_WRONLY)):
        try:
            fd = os.open(path, flag | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            result[path + ":" + mode] = "denied"
        else:
            os.close(fd)
            result[path + ":" + mode] = "accessible"
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
    channel.settimeout(2)
    try:
        channel.connect(endpoint)
        channel.sendall(b"unauthenticated direct request")
        data = channel.recv(1)
        result["direct_protocol"] = "denied" if not data else "accessible"
    except OSError:
        result["direct_protocol"] = "denied"
print(json.dumps(result, sort_keys=True))
"""
    output = subprocess.run(
        ["/usr/bin/python3", "-I", "-c", program, json.dumps([paths, str(endpoint), server_pid])],
        user=uid, group=uid, extra_groups=[], cwd="/", env={"PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=5, check=True, close_fds=True,
    )
    values = json.loads(output.stdout)
    if any(value != "denied" for value in values.values()):
        raise RecordError("candidate crossed the actual protected principal boundary")
    return len(values)


def direct_authenticated_request(client, plan, pack=None, *, reserve=False):
    manifest = client.manifest
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.settimeout(3)
        raw.connect(manifest["socket"])
        broker.peer_uid(raw, manifest["broker_uid"])
        with broker.tls_context(manifest, False).wrap_socket(
            raw, server_hostname="workflow-pilot-git-broker",
        ) as connection:
            if hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest() != manifest["server_certificate_sha256"]:
                raise RecordError("protected fixture broker pin changed")
            channel = broker.Channel(connection, utc_now() + timedelta(seconds=3))
            hello = strict_json(channel.read_frame(protocol.MAX_RESPONSE), protocol.MAX_RESPONSE)
            channel.send_frame(canonical_json({
                "protocol": protocol.PROTOCOL, "session_nonce": hello["session_nonce"],
                "operation": "publish", "plan": plan,
            }), protocol.MAX_JSON)
            if pack is not None:
                channel.send_frame(pack, protocol.MAX_PACK)
            if reserve:
                connection.settimeout(20)
                return {"disconnected": not connection.recv(1)}
            return channel.read_frame(protocol.MAX_RESPONSE)


def client_action(arguments):
    consumer = broker.BrokerClient(arguments.client_installation)
    if arguments.client_action == "preflight":
        return consumer.request()
    plan = strict_json(broker.read_regular(arguments.plan, protocol.MAX_JSON), protocol.MAX_JSON)
    if arguments.client_action == "attack":
        try:
            direct_authenticated_request(consumer, plan)
        except (RecordError, OSError):
            return {"rejected": True}
        return {"rejected": False}
    if arguments.client_action == "reserve":
        return direct_authenticated_request(consumer, plan, reserve=True)
    pack = broker.read_regular(arguments.pack, protocol.MAX_PACK) if arguments.pack else None
    return consumer.request(plan, pack, readback=arguments.client_action == "readback")


def exercise(broker_uid, coordinator_uid, candidate_uid):
    if (
        os.geteuid() != 0 or 0 in (broker_uid, coordinator_uid, candidate_uid)
        or len({broker_uid, coordinator_uid, candidate_uid}) != 3
    ):
        raise RecordError("external root-owned three-principal fixture is unavailable")
    # Root inside a one-UID user namespace is not a substitute for three kernel
    # principals. Nor may a root test execute code below a candidate-owned path.
    for parent in (ROOT, *ROOT.parents):
        metadata = parent.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not metadata.st_mode & 0o001:
            raise RecordError("fixture needs a root-owned publicly traversable source installation")
    root = artifact_directory("protected-git-broker")
    root.chmod(0o755)
    process, fixture = None, None
    try:
        keys = Keys(root / "keys")
        fixture = Fixture(root / "fixture", keys)
        fixture.root.chmod(0o755)
        fixture.store.close()
        fixture.store = None
        recursive_owner(fixture.state, broker_uid)
        recursive_owner(fixture.remote, broker_uid)
        runtime, server_keys, client_keys = root / "runtime", root / "server-keys", root / "client-keys"
        for directory, owner, mode in (
            (runtime, broker_uid, 0o755), (server_keys, broker_uid, 0o700),
            (client_keys, coordinator_uid, 0o700),
        ):
            directory.mkdir(mode=mode)
            os.chown(directory, owner, owner)
        public_ca = root / "ca.crt"
        shutil.copyfile(keys.root / "ca.crt", public_ca)
        public_ca.chmod(0o644)
        for name in ("server.crt", "server.key"):
            private_copy(keys.root / name, server_keys / name, broker_uid)
        for name in ("client.crt", "client.key"):
            private_copy(keys.root / name, client_keys / name, coordinator_uid)
        server = fixture.manifest()
        client = fixture.manifest(client=True)
        for config in (server, client):
            config.update({
                "broker_uid": broker_uid, "coordinator_uid": coordinator_uid,
                "candidate_uids": [candidate_uid], "socket": str(runtime / "broker.sock"),
                "ca_certificate": str(public_ca),
            })
        server.update({
            "certificate": str(server_keys / "server.crt"), "private_key": str(server_keys / "server.key"),
            "response_private_key": str(server_keys / "server.key"),
        })
        client.update({
            "certificate": str(client_keys / "client.crt"), "private_key": str(client_keys / "client.key"),
        })
        server_path, client_path = root / "server.json", root / "client.json"
        server_path.write_bytes(canonical_json(server))
        client_path.write_bytes(canonical_json(client))
        server_path.chmod(0o644)
        client_path.chmod(0o644)
        entry = ROOT / "scripts/workflow_pilot/git_broker.py"
        endpoint = Path(server["socket"])
        plan_path, pack_path = root / "plan.json", root / "objects.pack"

        def client_command(action, plan=None, pack=None, *, installation=None, background=False):
            command = [
                "/usr/bin/python3", "-I", str(Path(__file__).resolve()),
                "--client-action", action, "--client-installation", str(installation or client_path),
            ]
            if plan is not None:
                plan_path.write_bytes(canonical_json(plan))
                plan_path.chmod(0o644)
                command.extend(["--plan", str(plan_path)])
            if pack is not None:
                pack_path.write_bytes(pack)
                pack_path.chmod(0o644)
                command.extend(["--pack", str(pack_path)])
            options = dict(
                user=coordinator_uid, group=coordinator_uid, extra_groups=[],
                cwd=root, env={"PATH": "/usr/bin:/bin"}, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if background:
                return subprocess.Popen(command, **options)
            completed = subprocess.run(command, timeout=35, **options)
            if completed.returncode:
                raise RecordError("protected coordinator command failed closed")
            return strict_json(completed.stdout, protocol.MAX_RESPONSE)

        def start():
            child = subprocess.Popen(
                ["/usr/bin/python3", "-I", str(entry), "serve", "--installation", str(server_path)],
                user=broker_uid, group=broker_uid, extra_groups=[], cwd=root,
                env=clean_environment(fixture.state / "home"), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
            )
            try:
                wait_for_socket(child, endpoint)
            except BaseException:
                stop_owned(child)
                raise
            return child

        process = start()
        if client_command("preflight").get("ready") is not True:
            raise RecordError("real protected production consumer did not authenticate")
        plan, pack, current = fixture.make_plan()
        result = client_command("publish", plan, pack)
        if result["status"] != "published" or result["refs"] != protocol.expected_refs(plan, "new"):
            raise RecordError("valid protected atomic publication failed")
        fixture.current = current
        if client_command("readback", plan)["status"] != "published":
            raise RecordError("protected authenticated result readback failed")
        hook = fixture.remote / "hooks" / "pre-receive"
        hook.parent.mkdir(mode=0o755)
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        object_file = next(
            path for path in (fixture.remote / "objects").rglob("*") if path.is_file()
        )
        denied = candidate_probe(candidate_uid, [
            str(client_keys / "client.key"), str(server_keys / "server.key"),
            str(fixture.remote / "config"), str(hook), str(object_file),
            str(fixture.remote / fixture.policy.refs[0]),
            f"/proc/{process.pid}/environ", f"/proc/{process.pid}/mem",
        ], endpoint, process.pid)
        coordinator_denials = candidate_probe(coordinator_uid, [
            str(server_keys / "server.key"), str(fixture.remote / "config"),
            str(hook), str(object_file), f"/proc/{process.pid}/environ",
        ], endpoint, process.pid)
        advance, advance_pack, _next = fixture.make_plan()
        if client_command("publish", advance, advance_pack)["status"] != "rejected":
            raise RecordError("protected receive-pack hook was bypassed")
        before = fixture.git(fixture.remote, "show-ref")
        for kind in ("expired", "future", "issue", "endpoint", "master", "tag", "replay"):
            changed = copy.deepcopy(plan if kind == "replay" else advance)
            if kind == "expired":
                changed["issued_at"] = format_utc(utc_now() - timedelta(seconds=20))
                changed["expires_at"] = format_utc(utc_now() - timedelta(seconds=1))
            elif kind == "future":
                changed["issued_at"] = format_utc(utc_now() + timedelta(seconds=5))
                changed["expires_at"] = format_utc(utc_now() + timedelta(seconds=10))
            elif kind == "issue":
                changed["issue"] = 178
            elif kind == "endpoint":
                changed["endpoint"] = "https://github.com/other/repository.git"
            elif kind == "master":
                changed["updates"][0]["ref"] = "refs/heads/master"
            elif kind == "tag":
                changed["updates"][0]["ref"] = "refs/tags/unapproved"
            keys.sign(protocol.PLAN_DOMAIN, changed)
            if client_command("attack", changed) != {"rejected": True}:
                raise RecordError("broker independently accepted a prohibited publication")
        if fixture.git(fixture.remote, "show-ref") != before:
            raise RecordError("rejected attack changed protected refs")
        for name, change in (
            ("same-uid", {"broker_uid": coordinator_uid}), ("abstract", {"socket": "\0not-authority"}),
            ("wrong-peer", {"broker_uid": candidate_uid}),
            ("server-swap", {"server_certificate_sha256": "a" * 64}),
        ):
            bad = root / (name + ".json")
            bad.write_bytes(canonical_json({**client, **change}))
            bad.chmod(0o644)
            try:
                client_command("preflight", installation=bad)
            except (RecordError, OSError):
                pass
            else:
                raise RecordError("untrusted endpoint or server substitution accepted")
        pending, _pending_pack, _next = fixture.make_plan()
        waiting_client = client_command("reserve", pending, background=True)
        try:
            end, reserved = time.monotonic() + 3, False
            while time.monotonic() < end:
                with sqlite3.connect(
                    f"file:{fixture.state / 'nonces.sqlite3'}?mode=ro", uri=True, timeout=0.1,
                ) as journal:
                    row = journal.execute(
                        "SELECT status FROM operations WHERE nonce=?", (pending["nonce"],),
                    ).fetchone()
                if row == ("reserved",):
                    reserved = True
                    break
                time.sleep(0.01)
            if not reserved:
                raise RecordError("broker did not durably consume partial request")
            owned_inode = endpoint.stat().st_ino
            process.kill()
            process.wait(timeout=3)
            process = None
            waiting_client.communicate(timeout=3)
        finally:
            stop_owned(waiting_client)
        # Only this fixture's stopped process and socket are eligible for
        # cleanup; production relies on the exact systemd runtime directory.
        if endpoint.lstat().st_ino != owned_inode:
            raise RecordError("fixture socket changed ownership during teardown")
        endpoint.unlink()
        process = start()
        if client_command("readback", pending)["status"] != "uncertain":
            raise RecordError("SIGKILL/restart lost the consumed operation")
        if client_command("attack", pending) != {"rejected": True}:
            raise RecordError("interrupted capability was reusable after restart")
        stop_owned(process)
        process = None
        return {
            "case": "TC-WORKFLOW-AUTHENTICATED-GIT-BROKER-001",
            "protected_local": "passed", "candidate_denials": denied,
            "coordinator_credential_denials": coordinator_denials,
            "killed_reservation_replay": "rejected after durable restart",
            "atomic_refs": list(fixture.policy.refs),
            "credentialed_github": "not exercised; externally provisioned disposable endpoint required",
        }
    finally:
        stop_owned(process)
        if fixture is not None and fixture.store is not None:
            fixture.close()
        shutil.rmtree(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-uid", type=int, default=65534)
    parser.add_argument("--coordinator-uid", type=int, default=65532)
    parser.add_argument("--candidate-uid", type=int, default=65533)
    parser.add_argument("--client-action", choices=("preflight", "publish", "readback", "attack", "reserve"))
    parser.add_argument("--client-installation", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--pack", type=Path)
    arguments = parser.parse_args()
    try:
        result = (
            client_action(arguments) if arguments.client_action
            else exercise(arguments.broker_uid, arguments.coordinator_uid, arguments.candidate_uid)
        )
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (RecordError, OSError, ValueError, subprocess.SubprocessError):
        print(
            "protected broker fixture: BLOCKED/FAILED; require distinct real UIDs "
            "and a root-owned installation in a disposable authorized Linux environment",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
