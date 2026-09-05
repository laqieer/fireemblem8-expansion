"""Synthetic, noncredentialed fixtures; not evidence of an installed OS boundary."""

import base64
import copy
import grp
import hashlib
import importlib.util
import marshal
import os
import pwd
import shutil
import struct
import subprocess
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.workflow_pilot import git_broker as broker
from scripts.workflow_pilot import git_broker_protocol as protocol
from scripts.workflow_pilot.git_broker_store import PublicationStore, clean_environment
from scripts.workflow_pilot.signed_records import (
    COORDINATOR_KEY_DOMAIN, canonical_json, format_utc, signed_payload, utc_now,
)


ARTIFACTS = Path(__file__).resolve().parents[3] / "build" / "test-artifacts"


def artifact_directory(prefix):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    root = ARTIFACTS / (prefix + "-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    return root


def command(arguments, cwd, data=None, environment=None):
    return subprocess.run(
        arguments, cwd=cwd, env=environment or clean_environment(cwd),
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15,
    ).stdout


@contextmanager
def principal_database(manifest):
    """Synthetic NSS observations, never a substitute for protected deployment."""
    authorized = {manifest["broker_uid"], manifest["coordinator_uid"]}
    users = {
        uid: SimpleNamespace(pw_name=f"fixture-{uid}", pw_uid=uid, pw_gid=uid)
        for uid in (*authorized, *manifest["candidate_uids"])
    }
    supplementary = {
        uid: {manifest["socket_gid"]} if uid in authorized else set() for uid in users
    }
    groups = {
        gid: SimpleNamespace(gr_name=f"fixture-group-{gid}", gr_gid=gid, gr_mem=[])
        for gid in {*users, manifest["socket_gid"]}
    }
    groups[manifest["socket_gid"]].gr_mem = [users[uid].pw_name for uid in authorized]

    def account(name):
        return {user.pw_name: user for user in users.values()}[name]

    with mock.patch.object(pwd, "getpwuid", side_effect=lambda uid: users[uid]), \
         mock.patch.object(pwd, "getpwnam", side_effect=account), \
         mock.patch.object(grp, "getgrgid", side_effect=lambda gid: groups[gid]), \
         mock.patch.object(os, "getgrouplist", side_effect=lambda name, gid: sorted(
             {gid, *supplementary[account(name).pw_uid]},
         )):
        yield SimpleNamespace(users=users, groups=groups, supplementary=supplementary)


def installed_copy(root):
    module_root = root / "scripts" / "workflow_pilot"
    module_root.mkdir(mode=0o755, parents=True)
    for directory in (root, root / "scripts", module_root):
        directory.chmod(0o755)
    for name in broker.INSTALLED_MODULES:
        destination = module_root / name
        shutil.copyfile(Path(broker.__file__).parent / name, destination)
        destination.chmod(0o644)
    return module_root / "git_broker.py"


def poison_bytecode(entry, marker, name="signed_records.py"):
    source = entry.with_name(name)
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir(mode=0o777, exist_ok=True)
    payload = source.read_bytes() + (
        f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('unchecked cache executed')\n"
    ).encode()
    # PEP 552 unchecked-hash caches are consumed even by python -B.
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER + struct.pack("<I", 1) + b"\0" * 8
        + marshal.dumps(compile(payload, str(source), "exec", dont_inherit=True))
    )
    cache.chmod(0o666)
    return cache


class Keys:
    def __init__(self, root):
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        command([
            "/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", "ca.key", "-out", "ca.crt", "-days", "2", "-subj", "/CN=fixture-ca",
        ], root)
        for name, common_name, usage in (
            ("server", "workflow-pilot-git-broker", "serverAuth"),
            ("client", "fixture-coordinator", "clientAuth"),
            ("other", "unapproved-coordinator", "clientAuth"),
        ):
            command([
                "/usr/bin/openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", f"/CN={common_name}",
            ], root)
            extension = root / (name + ".extensions")
            extension.write_text(
                f"subjectAltName=DNS:{common_name}\nextendedKeyUsage={usage}\n",
            )
            command([
                "/usr/bin/openssl", "x509", "-req", "-in", f"{name}.csr",
                "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
                "-out", f"{name}.crt", "-days", "2", "-extfile", str(extension),
            ], root)
        self.signer = self.public("client")
        self.response_key = self.public("server")
        self.client_fingerprint = broker.certificate_fingerprint(root / "client.crt")
        self.server_fingerprint = broker.certificate_fingerprint(root / "server.crt")

    def public(self, name):
        modulus = command([
            "/usr/bin/openssl", "rsa", "-in", f"{name}.key", "-noout", "-modulus",
        ], self.root).decode("ascii").strip().split("=", 1)[1].lower()
        return {"algorithm": "rsa-pkcs1v15-sha256", "modulus_hex": modulus, "exponent": 65537}

    def sign(self, domain, record, name="client"):
        signature = command([
            "/usr/bin/openssl", "dgst", "-sha256", "-sign", f"{name}.key",
        ], self.root, signed_payload(domain, record))
        record["signature"] = base64.b64encode(signature).decode("ascii")
        return record

    def authority_signer(self):
        value = {
            **self.signer, "service_identity": "fixture-external-terminal-signer",
            "isolation_attestation": {
                "kind": "external-isolated-service",
                "private_key_in_implementation_namespace": False,
                "signing_api": "single-use-terminal-attestation",
            },
        }
        value["key_id"] = hashlib.sha256(COORDINATOR_KEY_DOMAIN + canonical_json(value)).hexdigest()
        return value


class Fixture:
    def __init__(self, root, keys):
        self.root, self.keys = root, keys
        self.state, self.source = root / "state", root / "source.git"
        self.remote = self.state / "remote.git"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state.mkdir(mode=0o700)
        for directory in (self.remote, self.source):
            command(["/usr/bin/git", "init", "--bare", "--quiet", "--template=", str(directory)], root)
            directory.chmod(0o700)
        for name in ("receive.denyNonFastForwards", "receive.denyDeletes"):
            self.git(self.remote, "config", name, "true")
        self.policy = protocol.Policy.parse({
            "deployment_id": "d" * 64, "repository": "example/broker-fixture",
            "repository_id": 1, "issue": 205, "endpoint": self.remote.as_uri(),
            "actor_id": 7, "client_certificate_sha256": keys.client_fingerprint,
            "signer": keys.authority_signer(), "ruleset_id": 37,
            "authorized_bypass_actors": [
                {"actor_type": "User", "actor_id": 7, "database_id": 7, "bypass_mode": "always"},
            ],
        })
        self.store = PublicationStore(self.policy, self.state)
        self.current = None

    def close(self):
        self.store.close()

    def git(self, repository, *arguments, data=None):
        environment = clean_environment(self.root)
        environment.update({
            "GIT_AUTHOR_NAME": "Broker fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Broker fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        })
        return command(
            ["/usr/bin/git", f"--git-dir={repository}", *arguments],
            self.root, data, environment,
        )

    def record_commit(self, name, record, parent=None):
        blob = self.git(self.source, "hash-object", "-w", "--stdin", data=canonical_json(record)).strip()
        tree = self.git(
            self.source, "mktree", data=b"100644 blob " + blob + b"\t" + name.encode() + b"\n",
        ).decode().strip()
        arguments = ["commit-tree", tree]
        if parent:
            arguments.extend(["-p", parent])
        return self.git(self.source, *arguments, data=b"Exact publication fixture\n").decode().strip()

    def manifest(self, client=False):
        kind = "client" if client else "server"
        value = {
            "schema_version": 1, "role": kind, "policy": copy.deepcopy(self.policy.__dict__),
            "broker_uid": 65534, "coordinator_uid": 65532, "candidate_uids": [65533],
            "socket": str(self.root / "broker.sock"), "socket_gid": 65532,
            "certificate": str(self.keys.root / (kind + ".crt")),
            "private_key": str(self.keys.root / (kind + ".key")),
            "ca_certificate": str(self.keys.root / "ca.crt"),
            "server_certificate_sha256": self.keys.server_fingerprint,
            "response_public_key": self.keys.response_key,
        }
        if not client:
            value.update({
                "state": str(self.state), "response_private_key": str(self.keys.root / "server.key"),
                "transport": {"kind": "local"},
            })
        return value

    def make_plan(self, operation=None, *, mutation=None):
        previous = self.current
        operation = operation or ("bootstrap" if previous is None else "advance")
        nonce, issued = os.urandom(32).hex(), utc_now()
        sequence = 0 if previous is None else previous[0]["sequence"] + 1
        old_authority, old_anchor = (None, None) if previous is None else previous[2:4]
        event = dict.fromkeys((
            "handoff_seal", "handoff_id", "handoff_kind", "lifecycle_state", "candidate_sha",
            "closed_at", "operation_nonce", "consume_store_id", "consume_sequence",
            "consume_anchor", "assignment", "interruption_snapshot", "history_receipt", "history_carrier",
        ))
        event["kind"] = "genesis"
        authority = {
            "schema_version": 2, "repository": self.policy.repository, "issue": self.policy.issue,
            "sequence": sequence, "handoff_sequence": 0, "head_seal": None, "pr_binding": None,
            "signer": copy.deepcopy(self.policy.signer), "ruleset_id": self.policy.ruleset_id,
            "authorized_bypass_actors": copy.deepcopy(self.policy.authorized_bypass_actors),
            "delivery_expectation": {
                "repository_id": self.policy.repository_id, "repository_full_name": self.policy.repository,
                "immediate_base_branch": "master", "immediate_base_oid": "a" * 40,
                "delivery_branch": "delivery/issue-205", "head_repository_full_name": self.policy.repository,
            },
            "event": event, "previous_object_id": old_authority,
        }
        if previous is not None:
            for name in (
                "handoff_sequence", "head_seal", "pr_binding", "signer",
                "ruleset_id", "authorized_bypass_actors", "delivery_expectation",
            ):
                authority[name] = copy.deepcopy(previous[0][name])
        attestation = {
            "source": "external-coordinator-service", "repository": self.policy.repository,
            "repository_id": self.policy.repository_id, "ruleset_source": "github-rulesets-api",
            "issue": self.policy.issue, "authority_object_id": old_authority,
            "anchor_object_id": old_anchor, "operation_nonce": nonce, "operation": operation,
            "new_head_seal": None, "history_carrier_digest": None, "history_receipt_digest": None,
            "pull_request_observation_digest": None, "binding_expectation": None,
            "observed_at": format_utc(issued), "coordinator_database_id": self.policy.actor_id,
            "ruleset_response": {
                "id": self.policy.ruleset_id, "enforcement": "active", "target": "branch",
                "include_refs": list(self.policy.refs), "exclude_refs": [],
                "update_restricted": True, "non_fast_forward_restricted": True,
                "deletion_restricted": True, "bypass_actors": copy.deepcopy(self.policy.authorized_bypass_actors),
            },
        }
        if operation == "advance":
            authority["handoff_sequence"] += 1
            authority["head_seal"] = hashlib.sha256(nonce.encode()).hexdigest()
            event.update({
                "kind": "handoff", "handoff_seal": authority["head_seal"], "candidate_sha": "b" * 40,
                "history_receipt": {"seal": authority["head_seal"], "sequence": authority["handoff_sequence"]},
                "history_carrier": {"fixture": "transport only; #178 validates the actual complete carrier"},
            })
            attestation["new_head_seal"] = authority["head_seal"]
            for name in ("history_carrier", "history_receipt"):
                attestation[name + "_digest"] = hashlib.sha256(canonical_json(event[name])).hexdigest()
        if operation == "bind":
            event["kind"] = "pr_binding"
            observation = {
                "source": "github-pull-request-api", "repository_id": self.policy.repository_id,
                "repository_full_name": self.policy.repository, "head_repository_full_name": self.policy.repository,
                "pull_request": 191, "state": "OPEN", "merged": False,
                "base_branch": "master", "head_branch": "delivery/issue-205",
                "base_oid": "a" * 40, "head_oid": "b" * 40,
                "coordinator_database_id": self.policy.actor_id,
                "authority_object_id": old_authority, "anchor_object_id": old_anchor,
                "created_at": format_utc(issued), "observed_at": format_utc(issued),
                "expected_handoff_branch": "delivery/issue-205", "delivery_branch": "delivery/issue-205",
            }
            self.keys.sign(b"workflow-pilot-github-pr-observation-v1\0", observation)
            authority["pr_binding"] = observation
            attestation["pull_request_observation_digest"] = hashlib.sha256(canonical_json(observation)).hexdigest()
            attestation["binding_expectation"] = {
                "repository_id": self.policy.repository_id, "repository_full_name": self.policy.repository,
                "pull_request": 191, "state": "OPEN", "merged": False, "base_branch": "master",
                "frozen_base_oid": "a" * 40, "current_base_oid": "a" * 40,
                "head_branch": "delivery/issue-205", "head_repository_full_name": self.policy.repository,
                "head_oid": "b" * 40, "coordinator_database_id": self.policy.actor_id,
            }
        self.keys.sign(protocol.ATTESTATION_DOMAIN, attestation)
        authority["publication_attestation"] = attestation
        anchor = {
            "schema_version": 1, "repository": self.policy.repository, "issue": self.policy.issue,
            "sequence": sequence, "authority_object_id": None, "previous_object_id": old_anchor,
        }
        if mutation:
            mutation(authority, anchor)
        authority_oid = self.record_commit("authority.json", authority, old_authority)
        if anchor["authority_object_id"] is None:
            anchor["authority_object_id"] = authority_oid
        anchor_oid = self.record_commit("anchor.json", anchor, old_anchor)
        objects = sorted(set(self.git(
            self.source, "rev-list", "--objects", "--no-object-names", authority_oid, anchor_oid,
        ).decode().splitlines()))
        pack = self.git(
            self.source, "pack-objects", "--stdout", "--no-reuse-delta", "--no-reuse-object", "--window=0",
            data=("\n".join(objects) + "\n").encode(),
        )
        plan = protocol.unsigned_plan(
            self.policy, operation=operation, sequence=sequence, nonce=nonce,
            issued_at=format_utc(issued), expires_at=format_utc(issued + timedelta(seconds=30)),
            old_authority=old_authority, old_anchor=old_anchor,
            new_authority=authority_oid, new_anchor=anchor_oid, pack=pack, objects=objects,
        )
        self.keys.sign(protocol.PLAN_DOMAIN, plan)
        return plan, pack, (authority, anchor, authority_oid, anchor_oid)

    def publish(self, plan, pack):
        self.store.reserve(plan, self.policy.client_certificate_sha256)
        return self.store.publish_reserved(plan, pack, utc_now() + timedelta(seconds=30))

    def bootstrap(self):
        plan, pack, current = self.make_plan()
        result = self.publish(plan, pack)
        if result[0] != "published":
            raise AssertionError(f"fixture bootstrap failed: {result[0]}")
        self.current = current
        return plan
