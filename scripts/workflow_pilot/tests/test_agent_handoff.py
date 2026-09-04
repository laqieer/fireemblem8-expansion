import base64
import copy
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.workflow_pilot import agent_handoff, raw_diff_check, reporter
from scripts.workflow_pilot.tests import test_reporter

ROOT = Path(__file__).resolve().parents[3]
TEST_ARTIFACTS = ROOT / "build" / "test-artifacts"
TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
AUTHORITY_OWNERS = {}
COORDINATOR_INSTALLATIONS = {}
AUTHORIZED_COORDINATORS = {}
SIGNER_SERVICES = {}
SIGNER_CONSUME_STATES = {}
SYNTHETIC_AUTHORITY_ADVANCES = 0
def load_handoff_schema():
    return json.loads(
        (
            ROOT / agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH
        ).read_text(encoding="utf-8")
    )
def validator_for_schema(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())
def schema_ref(schema, ref):
    return {
        "$schema": schema["$schema"],
        "$defs": copy.deepcopy(schema["$defs"]),
        "$ref": ref,
    }
def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def assert_schema_runtime_rejects(
    testcase,
    *,
    validator,
    document,
    repository_root,
    runtime_error,
):
    with testcase.assertRaises(ValidationError):
        validator.validate(document)
    with testcase.assertRaisesRegex(agent_handoff.HandoffDataError, runtime_error):
        agent_handoff.validate_document(document, repository_root)

SIGNER_SERVICE = r"""
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

private_path = sys.argv[1]
modulus = subprocess.run(
    ["openssl", "rsa", "-in", private_path, "-noout", "-modulus"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip().split("=", 1)[1].lower()
with open(private_path, "rb") as stream:
    private_bytes = stream.read()
private_fd = os.memfd_create("workflow-pilot-external-signer", flags=0)
os.write(private_fd, private_bytes)
os.lseek(private_fd, 0, os.SEEK_SET)
print(json.dumps({"modulus_hex": modulus, "exponent": 65537}), flush=True)
spent = set()
consumed = {}
finalized = set()
store_sequence = 0
store_anchor = "0" * 64
for line in sys.stdin:
    request = json.loads(line)
    payload = base64.b64decode(request["payload"])
    mode = request["mode"]
    if mode == "consume":
        nonce = request["nonce"]
        sequence = request["sequence"]
        previous_anchor = request["previous_anchor"]
        expected_anchor = request["anchor"]
        if nonce in spent or sequence != store_sequence + 1 or previous_anchor != store_anchor:
            print(json.dumps({"error": "nonce-spent-or-nonmonotonic"}), flush=True)
            continue
        actual_anchor = __import__("hashlib").sha256(
            (
                previous_anchor
                + ":"
                + str(sequence)
                + ":"
                + nonce
            ).encode()
        ).hexdigest()
        if expected_anchor != actual_anchor:
            print(json.dumps({"error": "anchor-mismatch"}), flush=True)
            continue
        document = json.loads(payload.split(b"\0", 1)[1])
        receipt = document["coordinator_receipt"]
        operation = receipt["operation"]
        if (
            operation["nonce"] != nonce
            or operation["consume_store_id"] != "test-external-monotonic-store"
            or operation["consume_sequence"] != sequence
            or operation["consume_previous_anchor"] != previous_anchor
            or operation["consume_anchor"] != actual_anchor
            or not operation["implementation_terminated"]
            or not operation["single_use"]
            or receipt["issued_at"] != operation["collected_through"]
            or operation["collected_through"] != operation["eligibility_instant"]
            or receipt["remote_coverage"]["interval_end"]
            != operation["eligibility_instant"]
            or any(
                source["observed_at"] != operation["eligibility_instant"]
                for source in receipt["remote_coverage"]["sources"]
            )
        ):
            print(json.dumps({"error": "terminal-consume-contract"}), flush=True)
            continue
        consume_time = datetime.fromisoformat(
            operation["eligibility_instant"].replace("Z", "+00:00")
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if consume_time > now or (now - consume_time).total_seconds() > 2:
            print(json.dumps({"error": "consume-time-not-current"}), flush=True)
            continue
        spent.add(nonce)
        consumed[nonce] = (sequence, actual_anchor)
        store_sequence = sequence
        store_anchor = actual_anchor
    elif mode == "finalize":
        nonce = request["nonce"]
        if nonce not in spent or nonce in finalized or request["anchor"] != store_anchor:
            print(json.dumps({"error": "result-not-consumable"}), flush=True)
            continue
        finalized.add(nonce)
    signature = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            f"/proc/self/fd/{private_fd}",
        ],
        input=payload,
        check=True,
        capture_output=True,
        pass_fds=(private_fd,),
    ).stdout
    print(
        json.dumps(
            {
                "signature": base64.b64encode(signature).decode("ascii"),
                "sequence": store_sequence,
                "anchor": store_anchor,
            }
        ),
        flush=True,
    )
"""
def git(repository_root, *arguments):
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
def git_with_input(repository_root, arguments, value, environment=None):
    git_environment = reporter.git_environment(offline=True)
    if environment is not None:
        git_environment.update(environment)
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        cwd=repository_root,
        env=git_environment,
        input=value,
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()
def signer_request(repository_root, request):
    service = SIGNER_SERVICES[str(repository_root)]
    service.stdin.write(json.dumps(request) + "\n")
    service.stdin.flush()
    response = json.loads(service.stdout.readline())
    if "error" in response:
        raise ValueError(response["error"])
    return response
def external_sign(repository_root, payload):
    return signer_request(
        repository_root,
        {
            "mode": "sign",
            "payload": base64.b64encode(payload).decode("ascii"),
        },
    )["signature"]
def consume_sign(repository_root, document):
    state = SIGNER_CONSUME_STATES[str(repository_root)]
    operation = document["coordinator_receipt"]["operation"]
    sequence = state["sequence"] + 1
    previous_anchor = state["anchor"]
    anchor = hashlib.sha256(
        (
            previous_anchor
            + ":"
            + str(sequence)
            + ":"
            + operation["nonce"]
        ).encode()
    ).hexdigest()
    operation["consume_store_id"] = state["store_id"]
    operation["consume_sequence"] = sequence
    operation["consume_previous_anchor"] = previous_anchor
    operation["consume_anchor"] = anchor
    payload = agent_handoff.coordinator_attestation_payload(document)
    response = signer_request(
        repository_root,
        {
            "mode": "consume",
            "payload": base64.b64encode(payload).decode("ascii"),
            "nonce": operation["nonce"],
            "sequence": sequence,
            "previous_anchor": previous_anchor,
            "anchor": anchor,
        },
    )
    state["sequence"] = response["sequence"]
    state["anchor"] = response["anchor"]
    document["coordinator_receipt"]["signature"] = response["signature"]
def finalize_result_attestation(repository_root, document, result):
    operation = document["coordinator_receipt"]["operation"]
    payload = agent_handoff.result_attestation_payload(document, result)
    response = signer_request(
        repository_root,
        {
            "mode": "finalize",
            "payload": base64.b64encode(payload).decode("ascii"),
            "nonce": operation["nonce"],
            "anchor": operation["consume_anchor"],
        },
    )
    return {
        "signer_key_id": document["history_authority"]["signer"]["key_id"],
        "operation_nonce": operation["nonce"],
        "consume_store_id": operation["consume_store_id"],
        "consume_sequence": operation["consume_sequence"],
        "consume_anchor": operation["consume_anchor"],
        "signature": response["signature"],
    }

REPORTER_TRUST = {}
REPORTER_INSTALLATIONS = {}
def trusted_reporter_installation(repository_root):
    key = str(repository_root)
    if key not in REPORTER_INSTALLATIONS:
        root = Path(tempfile.mkdtemp(prefix="workflow-pilot-offline-install-", dir=TEST_ARTIFACTS))
        installation = root / "installation"
        verifier_root = root / "verifier"
        shutil.copytree(installation_root_path(repository_root), installation)
        manifest_path = installation / "installation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["bootstrap_validator"]["path"] = "raw_diff_check.py"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verifier_root.mkdir()
        git(verifier_root, "init", "-q", "-b", "master")
        git(verifier_root, "config", "user.name", "Offline Verifier")
        git(verifier_root, "config", "user.email", "offline@example.invalid")
        git(verifier_root, "remote", "add", "origin", "https://github.com/example/offline-verifier.git")
        REPORTER_INSTALLATIONS[key] = (installation, verifier_root)
    return REPORTER_INSTALLATIONS[key]
def sign_reporter_trust_anchor(repository_root, anchor):
    anchor["signature"] = external_sign(repository_root, agent_handoff.reporter_trust_anchor_payload(anchor))
    return anchor
def trusted_reporter_anchor(
    repository_root, document, input_seal, *, authority=None, signer=None,
    issued_at=None, expires_at=None, signing_root=None,
):
    handoff = document["handoffs"][0]
    authority = copy.deepcopy(
        authority
        if authority is not None
        else agent_handoff.read_history_authority(
            repository_root,
            document["repository"],
            handoff["issue"],
            handoff["pull_request"],
        )
    )
    issued_at = issued_at or max(
        datetime.fromisoformat(state["at"].replace("Z", "+00:00"))
        for item in document["handoffs"]
        for state in item["states"]
    )
    expires_at = expires_at or (issued_at + timedelta(days=1))
    return sign_reporter_trust_anchor(signing_root or repository_root, {
        "input_seal": input_seal,
        "authority_digest": hashlib.sha256(agent_handoff.normalized_json(authority)).hexdigest(),
        "repository": authority["repository"],
        "ref": authority["ref"],
        "anchor_ref": authority["anchor_ref"],
        "signer": copy.deepcopy(signer or authority["signer"]),
        "issued_at": iso_utc(issued_at),
        "expires_at": iso_utc(expires_at),
    })
def remember_reporter_trust(record, trusted_anchor, trusted_installation):
    installation_path, verifier_root = trusted_installation
    REPORTER_TRUST[record["input_seal"]] = (copy.deepcopy(trusted_anchor), installation_path, verifier_root)
    return record
def reporter_record(repository_root, document, result):
    trusted_installation = trusted_reporter_installation(repository_root)
    trusted_anchor = trusted_reporter_anchor(repository_root, document, result["input_seal"])
    return remember_reporter_trust(
        agent_handoff.reporter_record(
            document,
            result,
            finalize_result_attestation(repository_root, document, result),
            repository_root=trusted_installation[1],
            trusted_anchor=trusted_anchor,
            trusted_installation=trusted_installation[0],
        ),
        trusted_anchor,
        trusted_installation,
    )
def validated_record(repository_root, document):
    return reporter_record(repository_root, document, agent_handoff.validate_document(document, repository_root))
def reporter_fixture_trust(*bundles):
    return {
        "schema_version": 1,
        "anchors": [copy.deepcopy(REPORTER_TRUST[bundle["input_seal"]][0]) for bundle in bundles],
    }
def reporter_fixture_installation(*bundles):
    return REPORTER_TRUST[bundles[0]["input_seal"]][1]
def reporter_fixture_repository_root(*bundles):
    return REPORTER_TRUST[bundles[0]["input_seal"]][2]
def validate_reporter_fixture(
    fixture, repository_root=None, implementation_handoff_trust=None, implementation_handoff_installation=None,
):
    if fixture.get("schema_version") == reporter.HANDOFF_FIXTURE_SCHEMA_VERSION:
        implementation_handoff_trust = (
            reporter_fixture_trust(*fixture["implementation_handoffs"])
            if implementation_handoff_trust is None
            else implementation_handoff_trust
        )
        implementation_handoff_installation = (
            reporter_fixture_installation(*fixture["implementation_handoffs"])
            if implementation_handoff_installation is None
            else implementation_handoff_installation
        )
        repository_root = reporter_fixture_repository_root(*fixture["implementation_handoffs"]) if repository_root is None else repository_root
    return reporter.validate_fixture(fixture, repository_root=repository_root, implementation_handoff_trust=implementation_handoff_trust, implementation_handoff_installation=implementation_handoff_installation)
def verify_reporter_record_offline(
    record, repository_root=None, trusted_anchor=None, trusted_installation=None, current_time=None,
):
    if repository_root is None and record["input_seal"] in REPORTER_TRUST:
        repository_root = REPORTER_TRUST[record["input_seal"]][2]
    if trusted_anchor is None or trusted_installation is None:
        stored_anchor, stored_installation, stored_root = REPORTER_TRUST[record["input_seal"]]
        trusted_anchor = copy.deepcopy(stored_anchor if trusted_anchor is None else trusted_anchor)
        trusted_installation = stored_installation if trusted_installation is None else trusted_installation
        repository_root = stored_root if repository_root is None else repository_root
    return agent_handoff.verify_reporter_record(record, revalidate_git=False, repository_root=repository_root, trusted_anchor=trusted_anchor, trusted_installation=trusted_installation, current_time=current_time)
def installation_root_path(repository_root):
    return COORDINATOR_INSTALLATIONS[str(repository_root)]
def installation_manifest(repository_root):
    return json.loads(
        (installation_root_path(repository_root) / "installation.json").read_text(
            encoding="utf-8"
        )
    )
def installation_authorized_coordinators(repository_root):
    return copy.deepcopy(AUTHORIZED_COORDINATORS[str(repository_root)])
def installation_authorized_non_user_bypass_actors(repository_root):
    return copy.deepcopy(
        installation_manifest(repository_root)[
            "authorized_non_user_bypass_actors"
        ]
    )
def primary_coordinator_database_id(repository_root):
    return installation_authorized_coordinators(repository_root)[0]["database_id"]
def signer_public_with_key_id(signer_public):
    signed = {
        key: value for key, value in signer_public.items() if key != "key_id"
    }
    refreshed = copy.deepcopy(signer_public)
    refreshed["key_id"] = hashlib.sha256(
        agent_handoff.COORDINATOR_RECEIPT_SEAL_DOMAIN
        + agent_handoff.normalized_json(signed)
    ).hexdigest()
    return refreshed
BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
def noncanonical_base64_alias(value):
    if value.endswith("=="):
        index = BASE64_ALPHABET.index(value[-3])
        return value[:-3] + BASE64_ALPHABET[index | 0x01] + "=="
    if value.endswith("="):
        index = BASE64_ALPHABET.index(value[-2])
        return value[:-2] + BASE64_ALPHABET[index | 0x01] + "="
    raise AssertionError("expected standard padded base64")
def signature_plus_modulus_alias(repository_root, payload):
    signer = installation_manifest(repository_root)["signer_public"]
    modulus = int(signer["modulus_hex"], 16)
    size = (modulus.bit_length() + 7) // 8
    ceiling = 1 << (size * 8)
    for suffix in range(256):
        candidate_payload = payload + bytes([suffix])
        signature_text = external_sign(repository_root, candidate_payload)
        signature = base64.b64decode(signature_text, validate=True)
        signature_value = int.from_bytes(signature, "big")
        mutated_value = signature_value + modulus
        if mutated_value < ceiling:
            return (
                signer,
                candidate_payload,
                signature_text,
                base64.b64encode(
                    mutated_value.to_bytes(size, "big")
                ).decode("ascii"),
            )
    raise AssertionError("could not find a same-width signature alias")
def ruleset_response(issue=178, repository_root=None):
    if repository_root is None:
        authorized = [{"login": "coordinator", "database_id": 9001}]
        non_user_bypass = []
    else:
        authorized = installation_authorized_coordinators(repository_root)
        non_user_bypass = installation_authorized_non_user_bypass_actors(
            repository_root
        )
    return {
        "id": 77,
        "enforcement": "active",
        "target": "branch",
        "include_refs": [
            agent_handoff.history_authority_ref(issue, None),
            agent_handoff.history_anchor_ref(issue),
        ],
        "exclude_refs": [],
        "update_restricted": True,
        "non_fast_forward_restricted": True,
        "deletion_restricted": True,
        "bypass_actors": [
            {
                "actor_type": "User",
                "actor_id": actor["database_id"],
                "database_id": actor["database_id"],
                "bypass_mode": "always",
            }
            for actor in authorized
        ]
        + copy.deepcopy(non_user_bypass),
    }
def install_stalling_transport(repository_root):
    stall_script = repository_root.parent / "stalling-ssh.sh"
    invocation_log = repository_root.parent / "stalling-ssh.log"
    child_pid_path = repository_root.parent / "stalling-ssh-child.pid"
    stall_script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{invocation_log}'\n"
        "(sleep 30) &\n"
        "child=$!\n"
        f"printf '%s' \"$child\" > '{child_pid_path}'\n"
        "wait \"$child\"\n",
        encoding="utf-8",
    )
    stall_script.chmod(0o700)
    remote_url = "ssh://git@github.com/example/workflow.git"
    manifest_path = COORDINATOR_INSTALLATIONS[str(repository_root)] / "installation.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["authority_protection"]["remote_url"] = remote_url
    manifest_path.write_text(json.dumps(manifest))
    git(
        repository_root,
        "remote",
        "set-url",
        "origin",
        remote_url,
    )
    return invocation_log, child_pid_path, stall_script
def wait_for_pid_exit(pid_path, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw_pid = pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if not raw_pid:
            time.sleep(0.05)
            continue
        if not Path(f"/proc/{raw_pid}").exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"transport child {pid_path} did not exit in time")
def hanging_check_spec(pid_path):
    return (
        ["/usr/bin/python3", "-I", "-"],
        (
            "import pathlib,subprocess\n"
            "child=subprocess.Popen(['/bin/sh','-c','sleep 30'])\n"
            f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid),encoding='utf-8')\n"
            "child.wait()\n"
        ).encode("utf-8"),
        {"mode": "external-bootstrap", "sha256": "a" * 64},
    )
def publication_attestation(
    repository_root,
    authority_object_id,
    anchor_object_id,
    *,
    issue=178,
    coordinator_database_id=None,
    operation=None,
    new_head_seal=None,
    history_carrier=None,
    history_receipt=None,
    pr_observation=None,
    binding_expectation=None,
    observed_at=None,
):
    if coordinator_database_id is None:
        coordinator_database_id = primary_coordinator_database_id(
            repository_root
        )
    if operation is None:
        operation = (
            "bootstrap" if authority_object_id is None else "advance"
        )
    if observed_at is None:
        observed_at = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "source": "external-coordinator-service",
        "repository": "example/workflow",
        "repository_id": 7001,
        "ruleset_source": "github-rulesets-api",
        "issue": issue,
        "authority_object_id": authority_object_id,
        "anchor_object_id": anchor_object_id,
        "operation_nonce": hashlib.sha256(
            f"publication:{issue}:{authority_object_id}:{anchor_object_id}".encode()
        ).hexdigest(),
        "operation": operation,
        "new_head_seal": new_head_seal,
        "history_carrier_digest": agent_handoff.publication_history_carrier_digest(
            history_carrier
        ),
        "history_receipt_digest": agent_handoff.publication_history_receipt_digest(
            history_receipt
        ),
        "pull_request_observation_digest": (
            hashlib.sha256(
                agent_handoff.normalized_json(pr_observation)
            ).hexdigest()
            if pr_observation is not None
            else None
        ),
        "binding_expectation": binding_expectation,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "coordinator_database_id": coordinator_database_id,
        "ruleset_response": ruleset_response(
            issue,
            repository_root=repository_root,
        ),
    }
    record["signature"] = external_sign(
        repository_root,
        agent_handoff.signed_record_payload(
            agent_handoff.PUBLICATION_ATTESTATION_DOMAIN,
            record,
        ),
    )
    return record
def pull_request_observation(
    repository_root,
    authority,
    *,
    pull_request=200,
    base_branch="master",
    head_branch=None,
    coordinator_database_id=None,
):
    if head_branch is None:
        head_branch = authority["delivery_expectation"]["delivery_branch"]
    if coordinator_database_id is None:
        coordinator_database_id = primary_coordinator_database_id(
            repository_root
        )
    observed_at = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "source": "github-pull-request-api",
        "repository_id": 7001,
        "repository_full_name": "example/workflow",
        "pull_request": pull_request,
        "state": "OPEN",
        "merged": False,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "head_repository_full_name": "example/workflow",
        "base_oid": git(
            repository_root,
            "rev-parse",
            f"refs/heads/{base_branch}",
        ),
        "head_oid": git(
            repository_root,
            "rev-parse",
            f"refs/heads/{head_branch}",
        ),
        "created_at": (observed_at - timedelta(minutes=1))
        .isoformat()
        .replace("+00:00", "Z"),
        "coordinator_database_id": coordinator_database_id,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "authority_object_id": authority["object_id"],
        "anchor_object_id": authority["anchor_object_id"],
        "expected_handoff_branch": head_branch,
        "delivery_branch": head_branch,
    }
    record["signature"] = external_sign(
        repository_root,
        agent_handoff.signed_record_payload(
            agent_handoff.PR_OBSERVATION_DOMAIN,
            record,
        ),
    )
    return record
def frozen_binding_expectation(
    authority,
    pull_request=200,
    *,
    current_base_oid=None,
    coordinator_database_id=None,
):
    delivery = authority["delivery_expectation"]
    if current_base_oid is None:
        current_base_oid = delivery["immediate_base_oid"]
    if coordinator_database_id is None:
        coordinator_database_id = (
            authority["pr_binding"]["coordinator_database_id"]
            if authority["pr_binding"] is not None
            else next(
                item["database_id"]
                for item in authority["authorized_bypass_actors"]
                if item["actor_type"] == "User"
            )
        )
    return agent_handoff.publication_binding_expectation(
        delivery_expectation=delivery,
        pull_request=pull_request,
        head_branch=authority["history_events"][0]["assignment"][
            "expected_branch"
        ],
        head_oid=authority["history_events"][-1]["candidate_sha"],
        coordinator_database_id=coordinator_database_id,
        current_base_oid=current_base_oid,
    )
def authority_publication(
    repository_root,
    current,
    *,
    issue=178,
    operation,
    history_carrier=None,
    history_receipt=None,
    pr_observation=None,
    current_base_oid=None,
    observed_at=None,
):
    coordinator_database_id = (
        None
        if pr_observation is None
        else pr_observation["coordinator_database_id"]
    )
    binding_expectation = (
        None
        if pr_observation is None
        else frozen_binding_expectation(
            current,
            pr_observation["pull_request"],
            current_base_oid=(
                pr_observation["base_oid"]
                if current_base_oid is None
                else current_base_oid
            ),
            coordinator_database_id=coordinator_database_id,
    )
    )
    return publication_attestation(
        repository_root, current["object_id"], current["anchor_object_id"],
        issue=issue, coordinator_database_id=coordinator_database_id,
        operation=operation,
        new_head_seal=None if history_receipt is None else history_receipt["seal"],
        history_carrier=history_carrier, history_receipt=history_receipt,
        pr_observation=pr_observation,
        binding_expectation=binding_expectation,
        observed_at=observed_at,
    )
def sign_coordinator_document(document, repository_root):
    document["coordinator_receipt"].pop("signature", None)
    operation = document["coordinator_receipt"]["operation"]
    operation["nonce"] = secrets.token_hex(32)
    for field in (
        "consume_store_id",
        "consume_sequence",
        "consume_previous_anchor",
        "consume_anchor",
    ):
        operation.pop(field, None)
    consume_sign(repository_root, document)
def reseal_history_authority_observation(authority):
    observation = authority["observation"]
    payload = {
        field: observation[field]
        for field in (
            "remote",
            "ref",
            "object_id",
            "anchor_ref",
            "anchor_object_id",
            "attempt",
        )
    }
    observation["token"] = hashlib.sha256(
        agent_handoff.HISTORY_OBSERVATION_SEAL_DOMAIN
        + agent_handoff.normalized_json(payload)
    ).hexdigest()
def reseal_handoff_result(document, result):
    resealed = copy.deepcopy(result)
    resealed["input_seal"] = hashlib.sha256(
        agent_handoff.INPUT_SEAL_DOMAIN
        + agent_handoff.normalized_json(document)
    ).hexdigest()
    resealed["result_seal"] = agent_handoff.seal_handoff_result(resealed)
    return resealed
def publish_self_consistent_history_carrier(
    repository_root,
    current,
    plan,
    document,
    result,
):
    handoff_id = document["handoffs"][0]["id"]
    history_receipt = agent_handoff.make_history_receipt(
        document,
        result,
        handoff_id,
        canonical_result=result,
    )
    history_carrier = agent_handoff.make_history_carrier(
        document,
        result,
        handoff_id,
    )
    plan["record"]["head_seal"] = history_receipt["seal"]
    plan["record"]["event"].update(
        handoff_seal=history_receipt["seal"],
        handoff_id=history_receipt["handoff_id"],
        handoff_kind=history_receipt["handoff_kind"],
        lifecycle_state=history_receipt["lifecycle_state"],
        candidate_sha=history_receipt["candidate_sha"],
        closed_at=history_receipt["closed_at"],
        operation_nonce=history_receipt["operation_nonce"],
        consume_store_id=history_receipt["consume_store_id"],
        consume_sequence=history_receipt["consume_sequence"],
        consume_anchor=history_receipt["consume_anchor"],
        assignment=copy.deepcopy(history_receipt["assignment"]),
        interruption_snapshot=copy.deepcopy(
            history_receipt["interruption_snapshot"]
        ),
        history_receipt=copy.deepcopy(history_receipt),
        history_carrier=copy.deepcopy(history_carrier),
    )
    plan["record"]["publication_attestation"] = authority_publication(
        repository_root,
        current,
        operation="advance",
        history_carrier=history_carrier,
        history_receipt=history_receipt,
    )
    publish_authority_plan(
        repository_root,
        AUTHORITY_OWNERS[str(repository_root)],
        plan,
        current["object_id"],
        issue=document["handoffs"][0]["issue"],
        pull_request=document["handoffs"][0]["pull_request"],
        read_back=False,
    )
    return history_receipt, history_carrier
def rename_handoff(document, handoff_id, *, owner=None):
    handoff = document["handoffs"][0]
    handoff["id"] = handoff_id
    if owner is not None:
        handoff["owner_id"], handoff["owner_database_id"] = owner
    document["delivery_graph"]["relationships"][0]["handoff_id"] = handoff_id
    next(
        task
        for task in document["delivery_graph"]["tasks"]
        if task["phase"] == "implementation"
    )["handoff_id"] = handoff_id
def configure_review_successor(
    document,
    history,
    *,
    handoff_id,
    owner=("owner-2", 102),
    pull_request=200,
):
    document["prior_handoffs"] = [history]
    handoff = document["handoffs"][0]
    handoff.update(
        id=handoff_id,
        owner_id=owner[0],
        owner_database_id=owner[1],
        pull_request=pull_request,
        handoff_kind="review_successor",
        replaces_handoff_id=history["handoff_id"],
    )
    document["delivery_graph"]["relationships"][0]["handoff_id"] = handoff_id
    task = next(
        item
        for item in document["delivery_graph"]["tasks"]
        if item["phase"] == "implementation"
    )
    task["handoff_id"], task["pull_request"] = handoff_id, pull_request
    return handoff
def owner_write_blob_ref(owner_root, reference, payload):
    object_id = git_with_input(
        owner_root,
        ("hash-object", "-w", "--stdin"),
        agent_handoff.normalized_json(payload),
    )
    git(owner_root, "push", "-q", "origin", f"{object_id}:{reference}")
    return object_id
def owner_create_record_commit(
    owner_root,
    record,
    filename,
    parent=None,
    message=b"workflow-pilot handoff authority\n",
):
    blob = git_with_input(
        owner_root,
        ("hash-object", "-w", "--stdin"),
        agent_handoff.normalized_json(record),
    )
    tree = git_with_input(
        owner_root,
        ("mktree",),
        f"100644 blob {blob}\t{filename}\n".encode("ascii"),
    )
    arguments = ["commit-tree", tree]
    if parent is not None:
        arguments.extend(("-p", parent))
    return git_with_input(
        owner_root,
        tuple(arguments),
        message,
        {
            "GIT_AUTHOR_NAME": "Authority Owner",
            "GIT_AUTHOR_EMAIL": "owner@example.invalid",
            "GIT_COMMITTER_NAME": "Authority Owner",
            "GIT_COMMITTER_EMAIL": "owner@example.invalid",
            "GIT_AUTHOR_DATE": "2026-08-31T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-31T00:00:00Z",
        },
    )
def publish_authority_plan(
    repository_root,
    owner_root,
    plan,
    parent,
    *,
    issue,
    pull_request,
    read_back=True,
):
    object_id = owner_create_record_commit(
        owner_root, plan["record"], "authority.json", parent
    )
    anchor_record = copy.deepcopy(plan["anchor_record_template"])
    anchor_record["authority_object_id"] = object_id
    anchor_object_id = owner_create_record_commit(
        owner_root, anchor_record, "anchor.json", plan["expected_anchor_object_id"]
    )
    agent_handoff.publish_authority_updates(
        owner_root,
        installation_root_path(repository_root),
        [(object_id, plan["ref"]), (anchor_object_id, plan["anchor_ref"])],
    )
    if not read_back:
        return object_id, anchor_object_id
    return agent_handoff.read_history_authority(
        repository_root, "example/workflow", issue, pull_request
    )
def protected_root_authority(
    repository_root,
    parent,
    result,
    *,
    with_history=False,
    with_bundle=False,
):
    document = handoff_document(repository_root, parent, result)
    report = agent_handoff.validate_document(document, repository_root)
    bundle = reporter_record(repository_root, document, report) if with_bundle else None
    history = (
        agent_handoff.make_history_receipt(document, report, "issue-178-round-1")
        if with_history else None
    )
    set_history_authority(repository_root, 1, document=document, result=report)
    current = agent_handoff.read_history_authority(repository_root, "example/workflow", 178, None)
    return document, report, current, history, bundle
def plan_advance_authority(
    repository_root,
    document,
    result,
    *,
    issue=None,
    pull_request=None,
    handoff_id="issue-178-round-1",
    current_time=None,
):
    if current_time is None:
        current_time = datetime.fromisoformat(
            document["coordinator_receipt"]["issued_at"].replace("Z", "+00:00")
        )
    history = agent_handoff.make_history_receipt(
        document,
        result,
        handoff_id,
        current_time=current_time,
    )
    history_carrier = agent_handoff.make_history_carrier(
        document,
        result,
        handoff_id,
    )
    issue = history["issue"] if issue is None else issue
    pull_request = history["pull_request"] if pull_request is None else pull_request
    current = agent_handoff.read_history_authority(repository_root, "example/workflow", issue, pull_request)
    return current, history, agent_handoff.plan_history_authority(
        repository_root,
        "example/workflow",
        issue,
        pull_request,
        operation="advance",
        expected_object_id=current["object_id"],
        expected_sequence=current["sequence"],
        handoff_document=document,
        handoff_result=result,
        handoff_id=handoff_id,
        current_time=current_time,
        publication_attestation=authority_publication(
            repository_root,
            current,
            issue=issue,
            operation="advance",
            history_carrier=history_carrier,
            history_receipt=history,
            observed_at=current_time,
        ),
    )
def advance_history_authority(repository_root, *, issue=178):
    global SYNTHETIC_AUTHORITY_ADVANCES
    current = agent_handoff.read_history_authority(
        repository_root,
        "example/workflow",
        issue,
        None,
    )
    if current["handoff_sequence"] == 0:
        parent_sha = git(repository_root, "rev-parse", "HEAD^")
        result_sha = git(repository_root, "rev-parse", "HEAD")
        handoff_id = f"issue-{issue}-round-1"
        document = handoff_document(
            repository_root,
            parent_sha,
            result_sha,
            issue=issue,
            handoff_id=handoff_id,
        )
        shift_handoff_times(document, -120)
        refresh_coordinator_receipt(document, repository_root)
    else:
        prior_closed_at = datetime.fromisoformat(
            current["history_events"][-1]["closed_at"].replace("Z", "+00:00")
        )
        if (
            current["pr_binding"] is None
            and datetime.now(timezone.utc).replace(microsecond=0)
            <= prior_closed_at + timedelta(seconds=30)
        ):
            return bind_history_authority(
                repository_root,
                issue=issue,
                pull_request=200,
            )
        SYNTHETIC_AUTHORITY_ADVANCES += 1
        change_path = (
            repository_root
            / "scripts"
            / "workflow_pilot"
            / f"synthetic_authority_{SYNTHETIC_AUTHORITY_ADVANCES}.py"
        )
        change_path.write_text(
            f"SYNTHETIC_AUTHORITY_ADVANCE = {SYNTHETIC_AUTHORITY_ADVANCES}\n",
            encoding="utf-8",
        )
        git(repository_root, "add", str(change_path.relative_to(repository_root)))
        git(
            repository_root,
            "commit",
            "-q",
            "-m",
            "test: synthetic authority advance "
            f"{SYNTHETIC_AUTHORITY_ADVANCES}\n\n"
            + agent_handoff.COPILOT_TRAILER,
        )
        parent_sha = git(repository_root, "rev-parse", "HEAD^")
        result_sha = git(repository_root, "rev-parse", "HEAD")
        handoff_id = (
            f"issue-{issue}-round-{current['handoff_sequence'] + 1}"
        )
        document = handoff_document(
            repository_root,
            parent_sha,
            result_sha,
            issue=issue,
            handoff_id=handoff_id,
        )
        prior_handoffs = [
            copy.deepcopy(event["history_receipt"])
            for event in current["history_events"]
        ]
        configure_review_successor(
            document,
            prior_handoffs[-1],
            handoff_id=handoff_id,
            owner=(
                f"owner-{current['handoff_sequence'] + 1}",
                101 + current["handoff_sequence"],
            ),
            pull_request=(
                current["pr_binding"]["pull_request"]
                if current["pr_binding"] is not None
                else None
            ),
        )
        document["prior_handoffs"] = prior_handoffs
        assignment_sent = min(
            datetime.fromisoformat(state["at"].replace("Z", "+00:00"))
            for state in document["handoffs"][0]["states"]
            if state["state"] == "assignment_sent"
        )
        shift_handoff_times(
            document,
            int(
                (
                    prior_closed_at
                    + timedelta(seconds=30)
                    - assignment_sent
                ).total_seconds()
            ),
        )
        refresh_coordinator_receipt(document, repository_root)
    report = agent_handoff.validate_document(document, repository_root)
    return set_history_authority(
        repository_root,
        current["sequence"] + 1,
        issue=issue,
        pull_request=(
            current["pr_binding"]["pull_request"]
            if current["pr_binding"] is not None
            else None
        ),
        document=document,
        result=report,
        handoff_id=handoff_id,
    )
def set_history_authority(
    repository_root,
    sequence,
    head_seal=None,
    *,
    issue=None,
    pull_request=None,
    document=None,
    result=None,
    handoff_id="issue-178-round-1",
):
    if sequence == 0:
        issue = 178 if issue is None else issue
        owner_root = AUTHORITY_OWNERS[str(repository_root)]
        reference = agent_handoff.history_authority_ref(issue, pull_request)
        anchor_reference = agent_handoff.history_anchor_ref(issue)
        publication = publication_attestation(
            repository_root,
            None,
            None,
            issue=issue,
            operation="bootstrap",
        )
        plan = agent_handoff.plan_history_authority(
            repository_root,
            "example/workflow",
            issue,
            None,
            operation="bootstrap",
            publication_attestation=publication,
        )
        parent = None
    else:
        if document is not None and result is not None:
            owner_root = AUTHORITY_OWNERS[str(repository_root)]
            current, history_receipt, plan = plan_advance_authority(
                repository_root,
                document,
                result,
                issue=issue,
                pull_request=pull_request,
                handoff_id=handoff_id,
            )
            issue = history_receipt["issue"]
            pull_request = history_receipt["pull_request"]
            reference = agent_handoff.history_authority_ref(issue, pull_request)
            anchor_reference = agent_handoff.history_anchor_ref(issue)
            parent = current["object_id"]
        else:
            raise AssertionError(
                "set_history_authority sequence>0 requires document/result; "
                "use advance_history_authority for synthetic moves"
            )
    planned_sequence = plan["record"]["sequence"]
    if planned_sequence != sequence:
        raise AssertionError("authority test sequence mismatch")
    plan["ref"] = reference
    plan["anchor_ref"] = anchor_reference
    return publish_authority_plan(
        repository_root,
        owner_root,
        plan,
        parent,
        issue=issue,
        pull_request=pull_request,
    )
def ensure_remote_branch(repository_root, branch):
    head_sha = git(repository_root, "rev-parse", f"refs/heads/{branch}")
    git(
        repository_root,
        "push",
        "-q",
        "origin",
        f"{head_sha}:refs/heads/{branch}",
    )
def bind_history_authority(
    repository_root,
    *,
    issue=178,
    pull_request=200,
    base_branch="master",
    head_branch=None,
    coordinator_database_id=None,
):
    owner_root = AUTHORITY_OWNERS[str(repository_root)]
    current = agent_handoff.read_history_authority(
        repository_root,
        "example/workflow",
        issue,
        None,
    )
    if head_branch is None:
        head_branch = current["delivery_expectation"]["delivery_branch"]
    ensure_remote_branch(repository_root, base_branch)
    ensure_remote_branch(repository_root, head_branch)
    pr_observation = pull_request_observation(
        repository_root,
        current,
        pull_request=pull_request,
        base_branch=base_branch,
        head_branch=head_branch,
        coordinator_database_id=coordinator_database_id,
    )
    publication = authority_publication(
        repository_root,
        current,
        issue=issue,
        operation="bind",
        pr_observation=pr_observation,
    )
    plan = agent_handoff.plan_history_authority(
        repository_root,
        "example/workflow",
        issue,
        pull_request,
        operation="bind",
        expected_object_id=current["object_id"],
        expected_sequence=current["sequence"],
        pull_request_observation=pr_observation,
        publication_attestation=publication,
    )
    return publish_authority_plan(
        repository_root,
        owner_root,
        plan,
        current["object_id"],
        issue=issue,
        pull_request=pull_request,
    )
def publish_bound_history_authority(
    repository_root,
    current,
    observation,
    publication,
    *,
    issue=178,
    sequence=None,
    handoff_sequence=None,
    head_seal=None,
):
    owner_root = AUTHORITY_OWNERS[str(repository_root)]
    if sequence is None:
        sequence = current["sequence"] + 1
    if handoff_sequence is None:
        handoff_sequence = current["handoff_sequence"]
    if head_seal is None:
        head_seal = current["head_seal"]
    record = {
        "schema_version": 2,
        "repository": "example/workflow",
        "issue": issue,
        "sequence": sequence,
        "handoff_sequence": handoff_sequence,
        "head_seal": head_seal,
        "pr_binding": copy.deepcopy(observation),
        "signer": current["signer"],
        "ruleset_id": current["ruleset_id"],
        "authorized_bypass_actors": current["authorized_bypass_actors"],
        "delivery_expectation": current["delivery_expectation"],
        "publication_attestation": copy.deepcopy(publication),
        "event": {
            "kind": "pr_binding",
            "handoff_seal": None,
            "handoff_id": None,
            "handoff_kind": None,
            "lifecycle_state": None,
            "candidate_sha": None,
            "closed_at": None,
            "operation_nonce": None,
            "consume_store_id": None,
            "consume_sequence": None,
            "consume_anchor": None,
            "assignment": None,
            "interruption_snapshot": None,
            "history_receipt": None,
            "history_carrier": None,
        },
        "previous_object_id": current["object_id"],
    }
    object_id = owner_create_record_commit(
        owner_root,
        record,
        "authority.json",
        current["object_id"],
    )
    anchor_record = {
        "schema_version": 1,
        "repository": "example/workflow",
        "issue": issue,
        "sequence": sequence,
        "authority_object_id": object_id,
        "previous_object_id": current["anchor_object_id"],
    }
    anchor_object_id = owner_create_record_commit(
        owner_root,
        anchor_record,
        "anchor.json",
        current["anchor_object_id"],
    )
    git(
        owner_root,
        "push",
        "-q",
        "--atomic",
        "origin",
        f"{object_id}:{current['ref']}",
        f"{anchor_object_id}:{current['anchor_ref']}",
    )
    return object_id, anchor_object_id
def publish_bound_authority(
    repository_root,
    current,
    *,
    observation=None,
    publication=None,
    current_base_oid=None,
    **overrides,
):
    if observation is None:
        ensure_remote_branch(
            repository_root,
            current["delivery_expectation"]["immediate_base_branch"],
        )
        ensure_remote_branch(
            repository_root,
            current["delivery_expectation"]["delivery_branch"],
        )
        observation = pull_request_observation(repository_root, current)
    if publication is None:
        publication = authority_publication(
            repository_root, current, operation="bind",
            pr_observation=observation, current_base_oid=current_base_oid,
        )
    publish_bound_history_authority(repository_root, current, observation, publication, **overrides)
    return observation, publication
def handoff_lifecycle_as_of(*bundles):
    latest = max(
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        for bundle in bundles
        for handoff in bundle["result"]["handoffs"]
        for timestamp in (
            handoff["assigned_at"],
            handoff["closed_at"] or handoff["assigned_at"],
        )
    )
    return iso_utc(latest + timedelta(seconds=1))
def reporter_fixture_with_handoffs(*bundles):
    fixture = test_reporter.minimal_fixture()
    fixture["schema_version"] = reporter.HANDOFF_FIXTURE_SCHEMA_VERSION
    fixture["lifecycle_as_of"] = handoff_lifecycle_as_of(*bundles)
    fixture["review_thread_event_source"]["coverage_end"] = fixture[
        "lifecycle_as_of"
    ]
    fixture["implementation_handoffs"] = copy.deepcopy(list(bundles))
    return fixture

@contextmanager
def handoff_repository(
    *,
    issue=178,
    branch=None,
    authorized_coordinators=None,
    authorized_non_user_bypass_actors=None,
):
    with tempfile.TemporaryDirectory(
        prefix="agent-handoff-",
        dir=TEST_ARTIFACTS,
    ) as temporary:
        if branch is None:
            branch = f"agent/issue-{issue}"
        if authorized_coordinators is None:
            authorized_coordinators = [
                {"login": "coordinator", "database_id": 9001}
            ]
        if authorized_non_user_bypass_actors is None:
            authorized_non_user_bypass_actors = []
        test_root = Path(temporary)
        remote_root = test_root / "authority.git"
        owner_root = test_root / "owner"
        repository_root = test_root / "implementation"
        installation_root = test_root / "coordinator-installation"
        signer_root = test_root / "external-signer"
        remote_root.mkdir()
        owner_root.mkdir()
        repository_root.mkdir()
        installation_root.mkdir(mode=0o700)
        signer_root.mkdir(mode=0o700)
        git(remote_root, "init", "-q", "--bare")
        git(remote_root, "config", "receive.denyNonFastForwards", "true")
        git(remote_root, "config", "receive.denyDeletes", "true")
        git(remote_root, "config", "receive.advertiseAtomic", "true")
        transaction_hook = remote_root / "hooks" / "reference-transaction"
        transaction_hook.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" != prepared ] && exit 0\n"
            f"printf ran > '{remote_root.parent / 'hook-ran'}'\n"
            "updates=$(cat)\n"
            "auth=$(printf '%s\\n' \"$updates\" | grep -c "
            "'refs/heads/workflow-pilot/authority/issue-' || true)\n"
            "anchor=$(printf '%s\\n' \"$updates\" | grep -c "
            "'refs/heads/workflow-pilot/authority-anchor/issue-' || true)\n"
            "[ \"$auth\" -eq 0 ] && [ \"$anchor\" -eq 0 ] && exit 0\n"
            "[ \"$auth\" -eq 1 ] && [ \"$anchor\" -eq 1 ]\n",
            encoding="utf-8",
        )
        transaction_hook.chmod(0o700)
        git(owner_root, "init", "-q", "-b", "master")
        git(owner_root, "config", "user.name", "Authority Owner")
        git(owner_root, "config", "user.email", "owner@example.invalid")
        git(owner_root, "remote", "add", "origin", str(remote_root))
        owner_write_blob_ref(
            owner_root,
            agent_handoff.REPOSITORY_IDENTITY_REF,
            {
                "schema_version": 1,
                "repository": "example/workflow",
            },
        )
        git(repository_root, "init", "-q", "-b", "master")
        git(repository_root, "config", "user.name", "Handoff Test")
        git(repository_root, "config", "user.email", "handoff@example.invalid")
        git(
            repository_root,
            "remote",
            "add",
            "origin",
            str(remote_root),
        )
        private_key = signer_root / "private.pem"
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                str(private_key),
            ],
            check=True,
            capture_output=True,
        )
        signer_service = subprocess.Popen(
            [sys.executable, "-I", "-c", SIGNER_SERVICE, str(private_key)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        signer_numbers = json.loads(signer_service.stdout.readline())
        private_key.unlink()
        signer_public = {
            "algorithm": "rsa-pkcs1v15-sha256",
            "key_id": "",
            "modulus_hex": signer_numbers["modulus_hex"],
            "exponent": signer_numbers["exponent"],
            "service_identity": "test-external-coordinator-signer",
            "isolation_attestation": {
                "kind": "external-isolated-service",
                "private_key_in_implementation_namespace": False,
                "signing_api": "single-use-terminal-attestation",
            },
        }
        signer_public = signer_public_with_key_id(signer_public)
        SIGNER_SERVICES[str(repository_root)] = signer_service
        SIGNER_CONSUME_STATES[str(repository_root)] = {
            "store_id": "test-external-monotonic-store",
            "sequence": 0,
            "anchor": "0" * 64,
        }
        AUTHORITY_OWNERS[str(repository_root)] = owner_root
        AUTHORIZED_COORDINATORS[str(repository_root)] = copy.deepcopy(
            authorized_coordinators
        )
        bootstrap_validator = installation_root / "raw_diff_check.py"
        bootstrap_validator.write_bytes(
            agent_handoff.RAW_DIFF_CHECK_PATH.read_bytes()
        )
        bootstrap_validator.chmod(0o500)
        (installation_root / "installation.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repository": "example/workflow",
                    "repository_database_id": 7001,
                    "collector": {
                        "login": "collector",
                        "database_id": 9000,
                    },
                    "authorized_coordinators": authorized_coordinators,
                    "authorized_non_user_bypass_actors": (
                        authorized_non_user_bypass_actors
                    ),
                    "authority_protection": {
                        "mode": "bare-remote-config",
                        "ruleset_id": 77,
                        "enforcement": "active",
                        "authority_ref_prefix": (
                            agent_handoff.HISTORY_REF_PREFIX
                        ),
                        "anchor_ref_prefix": (
                            agent_handoff.HISTORY_ANCHOR_REF_PREFIX
                        ),
                        "remote_url": str(remote_root),
                        "force_pushes_allowed": False,
                        "deletions_allowed": False,
                    },
                    "delivery": {
                        "immediate_base_branch": "master",
                        "delivery_branch": branch,
                        "head_repository_full_name": "example/workflow",
                    },
                    "bootstrap_validator": {
                        "path": str(bootstrap_validator),
                    },
                    "signer_public": signer_public,
                }
            ),
            encoding="utf-8",
        )
        COORDINATOR_INSTALLATIONS[str(repository_root)] = installation_root
        seed = repository_root / "README.md"
        seed.write_text("base\n", encoding="utf-8")
        checker = (
            repository_root
            / "scripts"
            / "workflow_pilot"
            / "raw_diff_check.py"
        )
        checker.parent.mkdir(parents=True)
        checker.write_bytes(agent_handoff.RAW_DIFF_CHECK_PATH.read_bytes())
        schema = repository_root / agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH
        schema.write_bytes(
            (
                ROOT
                / agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH
            ).read_bytes()
        )
        git(
            repository_root,
            "add",
            "README.md",
            agent_handoff.RAW_DIFF_CHECK_REPOSITORY_PATH,
            agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH,
        )
        git(repository_root, "commit", "-q", "-m", "test: base")
        base_sha = git(repository_root, "rev-parse", "HEAD")
        seed.write_text("base\nparent\n", encoding="utf-8")
        git(repository_root, "add", "README.md")
        git(repository_root, "commit", "-q", "-m", "test: assigned parent")
        parent_sha = git(repository_root, "rev-parse", "HEAD")
        git(repository_root, "switch", "-q", "-c", branch)
        implementation = repository_root / "scripts" / "workflow_pilot"
        implementation.mkdir(parents=True, exist_ok=True)
        (implementation / "change.py").write_text(
            "HANDOFF = True\nEVIDENCE = 'focused'\n",
            encoding="utf-8",
        )
        git(repository_root, "add", "scripts/workflow_pilot/change.py")
        git(
            repository_root,
            "commit",
            "-q",
            "-m",
            "feat(workflow): test bounded handoff\n\n"
            + agent_handoff.COPILOT_TRAILER,
        )
        result_sha = git(repository_root, "rev-parse", "HEAD")
        try:
            with mock.patch.dict(
                os.environ,
                {
                    agent_handoff.COORDINATOR_INSTALLATION_ENV: str(
                        installation_root
                    )
                },
            ):
                set_history_authority(repository_root, 0, None, issue=issue)
                yield repository_root, base_sha, parent_sha, result_sha
        finally:
            signer_service.stdin.close()
            signer_service.wait(timeout=10)
            signer_service.stdout.close()
            signer_service.stderr.close()
            del AUTHORITY_OWNERS[str(repository_root)]
            del COORDINATOR_INSTALLATIONS[str(repository_root)]
            del AUTHORIZED_COORDINATORS[str(repository_root)]
            del SIGNER_SERVICES[str(repository_root)]
            del SIGNER_CONSUME_STATES[str(repository_root)]
def timestamped_states(receipt=None):
    if receipt is not None:
        started = datetime.fromisoformat(
            receipt["started_at"].replace("Z", "+00:00")
        )
        completed = datetime.fromisoformat(
            receipt["completed_at"].replace("Z", "+00:00")
        )
        return [
            {
                "state": "assignment_sent",
                "at": (started - timedelta(seconds=2))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {
                "state": "assignment_received",
                "at": (started - timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {"state": "progressing", "at": receipt["started_at"]},
            {
                "state": "committed",
                "at": (completed + timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            {
                "state": "handed_off",
                "at": (completed + timedelta(seconds=2))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        ]
    return [
        {
            "state": "assignment_sent",
            "at": (
                datetime.now(timezone.utc).replace(microsecond=0)
                - timedelta(minutes=4)
            ).isoformat().replace("+00:00", "Z"),
        },
        {
            "state": "assignment_received",
            "at": (
                datetime.now(timezone.utc).replace(microsecond=0)
                - timedelta(minutes=3)
            ).isoformat().replace("+00:00", "Z"),
        },
        {
            "state": "progressing",
            "at": (
                datetime.now(timezone.utc).replace(microsecond=0)
                - timedelta(minutes=2)
            ).isoformat().replace("+00:00", "Z"),
        },
        {
            "state": "committed",
            "at": (
                datetime.now(timezone.utc).replace(microsecond=0)
                - timedelta(minutes=1)
            ).isoformat().replace("+00:00", "Z"),
        },
        {
            "state": "handed_off",
            "at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    ]
def evidence(status="passed", completed_at=None):
    if completed_at is None:
        completed_at = (
            datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(seconds=90)
        ).isoformat().replace("+00:00", "Z")
    exit_code = 0 if status == "passed" else None
    return [
        {
            "id": "acceptance",
            "kind": "acceptance",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "Acceptance criteria were exercised.",
        },
        {
            "id": "focused-check",
            "kind": "check",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "The focused module passed.",
        },
        {
            "id": "budget-lines",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "Git provided the changed-line count.",
        },
        {
            "id": "budget-rom",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "No ROM-producing path changed.",
        },
        {
            "id": "budget-ram",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "No RAM-owning path changed.",
        },
        {
            "id": "budget-protocol",
            "kind": "budget",
            "status": status,
            "exit_code": exit_code,
            "completed_at": completed_at,
            "detail": "The one admitted protocol change is versioned.",
        },
    ]
def shift_handoff_times(document, seconds):
    delta = timedelta(seconds=seconds)
    def shifted(value):
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00")) + delta
        ).isoformat().replace("+00:00", "Z")
    for handoff in document["handoffs"]:
        for state in handoff["states"]:
            state["at"] = shifted(state["at"])
        for item in handoff["evidence"]:
            item["completed_at"] = shifted(item["completed_at"])
        for receipt in handoff["check_receipts"]:
            receipt["started_at"] = shifted(receipt["started_at"])
            receipt["completed_at"] = shifted(receipt["completed_at"])
            receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
def retime_handoff(
    document,
    *,
    assignment_sent,
    state_offsets,
    evidence_offset,
    receipt_offsets,
):
    handoff = document["handoffs"][0]
    for state, offset in zip(handoff["states"], state_offsets):
        state["at"] = iso_utc(assignment_sent + offset)
    for item in handoff["evidence"]:
        item["completed_at"] = iso_utc(assignment_sent + evidence_offset)
    receipt_start, receipt_end = receipt_offsets
    for receipt in handoff["check_receipts"]:
        receipt["started_at"] = iso_utc(assignment_sent + receipt_start)
        receipt["completed_at"] = iso_utc(assignment_sent + receipt_end)
        receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
def coordinator_receipt(
    document,
    repository_root,
    *,
    actions=(),
    incomplete_sources=(),
    availability=None,
    resource_receipts=(),
):
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    states = [
        state
        for handoff in document["handoffs"]
        for state in handoff["states"]
    ]
    assignment_start = min(
        datetime.fromisoformat(state["at"].replace("Z", "+00:00"))
        for state in states
        if state["state"] == "assignment_sent"
    )
    implementation_terminated_at = max(
        datetime.fromisoformat(state["at"].replace("Z", "+00:00"))
        for state in states
    )
    actors = {
        ("collector", 9000),
        (
            document["coordinators"][0]["login"].casefold(),
            document["coordinators"][0]["database_id"],
        ),
        *(
            (handoff["owner_id"].casefold(), handoff["owner_database_id"])
            for handoff in document["handoffs"]
        ),
        *(
            (action["actor_login"].casefold(), action["actor_database_id"])
            for action in actions
        ),
    }
    resolved_at = (
        assignment_start - timedelta(seconds=2)
    ).isoformat().replace("+00:00", "Z")
    actor_records = [
        {
            "login": login,
            "database_id": database_id,
            "resolved_at": resolved_at,
            "source": "github-actor-api",
        }
        for login, database_id in sorted(actors, key=lambda item: item[1])
    ]
    source_records = []
    for name in agent_handoff.REMOTE_COVERAGE_SOURCES:
        events = [
            copy.deepcopy(action)
            for action in actions
            if action["source"] == name
        ]
        source_records.append(
            {
                "name": name,
                "available": name not in incomplete_sources,
                "complete": name not in incomplete_sources,
                "total_count": len(events),
                "observed_at": issued_at.isoformat().replace("+00:00", "Z"),
                "events": events,
            }
        )
    telemetry = []
    implementation_processes = []
    status = git(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    status_sha256 = hashlib.sha256(status.encode("utf-8")).hexdigest()
    for index, handoff in enumerate(document["handoffs"]):
        interruption = handoff["interruption"]
        snapshot = None
        if interruption is not None:
            file_records = []
            for preserved_path in interruption["preserved_paths"]:
                preserved = repository_root / preserved_path
                content = preserved.read_bytes()
                file_records.append(
                    {
                        "path": preserved_path,
                        "mode": preserved.stat().st_mode & 0o100777,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "content_base64": base64.b64encode(content).decode(
                            "ascii"
                        ),
                    }
                )
            snapshot = {
                "status_sha256": status_sha256,
                "dirty_paths": sorted(
                    set(interruption["preserved_paths"])
                ),
                "preserved_paths": sorted(
                    interruption["preserved_paths"]
                ),
                "files": sorted(
                    file_records,
                    key=lambda item: item["path"],
                ),
            }
        telemetry.append(
            {
                "handoff_id": handoff["id"],
                "owner_database_id": handoff["owner_database_id"],
                "started_at": handoff["states"][0]["at"],
                "ended_at": handoff["states"][-1]["at"],
                "peak_rss_bytes": 134217728 + index,
                "coordination_turns": 2,
                "recovery_minutes": 7 if interruption is not None else 0,
                "interruption_snapshot": snapshot,
                "source": "coordinator-runtime",
            }
        )
        implementation_processes.append(
            {
                "handoff_id": handoff["id"],
                "started_at": handoff["states"][0]["at"],
                "ended_at": handoff["states"][-1]["at"],
                "credentials_available": False,
                "network_mode": "denied",
                "source": "coordinator-launcher",
            }
        )
    if availability is None:
        availability = {
            "mode": "always_on",
            "observed_at": (
                assignment_start - timedelta(seconds=1)
            ).isoformat().replace("+00:00", "Z"),
            "valid_until": (
                issued_at + timedelta(hours=2)
            ).isoformat().replace("+00:00", "Z"),
            "unattended_from": (
                assignment_start - timedelta(seconds=1)
            ).isoformat().replace("+00:00", "Z"),
            "unattended_until": (
                issued_at + timedelta(hours=1)
            ).isoformat().replace("+00:00", "Z"),
            "autostop_enabled": False,
            "stop_on_disconnect": False,
            "enforcement_source": "coordinator-launcher",
        }
    authority = document["history_authority"]
    if authority["pr_binding"] is None:
        pr_observation = None
    else:
        pr_observation = copy.deepcopy(authority["pr_binding"])
    receipt = {
        "schema_version": 2,
        "repository": "example/workflow",
        "repository_database_id": 7001,
        "collector_login": "collector",
        "collector_database_id": 9000,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "operation": {
            "nonce": secrets.token_hex(32),
            "started_at": (
                assignment_start - timedelta(seconds=2)
            ).isoformat().replace("+00:00", "Z"),
            "implementation_terminated_at": (
                implementation_terminated_at.isoformat().replace(
                    "+00:00",
                    "Z",
                )
            ),
            "collected_through": issued_at.isoformat().replace("+00:00", "Z"),
            "eligibility_instant": issued_at.isoformat().replace(
                "+00:00",
                "Z",
            ),
            "implementation_terminated": True,
            "single_use": True,
        },
        "authority_protection": {
            "source": "github-rulesets-api",
            "repository_id": 7001,
            "repository_full_name": "example/workflow",
            "authority_ref": authority["ref"],
            "anchor_ref": authority["anchor_ref"],
            "authority_object_id": authority["object_id"],
            "anchor_object_id": authority["anchor_object_id"],
            "observed_at": issued_at.isoformat().replace("+00:00", "Z"),
            "response": ruleset_response(
                authority["issue"],
                repository_root=repository_root,
            ),
        },
        "pull_request_observation": pr_observation,
        "availability": availability,
        "remote_coverage": {
            "interval_start": (
                assignment_start - timedelta(seconds=1)
            ).isoformat().replace("+00:00", "Z"),
            "interval_end": issued_at.isoformat().replace("+00:00", "Z"),
            "actors": actor_records,
            "sources": source_records,
            "observed_actions": [copy.deepcopy(action) for action in actions],
            "implementation_processes": implementation_processes,
        },
        "runtime_telemetry": telemetry,
        "resource_receipts": list(resource_receipts),
    }
    return receipt
def refresh_coordinator_receipt(document, repository_root, **kwargs):
    document["coordinator_receipt"] = coordinator_receipt(
        document,
        repository_root,
        **kwargs,
    )
    sign_coordinator_document(document, repository_root)
def set_coordinator_receipt_time(document, stamp):
    receipt = document["coordinator_receipt"]
    receipt["issued_at"] = stamp
    for field in ("collected_through", "eligibility_instant"):
        receipt["operation"][field] = stamp
    receipt["authority_protection"]["observed_at"] = stamp
    receipt["remote_coverage"]["interval_end"] = stamp
    for source in receipt["remote_coverage"]["sources"]:
        source["observed_at"] = stamp
def resign_coordinator_receipt(document, repository_root):
    document["coordinator_receipt"]["signature"] = external_sign(
        repository_root,
        agent_handoff.coordinator_attestation_payload(document),
    )
def delivery_graph(
    *,
    child_issue=178,
    child_status="pending",
    child_handoff_id="issue-178-round-1",
    child_candidate_sha="b" * 40,
    parent_master_sha="a" * 40,
    merge_status="done",
    build_status="in_progress",
    remote_status="pending",
    watcher_process="running",
    watcher_status="in_progress",
    watcher_conclusion=None,
    recovery_status="not_required",
):
    def task(
        task_id,
        issue,
        pull_request,
        phase,
        status,
        *,
        handoff_id=None,
        candidate_sha=None,
    ):
        return {
            "id": task_id,
            "issue": issue,
            "pull_request": pull_request,
            "phase": phase,
            "status": status,
            "status_reason": (
                (
                    "workflow_failed"
                    if phase == "post_merge_build"
                    else (
                        "owner_interrupted"
                        if phase == "implementation"
                        else "dependency"
                    )
                )
                if status == "blocked"
                else None
            ),
            "handoff_id": handoff_id,
            "candidate_sha": candidate_sha,
        }
    return {
        "relationships": [
            {
                "child_issue": child_issue,
                "parent_issue": 176,
                "handoff_id": child_handoff_id,
                "type": "code_contract",
            }
        ],
        "tasks": [
            task("parent-merge", 176, 183, "merge", merge_status),
            task(
                "parent-post-merge-build",
                176,
                183,
                "post_merge_build",
                build_status,
                candidate_sha=parent_master_sha,
            ),
            task("parent-completion", 176, 183, "completion", "pending"),
            task("parent-closure", 176, 183, "closure", "pending"),
            task(
                "parent-remote",
                176,
                183,
                "remote_completion",
                remote_status,
            ),
            task(
                "parent-recovery",
                176,
                183,
                "fix_forward_revert",
                recovery_status,
            ),
            task(
                "child-implement",
                child_issue,
                None,
                "implementation",
                child_status,
                handoff_id=child_handoff_id,
                candidate_sha=child_candidate_sha,
            ),
        ],
        "dependencies": [
            {
                "task": "child-implement",
                "depends_on": "parent-merge",
                "type": "code_contract",
            },
            {
                "task": "parent-completion",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
            {
                "task": "parent-closure",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
            {
                "task": "parent-remote",
                "depends_on": "parent-post-merge-build",
                "type": "delivery_gate",
            },
        ],
        "workflow_runs": (
            []
            if build_status == "pending" and watcher_process is None
            else [
                {
                    "id": 9002,
                    "run_task": "parent-post-merge-build",
                    "head_sha": parent_master_sha,
                    "status": watcher_status,
                    "conclusion": watcher_conclusion,
                    "source": "github-actions-api",
                }
            ]
        ),
        "watchers": (
            []
            if watcher_process is None
            else [
                {
                    "id": "parent-master-watcher",
                    "run_id": 9002,
                    "process_state": watcher_process,
                }
            ]
        ),
    }
def handoff_document(
    repository_root,
    parent_sha,
    result_sha,
    *,
    issue=178,
    branch=None,
    handoff_id=None,
):
    receipt = agent_handoff.execute_allowed_check(
        receipt_id="receipt-focused-module",
        check_id="focused-module",
        contract="git-diff-check",
        repository_root=repository_root,
        parent_sha=parent_sha,
        candidate_sha=result_sha,
    )
    for field in ("started_at", "completed_at"):
        shifted = datetime.fromisoformat(receipt[field].replace("Z", "+00:00"))
        receipt[field] = (
            shifted - timedelta(seconds=10)
        ).isoformat().replace("+00:00", "Z")
    receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
    authority = agent_handoff.read_history_authority(
        repository_root,
        "example/workflow",
        issue,
        None,
    )
    if branch is None:
        branch = authority["delivery_expectation"]["delivery_branch"]
    if handoff_id is None:
        handoff_id = f"issue-{issue}-round-1"
    coordinator = installation_authorized_coordinators(repository_root)[0]
    pull_request = (
        authority["pr_binding"]["pull_request"]
        if authority["pr_binding"] is not None
        else None
    )
    graph = delivery_graph(
        child_issue=issue,
        child_handoff_id=handoff_id,
        child_status="done",
        child_candidate_sha=result_sha,
        parent_master_sha=parent_sha,
    )
    next(
        task
        for task in graph["tasks"]
        if task["phase"] == "implementation"
    )["pull_request"] = pull_request
    document = {
        "schema_version": 2,
        "repository": "example/workflow",
        "prior_handoffs": [],
        "history_authority": authority,
        "delivery_graph": graph,
        "coordinators": [
            {
                "id": "coordinator-1",
                "login": coordinator["login"],
                "database_id": coordinator["database_id"],
            }
        ],
        "handoffs": [
            {
                "id": handoff_id,
                "issue": issue,
                "pull_request": pull_request,
                "owner_id": "owner-1",
                "owner_database_id": 101,
                "handoff_kind": "root",
                "replaces_handoff_id": None,
                "assigned_parent_sha": parent_sha,
                "expected_branch": branch,
                "allowed_worktree": str(repository_root),
                "allowed_scope": ["scripts/workflow_pilot/"],
                "finding_ids": ["F-178-1"],
                "acceptance_criteria": [
                    {
                        "id": "AC-178-1",
                        "text": "Only an exact clean descendant enters trusted push.",
                        "evidence_ids": [
                            "acceptance",
                            "budget-lines",
                            "budget-rom",
                            "budget-ram",
                            "budget-protocol",
                        ],
                    }
                ],
                "required_checks": [
                    {
                        "id": "focused-module",
                        "contract": "git-diff-check",
                        "receipt_id": "receipt-focused-module",
                        "evidence_id": "focused-check",
                    }
                ],
                "budgets": {
                    "changed_lines": 20,
                    "rom_bytes": 0,
                    "ram_bytes": 0,
                    "protocol_changes": 0,
                },
                "prohibited_remote_actions": sorted(
                    agent_handoff.PROHIBITED_REMOTE_ACTIONS
                ),
                "max_lifetime_seconds": 3600,
                "max_peak_rss_bytes": 536870912,
                "states": timestamped_states(receipt),
                "evidence": evidence(completed_at=receipt["completed_at"]),
                "check_receipts": [receipt],
                "result": {
                    "sha": result_sha,
                },
                "interruption": None,
                "recovery_resolution": [],
            }
        ],
        "workflow_runs": [],
        "watchers": [],
    }
    refresh_coordinator_receipt(document, repository_root)
    return document
def add_run(document, result_sha, conclusion="success", process_result="success"):
    document["workflow_runs"] = [
        {
            "id": 9001,
            "handoff_id": document["handoffs"][0]["id"],
            "head_sha": result_sha,
            "status": "completed",
            "conclusion": conclusion,
            "observed_at": "2026-01-01T01:20:00Z",
            "source": "github-actions-api",
        }
    ]
    document["watchers"] = [
        {
            "id": "watcher-9001",
            "coordinator_id": "coordinator-1",
            "run_id": 9001,
            "head_sha": result_sha,
            "kind": "direct_shell",
            "started_at": "2026-01-01T01:10:00Z",
            "ended_at": "2026-01-01T01:15:00Z",
            "process_result": process_result,
        }
    ]
    refresh_coordinator_receipt(
        document,
        Path(document["handoffs"][0]["allowed_worktree"]),
    )
def interrupted_handoff_document(
    repository_root,
    result_sha,
    *,
    preserved_path="scripts/workflow_pilot/recovery.py",
    content=b"TEST",
    replacement_handoff_id=None,
):
    preserved = repository_root / preserved_path
    preserved.parent.mkdir(parents=True, exist_ok=True)
    preserved.write_bytes(content)
    document = handoff_document(repository_root, result_sha, result_sha)
    handoff = document["handoffs"][0]
    handoff["result"] = None
    states = timestamped_states()[:3]
    interrupted_at = (
        datetime.fromisoformat(states[-1]["at"].replace("Z", "+00:00"))
        + timedelta(seconds=30)
    ).isoformat().replace("+00:00", "Z")
    handoff["states"] = states + [{"state": "interrupted", "at": interrupted_at}]
    handoff["evidence"] = evidence("incomplete")
    handoff["required_checks"][0]["receipt_id"] = None
    handoff["check_receipts"] = []
    handoff["interruption"] = {
        "kind": "sigkill_oom",
        "signal": 9,
        "occurred_at": interrupted_at,
        "kernel_evidence": "kernel OOM kill",
        "interrupted_check_ids": ["focused-module"],
        "preserved_paths": [preserved_path],
        "replacement_handoff_id": replacement_handoff_id,
        "host_process_actions": [],
    }
    task = next(
        item
        for item in document["delivery_graph"]["tasks"]
        if item["phase"] == "implementation"
    )
    task["status"] = "blocked"
    task["status_reason"] = "owner_interrupted"
    task["candidate_sha"] = result_sha
    refresh_coordinator_receipt(document, repository_root)
    return document
class DeliveryDependencyGraphTests(unittest.TestCase):
    def test_parent_merge_unblocks_child_before_parent_remote_completion(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        self.assertEqual(report["rejection_codes"], [])
        self.assertIn("child-implement", report["ready_tasks"])
        self.assertEqual(
            report["relationships"][0],
            {
                "child_issue": 178,
                "parent_issue": 176,
                "handoff_id": "issue-178-round-1",
                "type": "code_contract",
                "implementation_task": {
                    "id": "child-implement",
                    "issue": 178,
                    "pull_request": None,
                    "status": "pending",
                    "status_reason": None,
                    "handoff_id": "issue-178-round-1",
                    "candidate_sha": "b" * 40,
                },
                "required_edge": {
                    "task": "child-implement",
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                },
                "parent_merge_status": "done",
                "implementation_ready": True,
            },
        )
        blocked = {
            item["id"]: item["blocked_by"] for item in report["blocked_tasks"]
        }
        self.assertEqual(
            blocked["parent-remote"],
            ["parent-post-merge-build"],
        )
    def test_pending_parent_merge_blocks_child_implementation(self):
        graph = delivery_graph(
            merge_status="pending",
            build_status="pending",
            watcher_process=None,
        )
        report = agent_handoff.evaluate_delivery_graph(graph)
        self.assertNotIn("child-implement", report["ready_tasks"])
        self.assertIn(
            {
                "id": "child-implement",
                "blocked_by": ["parent-merge"],
            },
            report["blocked_tasks"],
        )
        self.assertFalse(report["relationships"][0]["implementation_ready"])
    def test_healthy_pending_master_watcher_is_not_a_todo_dependency(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        self.assertIn("child-implement", report["ready_tasks"])
        self.assertEqual(
            report["watchers"],
            [
                {
                    "id": "parent-master-watcher",
                    "run_id": 9002,
                    "run_task": "parent-post-merge-build",
                    "process_state": "running",
                    "head_sha": "a" * 40,
                    "authoritative_status": "in_progress",
                    "conclusion": None,
                    "orthogonal_to_todos": True,
                }
            ],
        )
        invalid = delivery_graph()
        invalid["dependencies"].append(
            {
                "task": "child-implement",
                "depends_on": "parent-master-watcher",
                "type": "delivery_gate",
            }
        )
        invalid_report = agent_handoff.evaluate_delivery_graph(invalid)
        self.assertIn(
            "watcher-todo-dependency",
            invalid_report["rejection_codes"],
        )
    def test_terminal_failed_master_requires_recovery_without_rewriting_history(self):
        pending_report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        failed = delivery_graph(
            build_status="blocked",
            watcher_process="error",
            watcher_status="completed",
            watcher_conclusion="failure",
            recovery_status="in_progress",
        )
        failed_report = agent_handoff.evaluate_delivery_graph(failed)
        self.assertIn("child-implement", pending_report["ready_tasks"])
        self.assertIn("child-implement", failed_report["ready_tasks"])
        self.assertEqual(
            failed_report["master_recovery"],
            [
                {
                    "parent_issue": 176,
                    "required": True,
                    "task": "parent-recovery",
                    "status": "in_progress",
                }
            ],
        )
        self.assertNotIn(
            "missing-master-recovery",
            failed_report["rejection_codes"],
        )
    def test_code_contract_edge_to_parent_remote_rejects_and_names_merge_edge(self):
        graph = delivery_graph()
        graph["dependencies"][0] = {
            "task": "child-implement",
            "depends_on": "parent-remote",
            "type": "code_contract",
        }
        report = agent_handoff.evaluate_delivery_graph(graph)
        self.assertIn(
            "missing-required-code-contract-edge",
            report["rejection_codes"],
        )
        self.assertIn(
            "wrong-code-contract-edge",
            report["rejection_codes"],
        )
        self.assertIn(
            {
                "task": "child-implement",
                "depends_on": "parent-merge",
                "type": "code_contract",
            },
            report["required_edges"],
        )
        self.assertNotIn("child-implement", report["ready_tasks"])
    def test_parent_completion_closure_and_remote_keep_post_merge_gate(self):
        report = agent_handoff.evaluate_delivery_graph(delivery_graph())
        required = {
            (item["task"], item["depends_on"], item["type"])
            for item in report["required_edges"]
        }
        for task_id in (
            "parent-completion",
            "parent-closure",
            "parent-remote",
        ):
            with self.subTest(task=task_id):
                self.assertIn(
                    (
                        task_id,
                        "parent-post-merge-build",
                        "delivery_gate",
                    ),
                    required,
                )
        invalid = delivery_graph()
        invalid["dependencies"] = [
            item
            for item in invalid["dependencies"]
            if item["task"] != "parent-closure"
        ]
        invalid_report = agent_handoff.evaluate_delivery_graph(invalid)
        self.assertIn(
            "missing-parent-post-merge-gate",
            invalid_report["rejection_codes"],
        )
class AuthorityReadRaceTests(unittest.TestCase):
    def test_stable_and_advance_before_read_boundaries(self):
        with handoff_repository() as (root, _base, _parent, _result):
            stable = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            self.assertEqual(stable["sequence"], 0)
            self.assertEqual(stable["observation"]["attempt"], 1)
            agent_handoff.confirm_history_authority_observation(
                root, installation_root_path(root), stable["observation"]
            )
            document = handoff_document(root, _parent, _result)
            report = agent_handoff.validate_document(document, root)
            set_history_authority(root, 1, document=document, result=report)
            advanced = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            self.assertEqual(advanced["sequence"], 1)
            self.assertRegex(advanced["head_seal"], r"^[0-9a-f]{64}$")
            self.assertEqual(advanced["observation"]["attempt"], 1)
    def test_concurrent_advance_retries_from_new_remote_oid(self):
        with handoff_repository() as (root, _base, _parent, _result):
            moved = False
            def advance_once(attempt, phase, _object_id):
                nonlocal moved
                if phase == "after-fetch" and attempt == 1 and not moved:
                    moved = True
                    document = handoff_document(root, _parent, _result)
                    report = agent_handoff.validate_document(document, root)
                    set_history_authority(
                        root,
                        1,
                        document=document,
                        result=report,
                    )
            authority = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
                observation_hook=advance_once,
            )
            self.assertTrue(moved)
            self.assertEqual(authority["sequence"], 1)
            self.assertRegex(authority["head_seal"], r"^[0-9a-f]{64}$")
            self.assertEqual(authority["observation"]["attempt"], 1)
    def test_repeated_remote_movement_exhausts_bounded_read(self):
        with handoff_repository() as (root, _base, _parent, _result):
            sequence = 0
            def advance_every_time(_attempt, phase, _object_id):
                nonlocal sequence
                if phase != "after-fetch":
                    return
                sequence += 1
                advance_history_authority(root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "authority-moved",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                    observation_hook=advance_every_time,
                )
            self.assertEqual(
                sequence,
                agent_handoff.AUTHORITY_READ_ATTEMPTS,
            )
    def test_advance_after_read_before_eligibility_rejects(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            report = agent_handoff.validate_document(document, root)
            advanced = False
            def advance_at_eligibility(_attempt, phase, _object_id):
                nonlocal advanced
                if phase == "before-eligibility-confirm" and not advanced:
                    advanced = True
                    set_history_authority(
                        root,
                        1,
                        document=document,
                        result=report,
                    )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "authority-moved",
            ):
                agent_handoff.validate_document(
                    document,
                    root,
                    authority_hook=advance_at_eligibility,
                )
            self.assertTrue(advanced)
class ExactHandoffTests(unittest.TestCase):
    def test_schema_v2_closes_candidate_reported_authority(self):
        schema = load_handoff_schema()
        validator_for_schema(schema)
        self.assertEqual(schema["protocol_version"], 8)
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertIn(
            "coordinator_receipt",
            schema["required"],
        )
        self.assertNotIn("remote_actions", schema["properties"])
        self.assertNotIn(
            "peak_rss_bytes",
            schema["$defs"]["handoff"]["properties"],
        )
        self.assertIn(
            "properties",
            schema["$defs"]["historyAuthority"],
        )
        self.assertIn(
            "properties",
            schema["$defs"]["historyReceiptHandedOff"],
        )
        self.assertIn(
            "properties",
            schema["$defs"]["historyReceiptInterrupted"],
        )
        self.assertIn(
            "history_carrier_digest",
            schema["$defs"]["publicationAttestation"]["required"],
        )
        self.assertEqual(
            schema["$defs"]["handedOffHistoryEvent"]["properties"][
                "history_carrier"
            ],
            {"type": "null"},
        )
    def test_schema_v2_has_no_generic_object_or_array_placeholders(self):
        schema = load_handoff_schema()
        def walk(node, path):
            if isinstance(node, dict):
                if (
                    node.get("type") == "object"
                    and "properties" not in node
                    and "$ref" not in node
                    and "oneOf" not in node
                    and "allOf" not in node
                    and "anyOf" not in node
                ):
                    self.fail(f"{path} leaves an object placeholder open")
                if (
                    node.get("type") == "array"
                    and "items" not in node
                    and "$ref" not in node
                    and "oneOf" not in node
                    and "allOf" not in node
                    and "anyOf" not in node
                ):
                    self.fail(f"{path} leaves an array placeholder open")
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")
        walk(schema, "$")
    def test_schema_v2_restricts_non_user_bypass_actor_types(self):
        schema = load_handoff_schema()
        validator = validator_for_schema(
            schema_ref(schema, "#/$defs/typedBypassActor")
        )
        validator.validate(
            {
                "actor_type": "Integration",
                "actor_id": 1,
                "bypass_mode": "always",
            }
        )
        with self.assertRaises(ValidationError):
            validator.validate(
                {
                    "actor_type": "MachineElf",
                    "actor_id": 1,
                    "bypass_mode": "always",
                }
            )
    def test_schema_v2_validates_real_document_and_prior_history(self):
        schema = load_handoff_schema()
        document_validator = validator_for_schema(schema)
        authority_validator = validator_for_schema(
            schema_ref(schema, "#/$defs/historyAuthority")
        )
        receipt_validator = validator_for_schema(
            schema_ref(schema, "#/$defs/historyReceipt")
        )
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document_validator.validate(document)
            authority_validator.validate(document["history_authority"])
            handoff_result = agent_handoff.validate_document(document, root)
            receipt = agent_handoff.make_history_receipt(
                document,
                handoff_result,
                "issue-178-round-1",
            )
            for field, value in (
                ("trusted_push_eligible", False),
                ("rejection_codes", ["duplicate-watcher"]),
            ):
                with self.subTest(field=field):
                    changed = copy.deepcopy(handoff_result)
                    changed["summary"][field] = value
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "canonical validation output",
                    ):
                        agent_handoff.make_history_receipt(
                            document,
                            changed,
                            "issue-178-round-1",
                        )
            receipt_validator.validate(receipt)
            with_prior = copy.deepcopy(document)
            with_prior["prior_handoffs"] = [receipt]
            document_validator.validate(with_prior)
    def test_schema_v2_rejects_authority_history_unknown_missing_and_wrong_types(self):
        schema = load_handoff_schema()
        authority_validator = validator_for_schema(
            schema_ref(schema, "#/$defs/historyAuthority")
        )
        receipt_validator = validator_for_schema(
            schema_ref(schema, "#/$defs/historyReceipt")
        )
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            handoff_result = agent_handoff.validate_document(document, root)
            receipt = agent_handoff.make_history_receipt(
                document,
                handoff_result,
                "issue-178-round-1",
            )
        unknown_authority = copy.deepcopy(document["history_authority"])
        unknown_authority["unexpected"] = True
        with self.assertRaises(ValidationError):
            authority_validator.validate(unknown_authority)
        missing_receipt = copy.deepcopy(receipt)
        del missing_receipt["assignment"]
        with self.assertRaises(ValidationError):
            receipt_validator.validate(missing_receipt)
        wrong_type_receipt = copy.deepcopy(receipt)
        wrong_type_receipt["consume_sequence"] = "1"
        with self.assertRaises(ValidationError):
            receipt_validator.validate(wrong_type_receipt)
    def test_time_schema_and_parser_limit_fractional_precision(self):
        schema = schema_ref(load_handoff_schema(), "#/$defs/time")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        accepted = {
            "2026-01-01T00:00:00Z": 0,
            "2026-01-01T00:00:00.1Z": 100000,
            "2026-01-01T00:00:00.12Z": 120000,
            "2026-01-01T00:00:00.123Z": 123000,
            "2026-01-01T00:00:00.1234Z": 123400,
            "2026-01-01T00:00:00.12345Z": 123450,
            "2026-01-01T00:00:00.123456Z": 123456,
            "2024-02-29T23:59:59.123456Z": 123456,
        }
        for stamp, microseconds in accepted.items():
            with self.subTest(stamp=stamp):
                validator.validate(stamp)
                parsed = reporter.parse_time(stamp, "stamp")
                self.assertEqual(parsed.microsecond, microseconds)
                self.assertEqual(
                    agent_handoff.authoritative_current_time(parsed, label="stamp").microsecond,
                    microseconds,
                )
        for stamp in (
            "2026-02-29T00:00:00Z",
            "2024-13-01T00:00:00Z",
            "2024-04-31T00:00:00Z",
            "2024-01-01T24:00:00Z",
            "2024-01-01T00:60:00Z",
            "2024-01-01T23:59:60Z",
            "2026-01-01T00:00:00.1234567Z",
            "2026-01-01T00:00:00.123456789Z",
            "2026-01-01T00:00:00+00:00",
        ):
            with self.subTest(invalid=stamp):
                with self.assertRaises(ValidationError):
                    validator.validate(stamp)
                with self.assertRaises(reporter.PilotDataError):
                    reporter.parse_time(stamp, "stamp")
        with self.assertRaisesRegex(agent_handoff.HandoffDataError, "UTC"):
            agent_handoff.authoritative_current_time(
                datetime.fromisoformat("2026-01-01T00:00:00+01:00"),
                label="stamp",
            )
    def test_schema_v2_matches_runtime_structural_mutation_corpus(self):
        schema = load_handoff_schema()
        validator = validator_for_schema(schema)
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            validator.validate(document)
            accepted = agent_handoff.validate_document(document, root)
            self.assertTrue(accepted["summary"]["trusted_push_eligible"])
            def with_offset(value):
                return value[:-1] + "+00:00"
            def top_run_offset_time(doc):
                add_run(doc, result)
                doc["workflow_runs"][0]["observed_at"] = with_offset(
                    doc["workflow_runs"][0]["observed_at"]
                )
                refresh_coordinator_receipt(doc, root)
            cases = {
                "delivery-graph-placeholder": (
                    lambda doc: doc.__setitem__("delivery_graph", {}),
                    "delivery_graph is missing fields",
                ),
                "workflow-runs-placeholder": (
                    lambda doc: doc.__setitem__("workflow_runs", [123]),
                    "workflow_runs\\[0\\] must be an object",
                ),
                "watchers-placeholder": (
                    lambda doc: doc.__setitem__("watchers", [123]),
                    "watchers\\[0\\] must be an object",
                ),
                "acceptance-criteria-placeholder": (
                    lambda doc: doc["handoffs"][0].__setitem__(
                        "acceptance_criteria",
                        [123],
                    ),
                    "handoffs\\[0\\]\\.acceptance_criteria\\[0\\] must be an object",
                ),
                "required-checks-placeholder": (
                    lambda doc: doc["handoffs"][0].__setitem__(
                        "required_checks",
                        [123],
                    ),
                    "handoffs\\[0\\]\\.required_checks\\[0\\] must be an object",
                ),
                "budgets-placeholder": (
                    lambda doc: doc["handoffs"][0].__setitem__("budgets", []),
                    "handoffs\\[0\\]\\.budgets must be an object",
                ),
                "top-run-offset-time": (
                    top_run_offset_time,
                    "workflow_runs\\[0\\]\\.observed_at must be an RFC 3339 UTC timestamp",
                ),
                "operation-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__("operation", {}),
                    "coordinator_receipt\\.operation is missing fields",
                ),
                "authority-protection-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "authority_protection",
                        {},
                    ),
                    "coordinator_receipt\\.authority_protection is missing fields",
                ),
                "availability-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "availability",
                        {},
                    ),
                    "coordinator_receipt\\.availability is missing fields",
                ),
                "remote-coverage-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "remote_coverage",
                        {},
                    ),
                    "coordinator_receipt\\.remote_coverage is missing fields",
                ),
                "runtime-telemetry-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "runtime_telemetry",
                        [123],
                    ),
                    "coordinator_receipt\\.runtime_telemetry\\[0\\] must be an object",
                ),
                "resource-receipts-placeholder": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "resource_receipts",
                        [123],
                    ),
                    "coordinator_receipt\\.resource_receipts\\[0\\] must be an object",
                ),
                "issued-at-offset-time": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "issued_at",
                        with_offset(doc["coordinator_receipt"]["issued_at"]),
                    ),
                    "coordinator_receipt\\.issued_at must be an RFC 3339 UTC timestamp",
                ),
                "issued-at-overprecision": (
                    lambda doc: doc["coordinator_receipt"].__setitem__(
                        "issued_at",
                        "2026-01-01T00:00:00.1234567Z",
                    ),
                    "coordinator_receipt\\.issued_at must be an RFC 3339 UTC timestamp",
                ),
            }
            for name, (mutate, runtime_error) in cases.items():
                with self.subTest(name=name):
                    mutated = copy.deepcopy(document)
                    mutate(mutated)
                    assert_schema_runtime_rejects(
                        self,
                        validator=validator,
                        document=mutated,
                        repository_root=root,
                        runtime_error=runtime_error,
                    )
    def test_exact_clean_strict_descendant_is_accepted(self):
        with handoff_repository() as (root, _base, parent, result):
            report = agent_handoff.validate_document(
                handoff_document(root, parent, result),
                root,
            )
        self.assertTrue(
            report["summary"]["trusted_push_eligible"],
            report,
        )
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertEqual(report["summary"]["rejection_codes"], [])
        self.assertEqual(report["handoffs"][0]["outcome"], "accepted")
        self.assertEqual(report["handoffs"][0]["changed_lines"], 2)
        self.assertRegex(report["input_seal"], r"^[0-9a-f]{64}$")
        self.assertRegex(report["result_seal"], r"^[0-9a-f]{64}$")
    def test_unmerged_parent_contract_blocks_full_handoff(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["delivery_graph"] = delivery_graph(
                merge_status="pending",
                build_status="pending",
                watcher_process=None,
                child_status="done",
                child_candidate_sha=result,
                parent_master_sha=parent,
            )
            report = agent_handoff.validate_document(document, root)
        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertIn(
            "code-contract-not-merged",
            report["handoffs"][0]["rejection_codes"],
        )
        self.assertIn(
            "task-status-dependency-mismatch",
            report["handoffs"][0]["rejection_codes"],
        )
    def test_handoff_issue_relationship_and_task_status_are_bound(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_issue = handoff_document(root, parent, result)
            wrong_issue["handoffs"][0]["issue"] = 999
            set_history_authority(
                root,
                0,
                None,
                issue=999,
                pull_request=None,
            )
            wrong_issue[
                "history_authority"
            ] = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                999,
                None,
            )
            refresh_coordinator_receipt(wrong_issue, root)
            report = agent_handoff.validate_document(wrong_issue, root)
            self.assertIn(
                "missing-handoff-code-contract",
                report["handoffs"][0]["rejection_codes"],
            )
            blocked = handoff_document(root, parent, result)
            child_task = next(
                task
                for task in blocked["delivery_graph"]["tasks"]
                if task["id"] == "child-implement"
            )
            child_task["status"] = "blocked"
            child_task["status_reason"] = "owner_interrupted"
            report = agent_handoff.validate_document(blocked, root)
            self.assertIn(
                "handoff-task-status-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )
            duplicate_relation = handoff_document(root, parent, result)
            duplicate_relation["delivery_graph"]["relationships"].append(
                copy.deepcopy(
                    duplicate_relation["delivery_graph"]["relationships"][0]
                )
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "contains duplicates",
            ):
                agent_handoff.validate_document(duplicate_relation, root)
            duplicate_task = handoff_document(root, parent, result)
            task_copy = copy.deepcopy(
                next(
                    task
                    for task in duplicate_task["delivery_graph"]["tasks"]
                    if task["id"] == "child-implement"
                )
            )
            task_copy["id"] = "child-implement-duplicate"
            duplicate_task["delivery_graph"]["tasks"].append(task_copy)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "duplicate tasks",
            ):
                agent_handoff.validate_document(duplicate_task, root)
            missing_relation = handoff_document(root, parent, result)
            missing_relation["delivery_graph"]["relationships"] = []
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must name a code/contract dependency",
            ):
                agent_handoff.validate_document(missing_relation, root)
            missing_task = handoff_document(root, parent, result)
            missing_task["delivery_graph"]["tasks"] = [
                task
                for task in missing_task["delivery_graph"]["tasks"]
                if task["phase"] != "implementation"
            ]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unknown delivery task",
            ):
                agent_handoff.validate_document(missing_task, root)
            valid = handoff_document(root, parent, result); valid_report = agent_handoff.validate_document(valid, root)
            set_history_authority(root, 0, None, issue=179)
            issue179 = handoff_document(root, parent, result, issue=179, handoff_id="issue-179-round-1"); issue179_report = agent_handoff.validate_document(issue179, root)
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "canonical handoff issue"):
                agent_handoff.plan_history_authority(root, "example/workflow", 178, None, operation="advance", expected_object_id=valid["history_authority"]["object_id"], expected_sequence=valid["history_authority"]["sequence"], handoff_document=issue179, handoff_result=issue179_report, handoff_id="issue-179-round-1", current_time=datetime.fromisoformat(issue179["coordinator_receipt"]["issued_at"].replace("Z", "+00:00")))
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "canonical handoff pull request"):
                agent_handoff.plan_history_authority(root, "example/workflow", 178, 200, operation="advance", expected_object_id=valid["history_authority"]["object_id"], expected_sequence=valid["history_authority"]["sequence"], handoff_document=valid, handoff_result=valid_report, handoff_id="issue-178-round-1", current_time=datetime.fromisoformat(valid["coordinator_receipt"]["issued_at"].replace("Z", "+00:00")))
    def test_parent_post_merge_run_is_sha_status_and_conclusion_bound(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_sha = handoff_document(root, parent, result)
            wrong_sha["delivery_graph"]["workflow_runs"][0][
                "head_sha"
            ] = result
            report = agent_handoff.validate_document(wrong_sha, root)
            self.assertIn(
                "watcher-run-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )
            failed = handoff_document(root, parent, result)
            post_build = next(
                task
                for task in failed["delivery_graph"]["tasks"]
                if task["id"] == "parent-post-merge-build"
            )
            recovery = next(
                task
                for task in failed["delivery_graph"]["tasks"]
                if task["id"] == "parent-recovery"
            )
            post_build["status"] = "blocked"
            post_build["status_reason"] = "workflow_failed"
            recovery["status"] = "in_progress"
            run = failed["delivery_graph"]["workflow_runs"][0]
            run["status"] = "completed"
            run["conclusion"] = "failure"
            failed["delivery_graph"]["watchers"][0]["process_state"] = "error"
            refresh_coordinator_receipt(failed, root)
            report = agent_handoff.validate_document(failed, root)
            self.assertTrue(report["summary"]["trusted_push_eligible"])
            self.assertFalse(report["summary"]["delivery_eligible"])
            self.assertTrue(
                report["delivery_graph"]["relationships"][0][
                    "implementation_ready"
                ]
            )
            self.assertFalse(
                report["delivery_graph"]["parent_delivery"][0][
                    "delivery_eligible"
                ]
            )
            premature = handoff_document(root, parent, result)
            next(
                task
                for task in premature["delivery_graph"]["tasks"]
                if task["id"] == "parent-closure"
            )["status"] = "done"
            report = agent_handoff.validate_document(premature, root)
            self.assertIn(
                "task-status-dependency-mismatch",
                report["handoffs"][0]["rejection_codes"],
            )
            self.assertFalse(report["summary"]["delivery_eligible"])
    def test_cli_emits_canonical_result_and_fails_closed(self):
        with (
            handoff_repository() as (root, _base, parent, result),
            tempfile.TemporaryDirectory(
                prefix="agent-handoff-cli-",
                dir=TEST_ARTIFACTS,
            ) as fixture_directory,
        ):
            fixture_path = Path(fixture_directory) / "handoff.json"
            document = handoff_document(root, parent, result)
            fixture_path.write_text(json.dumps(document), encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "scripts.workflow_pilot.agent_handoff",
                "--fixture",
                str(fixture_path),
                "--worktree",
                str(root),
            ]
            accepted = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
            accepted_result = json.loads(accepted.stdout)
            self.assertTrue(accepted_result["summary"]["trusted_push_eligible"])
            self.assertEqual(accepted.stdout, agent_handoff.normalized_json(accepted_result))
            set_history_authority(root, 0, None, issue=179)
            fixture_path.write_text(json.dumps(handoff_document(root, parent, result, issue=179, handoff_id="issue-179-round-1")), encoding="utf-8")
            mismatch = subprocess.run(command + ["--authority-operation", "advance", "--repository", "example/workflow", "--handoff-id", "issue-179-round-1", "--issue", "178"], cwd=ROOT, check=False, capture_output=True)
            self.assertIn(b"advance planning derives issue and pull request from the fixture", mismatch.stderr)
            document["handoffs"][0]["result"]["sha"] = parent
            fixture_path.write_text(json.dumps(document), encoding="utf-8")
            rejected = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn(
                "stale-result",
                json.loads(rejected.stdout)["summary"]["rejection_codes"],
            )
            unsupported = subprocess.run(
                command + ["--history-receipt", str(fixture_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertIn(b"unrecognized arguments: --history-receipt", unsupported.stderr)
    def test_stale_wrong_parent_and_unrelated_branch_reject(self):
        with handoff_repository() as (root, base, parent, result):
            cases = {}
            stale = handoff_document(root, parent, result)
            stale["handoffs"][0]["result"]["sha"] = parent
            cases["stale"] = (stale, "stale-result")
            wrong_parent = handoff_document(root, parent, result)
            wrong_parent["handoffs"][0]["assigned_parent_sha"] = base
            cases["wrong-parent"] = (wrong_parent, "wrong-parent")
            unrelated = handoff_document(root, parent, result)
            unrelated["handoffs"][0]["expected_branch"] = "agent/unrelated"
            cases["unrelated"] = (unrelated, "unrelated-branch")
            for name, (document, code) in cases.items():
                with self.subTest(name=name):
                    report = agent_handoff.validate_document(document, root)
                    self.assertFalse(report["summary"]["trusted_push_eligible"])
                    self.assertIn(code, report["summary"]["rejection_codes"])
    def test_dirty_conflicting_missing_and_incomplete_results_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            dirty = root / "scripts" / "workflow_pilot" / "dirty.py"
            dirty.write_text("DIRTY = True\n", encoding="utf-8")
            report = agent_handoff.validate_document(
                handoff_document(root, parent, result),
                root,
            )
            self.assertIn("dirty-worktree", report["summary"]["rejection_codes"])
            dirty.unlink()
            incomplete = handoff_document(root, parent, result)
            incomplete["handoffs"][0]["states"] = [
                incomplete["handoffs"][0]["states"][0],
                incomplete["handoffs"][0]["states"][2],
            ]
            incomplete["handoffs"][0]["result"] = None
            report = agent_handoff.validate_document(incomplete, root)
            self.assertIn(
                "incomplete-lifecycle",
                report["summary"]["rejection_codes"],
            )
            missing_evidence = handoff_document(root, parent, result)
            missing_evidence["handoffs"][0]["evidence"] = []
            report = agent_handoff.validate_document(missing_evidence, root)
            self.assertIn("missing-evidence", report["summary"]["rejection_codes"])
            self.assertIn("incomplete-check", report["summary"]["rejection_codes"])
            missing_commit = handoff_document(root, parent, result)
            missing_commit["handoffs"][0]["result"]["sha"] = "f" * 40
            report = agent_handoff.validate_document(missing_commit, root)
            self.assertIn("missing-commit", report["summary"]["rejection_codes"])
    def test_executable_fsmonitor_cannot_run_or_hide_dirty_worktree(self):
        with handoff_repository() as (root, _base, parent, result):
            marker = root.parent / "fsmonitor-executed"; monitor = root.parent / "malicious-fsmonitor"
            monitor.write_text('#!/bin/sh\nprintf executed >"$1"\nexit 0\n', encoding="utf-8")
            monitor.chmod(0o700)
            git(root, "config", "core.fsmonitor", f"{monitor} {marker}")
            clean = agent_handoff.validate_document(handoff_document(root, parent, result), root)
            self.assertNotIn("dirty-worktree", clean["summary"]["rejection_codes"])
            self.assertFalse(marker.exists())
            (root / "README.md").write_text("dirty\n", encoding="utf-8")
            dirty = agent_handoff.validate_document(handoff_document(root, parent, result), root)
            self.assertIn("dirty-worktree", dirty["summary"]["rejection_codes"])
            self.assertFalse(marker.exists())
    def test_remote_rewrites_cannot_replace_installation_endpoint(self):
        with handoff_repository() as (root, _base, _parent, _result):
            temporary_clients = set(Path(tempfile.gettempdir()).glob("workflow-pilot-git-*"))
            installation = installation_root_path(root)
            ref = agent_handoff.REPOSITORY_IDENTITY_REF
            oid = agent_handoff._remote_ref_oid(root, installation, ref, allow_missing=False)
            agent_handoff._fetch_remote_authority(root, installation, ref, oid)
            head = git(root, "rev-parse", "HEAD")
            updates = [(head, agent_handoff.history_authority_ref(999, None)), (head, agent_handoff.history_anchor_ref(999))]
            agent_handoff.require_atomic_push_capability(root, installation, updates)
            remote = Path(installation_manifest(root)["authority_protection"]["remote_url"])
            substitute = root.parent / "substitute.git"
            shutil.copytree(remote, substitute)
            git(substitute, "config", "receive.advertiseAtomic", "false")
            included = root.parent / "remote-rewrite.config"
            included.write_text(f'[url "{substitute.as_uri()}"]\n\tinsteadOf = {remote}\n\tpushInsteadOf = {remote}\n[remote "origin"]\n\turl = {substitute}\n\tpushurl = {substitute}\n[remote "backup"]\n\turl = {substitute}\n[http]\n\tproxy = http://127.0.0.1:9\n\tcurloptResolve = workflow-pilot.invalid:443:127.0.0.1\n\tsslVerify = false\n\tsslCAInfo = {included}\n[http "https://workflow-pilot.invalid"]\n\tproxy = http://127.0.0.1:9\n[credential]\n\thelper = {included}\n')
            git(root, "config", "--add", "include.path", str(included))
            operations = (lambda: agent_handoff._remote_ref_oid(root, installation, ref, allow_missing=False), lambda: agent_handoff._fetch_remote_authority(root, installation, ref, oid), lambda: agent_handoff.require_atomic_push_capability(root, installation, updates))
            self.assertEqual(operations[0](), oid)
            operations[1]()
            operations[2]()
            self.assertTrue((remote.parent / "hook-ran").is_file())
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "external path"): agent_handoff.publish_authority_updates(root, installation_manifest(root), updates)
            for endpoint in ("ssh://git@github.com/example/workflow.git", "git@github.com:example/workflow.git"):
                manifest = installation_manifest(root); manifest["authority_protection"]["remote_url"] = endpoint; (installation / "installation.json").write_text(json.dumps(manifest))
                with agent_handoff._transport_capability(root, installation) as capability: self.assertEqual(capability[0], endpoint)
            common = Path(git(root, "rev-parse", "--git-common-dir")); common = common if common.is_absolute() else root / common
            objects = common / "objects"; held = common / "objects-held"; remote_held = root.parent / "authority-held"; original = agent_handoff._run_bounded_process
            def swap(**kwargs):
                if "push" not in kwargs["argv"]: return original(**kwargs)
                objects.rename(held); objects.mkdir(); remote.rename(remote_held); shutil.copytree(substitute, remote)
                try: return original(**kwargs)
                finally: shutil.rmtree(remote); remote_held.rename(remote); objects.rmdir(); held.rename(objects)
            manifest = installation_manifest(root); manifest["authority_protection"]["remote_url"] = str(remote); (installation / "installation.json").write_text(json.dumps(manifest))
            (remote.parent / "hook-ran").unlink(missing_ok=True)
            with mock.patch.object(agent_handoff, "_run_bounded_process", side_effect=swap): agent_handoff.publish_authority_updates(root, installation, updates)
            self.assertTrue((remote.parent / "hook-ran").is_file())
            linked = root.parent / "linked"; git(root, "worktree", "add", "-q", "--detach", str(linked), head)
            alternate = root.parent / "alternate.git"; alternate.mkdir(); git(alternate, "init", "-q", "--bare")
            alternate_oid = owner_create_record_commit(alternate, {"alternate": True}, "authority.json", None)
            info = common / "objects" / "info"; info.mkdir(exist_ok=True); (info / "alternates").write_text(str(alternate / "objects"))
            alternate_updates = [(alternate_oid, agent_handoff.history_authority_ref(1000, None)), (alternate_oid, agent_handoff.history_anchor_ref(1000))]
            agent_handoff.publish_authority_updates(linked, installation, alternate_updates)
            self.assertEqual(git(remote, "rev-parse", alternate_updates[0][1]), alternate_oid)
            (info / "alternates").unlink()
            helper = root.parent / "option-helper"; option_marker = root.parent / "option-executed"; helper.write_text(f'#!/bin/sh\nprintf ran > "{option_marker}"\n'); helper.chmod(0o700)
            manifest["authority_protection"]["remote_url"] = f"--upload-pack={helper}"; (installation / "installation.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "not canonical"): operations[0]()
            self.assertFalse(option_marker.exists())
            manifest = installation_manifest(root); manifest["authority_protection"]["remote_url"] = "https://github.com/example/workflow.git"; (installation / "installation.json").write_text(json.dumps(manifest))
            with self.assertRaises(agent_handoff.HandoffDataError) as failure: operations[0]()
            self.assertNotIn("127.0.0.1", str(failure.exception))
            self.assertEqual(set(Path(tempfile.gettempdir()).glob("workflow-pilot-git-*")), temporary_clients)
    def test_assignment_states_are_distinct_and_not_inferred(self):
        with handoff_repository() as (root, _base, parent, result):
            for state_name in (
                "assignment_received",
                "progressing",
                "committed",
                "handed_off",
            ):
                with self.subTest(state=state_name):
                    document = handoff_document(root, parent, result)
                    document["handoffs"][0]["states"] = [
                        state
                        for state in document["handoffs"][0]["states"]
                        if state["state"] != state_name
                    ]
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "incomplete-lifecycle",
                        report["summary"]["rejection_codes"],
                    )
            document = handoff_document(root, parent, result)
            document["handoffs"][0]["states"] = document["handoffs"][0][
                "states"
            ][1:]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must start with assignment_sent",
            ):
                agent_handoff.validate_document(document, root)
    def test_in_progress_assignment_prefixes_are_valid_but_never_eligible(self):
        with handoff_repository() as (root, _base, parent, result):
            for prefix_length in (1, 2, 3):
                with self.subTest(prefix_length=prefix_length):
                    document = handoff_document(root, parent, result)
                    handoff = document["handoffs"][0]
                    handoff["states"] = handoff["states"][:prefix_length]
                    handoff["result"] = None
                    handoff["evidence"] = []
                    handoff["required_checks"][0]["receipt_id"] = None
                    handoff["check_receipts"] = []
                    child_task = next(
                        task
                        for task in document["delivery_graph"]["tasks"]
                        if task["phase"] == "implementation"
                    )
                    child_task["candidate_sha"] = parent
                    child_task["status"] = (
                        "in_progress" if prefix_length == 3 else "pending"
                    )
                    refresh_coordinator_receipt(document, root)
                    report = agent_handoff.validate_document(document, root)
                    self.assertEqual(
                        report["handoffs"][0]["outcome"],
                        "in_progress",
                    )
                    self.assertEqual(
                        report["handoffs"][0]["rejection_codes"],
                        [],
                    )
                    self.assertFalse(
                        report["summary"]["trusted_push_eligible"]
                    )
                    self.assertFalse(report["summary"]["delivery_eligible"])
    def test_conflicting_worktree_rejects(self):
        with handoff_repository() as (root, _base, _parent, result):
            change = root / "scripts" / "workflow_pilot" / "change.py"
            git(root, "switch", "-q", "-c", "conflict-side")
            change.write_text("SIDE = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: side\n\n" + agent_handoff.COPILOT_TRAILER,
            )
            git(root, "switch", "-q", "agent/issue-178")
            change.write_text("MAIN = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: main\n\n" + agent_handoff.COPILOT_TRAILER,
            )
            main_result = git(root, "rev-parse", "HEAD")
            merge = subprocess.run(
                reporter.git_command(root, "merge", "--no-edit", "conflict-side"),
                cwd=root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(merge.returncode, 0)
            report = agent_handoff.validate_document(
                handoff_document(root, result, main_result),
                root,
            )
        self.assertIn(
            "conflicting-worktree",
            report["summary"]["rejection_codes"],
        )
    def test_missing_terminal_copilot_trailer_rejects(self):
        with handoff_repository() as (root, _base, _parent, result):
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HANDOFF = False\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(root, "commit", "-q", "-m", "fix: no trailer")
            no_trailer = git(root, "rev-parse", "HEAD")
            document = handoff_document(root, result, no_trailer)
            report = agent_handoff.validate_document(document, root)
        self.assertIn(
            "missing-copilot-trailer",
            report["summary"]["rejection_codes"],
        )
    def test_required_checks_use_closed_receipts_not_passed_labels(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted = handoff_document(root, parent, result)
            report = agent_handoff.validate_document(accepted, root)
            self.assertNotIn(
                "invalid-check-receipt",
                report["summary"]["rejection_codes"],
            )
            literal_false = handoff_document(root, parent, result)
            literal_false["handoffs"][0]["required_checks"][0][
                "contract"
            ] = "false"
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must be one of git-diff-check",
            ):
                agent_handoff.validate_document(literal_false, root)
            shell_false = handoff_document(root, parent, result)
            shell_false["handoffs"][0]["required_checks"][0][
                "command"
            ] = "false"
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unknown fields: command",
            ):
                agent_handoff.validate_document(shell_false, root)
            missing = handoff_document(root, parent, result)
            missing["handoffs"][0]["check_receipts"] = []
            report = agent_handoff.validate_document(missing, root)
            self.assertIn(
                "invalid-check-receipt",
                report["summary"]["rejection_codes"],
            )
            mutations = {
                "check_id": "wrong-check",
                "argv": ["/usr/bin/false"],
                "candidate_sha": parent,
                "worktree_identity": "0" * 64,
                "checker_trust": {
                    "mode": "external-bootstrap",
                    "sha256": "0" * 64,
                },
            }
            for field, value in mutations.items():
                with self.subTest(receipt_field=field):
                    document = handoff_document(root, parent, result)
                    receipt = document["handoffs"][0]["check_receipts"][0]
                    receipt[field] = value
                    receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "invalid-check-receipt",
                        report["summary"]["rejection_codes"],
                    )
            wrong_time = handoff_document(root, parent, result)
            receipt = wrong_time["handoffs"][0]["check_receipts"][0]
            receipt["completed_at"] = next(
                state["at"]
                for state in wrong_time["handoffs"][0]["states"]
                if state["state"] == "handed_off"
            )
            receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "follows its owner boundary",
            ):
                agent_handoff.validate_document(wrong_time, root)
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("TRAILING = True  \n", encoding="utf-8")
            (root / ".gitattributes").write_text(
                "*.py diff=hostile\n",
                encoding="utf-8",
            )
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(root, "add", ".gitattributes")
            git(
                root,
                "config",
                "core.whitespace",
                "-trailing-space",
            )
            git(root, "config", "diff.external", "/usr/bin/true")
            git(root, "config", "diff.hostile.textconv", "/usr/bin/true")
            git(root, "config", "alias.diff", "!/usr/bin/true")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: failing safe check\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            failing_result = git(root, "rev-parse", "HEAD")
            hostile_config = root / ".git" / "hostile-global"
            hostile_config.write_text(
                "[core]\n\twhitespace = -trailing-space\n"
                "[diff]\n\texternal = /usr/bin/true\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_GLOBAL": str(hostile_config),
                    "GIT_CONFIG_SYSTEM": str(hostile_config),
                    "GIT_EXTERNAL_DIFF": "/usr/bin/true",
                },
            ):
                failing = handoff_document(root, result, failing_result)
            self.assertNotEqual(
                failing["handoffs"][0]["check_receipts"][0]["exit_code"],
                0,
            )
            report = agent_handoff.validate_document(failing, root)
            self.assertIn(
                "required-check-failed",
                report["summary"]["rejection_codes"],
            )
            local_attributes = root / ".git" / "info" / "attributes"
            local_attributes.write_text(
                "*.py -text\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "local attributes file is not permitted",
            ):
                agent_handoff.execute_allowed_check(
                    receipt_id="attributes",
                    check_id="focused-module",
                    contract="git-diff-check",
                    repository_root=root,
                    parent_sha=result,
                    candidate_sha=failing_result,
                )
            local_attributes.write_text("", encoding="utf-8")
            self.assertEqual(raw_diff_check.exact_repository_root(str(root)), root)
            self.assertEqual(agent_handoff.validate_repository_root(root), root)
            local_attributes.unlink()
            os.mkfifo(local_attributes)
            with mock.patch(
                "scripts.workflow_pilot.raw_diff_check.run_git",
                side_effect=AssertionError("raw diff Git must not run"),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "local Git attributes are not permitted",
                ):
                    raw_diff_check.exact_repository_root(str(root))
            with mock.patch(
                "scripts.workflow_pilot.reporter.run_git",
                side_effect=AssertionError("reporter Git must not run"),
            ):
                with self.assertRaisesRegex(
                    agent_handoff.HandoffDataError,
                    "local attributes file is not permitted",
                ):
                    agent_handoff.validate_repository_root(root)
    def test_tracked_whitespace_attributes_cannot_disable_raw_check(self):
        cases = (
            (
                ".gitattributes",
                "*.py whitespace=-trailing-space\n",
            ),
            (
                "scripts/workflow_pilot/.gitattributes",
                "*.py whitespace=-trailing-space\n",
            ),
            (
                ".gitattributes",
                "[attr]relaxed whitespace=-trailing-space\n*.py relaxed\n",
            ),
            (
                ".gitattributes",
                "*.py -whitespace\n",
            ),
        )
        for attribute_path, attribute_text in cases:
            with self.subTest(
                attribute_path=attribute_path,
                attribute_text=attribute_text,
            ):
                with handoff_repository() as (root, _base, _parent, result):
                    path = root / attribute_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(attribute_text, encoding="utf-8")
                    change = root / "scripts" / "workflow_pilot" / "change.py"
                    change.write_text("TRACKED = True  \n", encoding="utf-8")
                    git(root, "add", attribute_path)
                    git(root, "add", "scripts/workflow_pilot/change.py")
                    git(
                        root,
                        "commit",
                        "-q",
                        "-m",
                        "test: hostile whitespace attrs\n\n"
                        + agent_handoff.COPILOT_TRAILER,
                    )
                    candidate = git(root, "rev-parse", "HEAD")
                    receipt = agent_handoff.execute_allowed_check(
                        receipt_id="tracked-attrs",
                        check_id="focused-module",
                        contract="git-diff-check",
                        repository_root=root,
                        parent_sha=result,
                        candidate_sha=candidate,
                    )
                    self.assertNotEqual(receipt["exit_code"], 0)
        with handoff_repository() as (root, _base, _parent, result):
            (root / ".gitattributes").write_text(
                "*.py whitespace=-trailing-space\n",
                encoding="utf-8",
            )
            git(root, "add", ".gitattributes")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: parent whitespace attrs\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            attribute_parent = git(root, "rev-parse", "HEAD")
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("PARENT_ATTR = True  \n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: parent attrs cannot hide whitespace\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            receipt = agent_handoff.execute_allowed_check(
                receipt_id="parent-attrs",
                check_id="focused-module",
                contract="git-diff-check",
                repository_root=root,
                parent_sha=attribute_parent,
                candidate_sha=candidate,
            )
            self.assertNotEqual(receipt["exit_code"], 0)
        with handoff_repository() as (root, _base, _parent, result):
            (root / ".gitattributes").write_text(
                "*.md text\n",
                encoding="utf-8",
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("BENIGN = True\n", encoding="utf-8")
            git(root, "add", ".gitattributes")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: benign attrs remain allowed\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            receipt = agent_handoff.execute_allowed_check(
                receipt_id="benign-attrs",
                check_id="focused-module",
                contract="git-diff-check",
                repository_root=root,
                parent_sha=result,
                candidate_sha=candidate,
            )
            self.assertEqual(receipt["exit_code"], 0)
    def test_crlf_addition_is_blank_at_eol(self):
        with handoff_repository() as (root, _base, _parent, result):
            path = root / "scripts" / "workflow_pilot" / "crlf.py"
            path.write_bytes(b"CRLF = True\r\n")
            git(root, "add", str(path.relative_to(root)))
            git(root, "commit", "-q", "-m", "test: CRLF\n\n" + agent_handoff.COPILOT_TRAILER)
            candidate = git(root, "rev-parse", "HEAD")
            self.assertEqual(raw_diff_check.raw_diff_errors(root, result, candidate), ["scripts/workflow_pilot/crlf.py:1: blank-at-eol"])
    def test_scope_line_resource_protocol_lifetime_and_rss_budgets_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            cases = {}
            lines = handoff_document(root, parent, result)
            lines["handoffs"][0]["budgets"]["changed_lines"] = 1
            cases["lines"] = (lines, "changed-lines-budget-exceeded")
            scope = handoff_document(root, parent, result)
            scope["handoffs"][0]["allowed_scope"] = ["docs/"]
            cases["scope"] = (scope, "scope-violation")
            lifetime = handoff_document(root, parent, result)
            lifetime["handoffs"][0]["max_lifetime_seconds"] = 1
            cases["lifetime"] = (lifetime, "owner-lifetime-exceeded")
            rss = handoff_document(root, parent, result)
            rss["handoffs"][0]["max_peak_rss_bytes"] = 1
            cases["rss"] = (rss, "owner-rss-exceeded")
            for name, (document, code) in cases.items():
                with self.subTest(name=name):
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(code, report["summary"]["rejection_codes"])
    def test_fractional_runtime_telemetry_must_resolve_to_whole_seconds(self):
        with handoff_repository() as (root, _base, parent, result):
            exact = handoff_document(root, parent, result)
            exact["handoffs"][0]["max_lifetime_seconds"] = 1
            retime_handoff(
                exact,
                assignment_sent=(
                    datetime.now(timezone.utc).replace(microsecond=100000)
                    - timedelta(minutes=10)
                ),
                state_offsets=[
                    timedelta(seconds=0),
                    timedelta(milliseconds=100),
                    timedelta(milliseconds=200),
                    timedelta(milliseconds=900),
                    timedelta(seconds=1),
                ],
                evidence_offset=timedelta(milliseconds=600),
                receipt_offsets=(
                    timedelta(milliseconds=300),
                    timedelta(milliseconds=700),
                ),
            )
            refresh_coordinator_receipt(exact, root)
            exact_report = agent_handoff.validate_document(exact, root)
            self.assertTrue(exact_report["summary"]["trusted_push_eligible"])
            self.assertEqual(exact_report["handoffs"][0]["lifetime_seconds"], 1)
            self.assertEqual(
                agent_handoff.make_history_receipt(exact, exact_report, "issue-178-round-1")["closed_at"],
                exact_report["handoffs"][0]["closed_at"],
            )
            fractional_cases = {
                "fractional-span": (
                    datetime.now(timezone.utc).replace(microsecond=100000)
                    - timedelta(minutes=8),
                    [
                        timedelta(seconds=0),
                        timedelta(milliseconds=100),
                        timedelta(milliseconds=200),
                        timedelta(seconds=1, milliseconds=800),
                        timedelta(seconds=1, milliseconds=900),
                    ],
                ),
                "fractional-endpoint": (
                    datetime.now(timezone.utc).replace(microsecond=0)
                    - timedelta(minutes=6),
                    [
                        timedelta(seconds=0),
                        timedelta(milliseconds=100),
                        timedelta(milliseconds=200),
                        timedelta(seconds=1),
                        timedelta(seconds=1, milliseconds=100),
                    ],
                ),
            }
            for name, (assignment_sent, offsets) in fractional_cases.items():
                with self.subTest(name=name):
                    document = handoff_document(root, parent, result)
                    document["handoffs"][0]["max_lifetime_seconds"] = 1
                    retime_handoff(
                        document,
                        assignment_sent=assignment_sent,
                        state_offsets=offsets,
                        evidence_offset=timedelta(milliseconds=600),
                        receipt_offsets=(
                            timedelta(milliseconds=300),
                            timedelta(milliseconds=700),
                        ),
                    )
                    refresh_coordinator_receipt(document, root)
                    report = agent_handoff.validate_document(document, root)
                    self.assertFalse(report["summary"]["trusted_push_eligible"])
                    self.assertIn(
                        "invalid-runtime-telemetry",
                        report["summary"]["rejection_codes"],
                    )
                    self.assertNotIn(
                        "owner-lifetime-exceeded",
                        report["summary"]["rejection_codes"],
                    )
                    self.assertEqual(report["handoffs"][0]["lifetime_seconds"], 0)
    def test_duplicate_owner_and_watcher_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            duplicate_owner = handoff_document(root, parent, result)
            second = copy.deepcopy(duplicate_owner["handoffs"][0])
            second["id"] = "issue-178-round-2"
            duplicate_owner["handoffs"].append(second)
            owner_report = agent_handoff.validate_document(duplicate_owner, root)
            self.assertIn(
                "duplicate-owner",
                owner_report["summary"]["rejection_codes"],
            )
            duplicate_coordinator = handoff_document(root, parent, result)
            second_coordinator = copy.deepcopy(
                duplicate_coordinator["coordinators"][0]
            )
            second_coordinator["id"] = "coordinator-2"
            duplicate_coordinator["coordinators"].append(second_coordinator)
            coordinator_report = agent_handoff.validate_document(
                duplicate_coordinator,
                root,
            )
            self.assertIn(
                "duplicate-coordinator",
                coordinator_report["summary"]["rejection_codes"],
            )
            duplicate_watcher = handoff_document(root, parent, result)
            add_run(duplicate_watcher, result)
            second_watcher = copy.deepcopy(duplicate_watcher["watchers"][0])
            second_watcher["id"] = "watcher-9001-duplicate"
            duplicate_watcher["watchers"].append(second_watcher)
            watcher_report = agent_handoff.validate_document(
                duplicate_watcher,
                root,
            )
            self.assertIn(
                "duplicate-watcher",
                watcher_report["summary"]["rejection_codes"],
            )
            missing_watcher = handoff_document(root, parent, result)
            add_run(missing_watcher, result)
            missing_watcher["watchers"] = []
            sign_coordinator_document(missing_watcher, root)
            for code, document in {
                "duplicate-watcher": duplicate_watcher,
                "missing-or-duplicate-watcher": missing_watcher,
            }.items():
                validation_time = datetime.fromisoformat(
                    document["coordinator_receipt"]["issued_at"].replace(
                        "Z",
                        "+00:00",
                    )
                )
                with self.subTest(watcher_code=code), self.assertRaisesRegex(
                    agent_handoff.HandoffDataError,
                    "no closed result to seal",
                ):
                    report = agent_handoff.validate_document(
                        document,
                        root,
                        current_time=validation_time,
                    )
                    self.assertIn(code, report["summary"]["rejection_codes"])
                    agent_handoff.make_history_receipt(
                        document,
                        report,
                        "issue-178-round-1",
                    )
                current = document["history_authority"]
                report = agent_handoff.validate_document(
                    document,
                    root,
                    current_time=validation_time,
                )
                with self.assertRaisesRegex(agent_handoff.HandoffDataError, "canonical validation output|no closed result to seal"):
                    agent_handoff.plan_history_authority(root, "example/workflow", 178, None, operation="advance", expected_object_id=current["object_id"], expected_sequence=current["sequence"], handoff_document=document, handoff_result=report, handoff_id="issue-178-round-1")
                with self.assertRaisesRegex(agent_handoff.HandoffDataError, "canonical validation output|no closed result to seal"):
                    set_history_authority(root, 1, document=document, result=report)
    def test_implementation_owner_remote_actions_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            identities = (
                ("owner-1", 101, "Owner-1", 101),
                ("Build-Bot[bot]", 102, "build-bot[BOT]", 102),
            )
            for owner_login, owner_id, actor_login, actor_id in identities:
                with self.subTest(owner=owner_login, actor=actor_login):
                    document = handoff_document(root, parent, result)
                    document["handoffs"][0]["owner_id"] = owner_login
                    document["handoffs"][0]["owner_database_id"] = owner_id
                    action = {
                        "id": "remote:push",
                        "handoff_id": "issue-178-round-1",
                        "actor_login": actor_login,
                        "actor_database_id": actor_id,
                        "action": "push",
                        "occurred_at": datetime.now(timezone.utc)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "source": "github-timeline",
                    }
                    refresh_coordinator_receipt(
                        document,
                        root,
                        actions=[action],
                    )
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "implementation-owner-remote-action",
                        report["summary"]["rejection_codes"],
                    )
    def test_stable_issue_authority_binds_pr_and_rejects_aba(self):
        with handoff_repository() as (root, _base, parent, first_result):
            first = handoff_document(root, parent, first_result)
            shift_handoff_times(first, -60)
            refresh_coordinator_receipt(first, root)
            first_report = agent_handoff.validate_document(first, root)
            genesis, first_receipt, plan = plan_advance_authority(root, first, first_report)
            self.assertIn("publish_authority_updates", plan["atomic_push"])
            self.assertNotIn("git push", plan["atomic_push"])
            self.assertNotIn(" origin ", plan["atomic_push"])
            self.assertNotIn("--force", plan["atomic_push"])
            set_history_authority(
                root,
                1,
                document=first,
                result=first_report,
            )
            bound = bind_history_authority(root)
            self.assertEqual(
                bound["ref"],
                agent_handoff.history_authority_ref(178, None),
            )
            self.assertEqual(bound["handoff_sequence"], 1)
            self.assertEqual(bound["pr_binding"]["pull_request"], 200)
            self.assertEqual(bound["pr_binding"]["base_branch"], "master")
            self.assertEqual(
                bound["pr_binding"]["head_branch"],
                "agent/issue-178",
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HANDOFF = 'rebind'\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: attempted second root\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            second_result = git(root, "rev-parse", "HEAD")
            reused = handoff_document(root, first_result, second_result)
            reused["prior_handoffs"] = [first_receipt]
            reused["handoffs"][0]["id"] = "issue-178-rebound-root"
            reused["handoffs"][0]["owner_id"] = "owner-1"
            reused["handoffs"][0]["owner_database_id"] = 101
            reused["handoffs"][0]["handoff_kind"] = "review_successor"
            reused["handoffs"][0]["replaces_handoff_id"] = (
                "issue-178-round-1"
            )
            relationship = reused["delivery_graph"]["relationships"][0]
            relationship["handoff_id"] = "issue-178-rebound-root"
            task = next(
                item
                for item in reused["delivery_graph"]["tasks"]
                if item["phase"] == "implementation"
            )
            task["handoff_id"] = "issue-178-rebound-root"
            refresh_coordinator_receipt(reused, root)
            reused_report = agent_handoff.validate_document(reused, root)
            self.assertIn(
                "closed-owner-reused",
                reused_report["summary"]["rejection_codes"],
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "immutable",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    201,
                    operation="bind",
                    expected_object_id=bound["object_id"],
                    expected_sequence=bound["sequence"],
                    pull_request_observation=pull_request_observation(
                        root,
                        bound,
                        pull_request=201,
                    ),
                    publication_attestation=publication_attestation(
                        root,
                        bound["object_id"],
                        bound["anchor_object_id"],
                    ),
                )
            remote = Path(git(root, "remote", "get-url", "origin"))
            transaction_hook = remote / "hooks" / "reference-transaction"
            transaction_hook.chmod(0o600)
            git(
                remote,
                "update-ref",
                bound["ref"],
                genesis["object_id"],
            )
            transaction_hook.chmod(0o700)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "independent authority anchor",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                )
            transaction_hook.chmod(0o600)
            git(remote, "update-ref", bound["ref"], bound["object_id"])
            transaction_hook.chmod(0o700)
            replay = subprocess.run(
                reporter.git_command(
                    AUTHORITY_OWNERS[str(root)],
                    "push",
                    "--force",
                    "origin",
                    f"{genesis['object_id']}:{bound['ref']}",
                ),
                cwd=AUTHORITY_OWNERS[str(root)],
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(replay.returncode, 0)
    def test_ruleset_response_is_exact_and_has_no_unexpected_bypass(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted = handoff_document(root, parent, result)
            self.assertTrue(
                agent_handoff.validate_document(accepted, root)[
                    "summary"
                ]["trusted_push_eligible"]
            )
            self.assertEqual(
                accepted["coordinator_receipt"]["authority_protection"][
                    "response"
                ]["bypass_actors"],
                [
                    {
                        "actor_type": "User",
                        "actor_id": 9001,
                        "database_id": 9001,
                        "bypass_mode": "always",
                    }
                ],
            )
            mutations = {
                "unrelated-id": lambda response: response.update(id=78),
                "wrong-include": lambda response: response[
                    "include_refs"
                ].pop(),
                "inactive": lambda response: response.update(
                    enforcement="evaluate"
                ),
                "update-open": lambda response: response.update(
                    update_restricted=False
                ),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    document = handoff_document(root, parent, result)
                    mutate(
                        document["coordinator_receipt"][
                            "authority_protection"
                        ]["response"]
                    )
                    sign_coordinator_document(document, root)
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "unrelated or ineffective",
                    ):
                        agent_handoff.validate_document(document, root)
            extra_bypass = handoff_document(root, parent, result)
            extra_bypass["coordinator_receipt"]["authority_protection"][
                "response"
            ]["bypass_actors"].append(
                {
                    "actor_type": "RepositoryRole",
                    "actor_id": 5,
                    "bypass_mode": "always",
                }
            )
            sign_coordinator_document(extra_bypass, root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unauthorized typed bypass",
            ):
                agent_handoff.validate_document(extra_bypass, root)
    def test_bare_remote_installation_requires_force_push_and_deletion_disabled(self):
        with handoff_repository() as (root, _base, _parent, _result):
            self.assertEqual(
                agent_handoff.load_coordinator_installation(root)[
                    "authority_protection"
                ]["mode"],
                "bare-remote-config",
            )
            installation_path = installation_root_path(root) / "installation.json"
            baseline = installation_manifest(root)
            for field, pattern in (
                ("force_pushes_allowed", "must reject force pushes"),
                ("deletions_allowed", "must reject deletions"),
            ):
                with self.subTest(field=field):
                    mutated = copy.deepcopy(baseline)
                    mutated["authority_protection"][field] = True
                    installation_path.write_text(
                        json.dumps(mutated),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        pattern,
                    ):
                        agent_handoff.load_coordinator_installation(root)
                    installation_path.write_text(
                        json.dumps(baseline),
                        encoding="utf-8",
                    )
            current = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            base_document = handoff_document(root, _parent, _result)
            base_result = agent_handoff.validate_document(
                base_document,
                root,
            )
            base_history = agent_handoff.make_history_receipt(
                base_document,
                base_result,
                "issue-178-round-1",
            )
            unrelated = authority_publication(
                root,
                current,
                operation="advance",
                history_receipt=base_history,
            )
            unrelated["ruleset_response"]["id"] = 78
            unrelated["signature"] = external_sign(
                root,
                agent_handoff.signed_record_payload(
                    agent_handoff.PUBLICATION_ATTESTATION_DOMAIN,
                    unrelated,
                ),
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unrelated or incomplete",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                    operation="advance",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    handoff_document=base_document,
                    handoff_result=base_result,
                    handoff_id="issue-178-round-1",
                    publication_attestation=unrelated,
                )
    def test_trusted_installation_members_stay_external_and_race_free(self):
        with handoff_repository() as (root, _base, _parent, _result):
            source_installation = installation_root_path(root)
            def install_case(name):
                target = source_installation.parent / name
                shutil.copytree(source_installation, target)
                manifest = json.loads(
                    (target / "installation.json").read_text(encoding="utf-8")
                )
                manifest["bootstrap_validator"]["path"] = str(
                    target / "raw_diff_check.py"
                )
                (target / "installation.json").write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )
                return target
            positive = agent_handoff.load_coordinator_installation(
                root,
                source_installation,
            )
            self.assertEqual(
                hashlib.sha256(positive["_bootstrap_validator_source"]).hexdigest(),
                hashlib.sha256(
                    (source_installation / "raw_diff_check.py").read_bytes()
                ).hexdigest(),
            )
            manifest_link = install_case("manifest-link")
            candidate_manifest = root / "candidate-installation.json"
            candidate_manifest.write_text(
                json.dumps(installation_manifest(root)),
                encoding="utf-8",
            )
            (manifest_link / "installation.json").unlink()
            os.symlink(candidate_manifest, manifest_link / "installation.json")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "regular file",
            ):
                agent_handoff.load_coordinator_installation(root, manifest_link)
            real_parent = source_installation.parent / "real-parent"
            real_parent.mkdir()
            parent_symlink_install = real_parent / "coordinator-installation"
            shutil.copytree(source_installation, parent_symlink_install)
            parent_manifest = json.loads(
                (parent_symlink_install / "installation.json").read_text(
                    encoding="utf-8"
                )
            )
            parent_manifest["bootstrap_validator"]["path"] = str(
                parent_symlink_install / "raw_diff_check.py"
            )
            (parent_symlink_install / "installation.json").write_text(
                json.dumps(parent_manifest),
                encoding="utf-8",
            )
            symlink_parent = source_installation.parent / "linked-parent"
            os.symlink(real_parent, symlink_parent)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must be a directory",
            ):
                agent_handoff.load_coordinator_installation(
                    root,
                    symlink_parent / "coordinator-installation",
                )
            outside_validator = install_case("outside-validator")
            outside_manifest = json.loads(
                (outside_validator / "installation.json").read_text(
                    encoding="utf-8"
                )
            )
            external_validator = source_installation.parent / "external.py"
            external_validator.write_bytes(
                (outside_validator / "raw_diff_check.py").read_bytes()
            )
            outside_manifest["bootstrap_validator"]["path"] = str(
                external_validator
            )
            (outside_validator / "installation.json").write_text(
                json.dumps(outside_manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "rooted under the coordinator installation",
            ):
                agent_handoff.load_coordinator_installation(
                    root,
                    outside_validator,
                )
            validator_link = install_case("validator-link")
            candidate_validator = root / "candidate-validator.py"
            candidate_validator.write_bytes(
                (validator_link / "raw_diff_check.py").read_bytes()
            )
            (validator_link / "raw_diff_check.py").unlink()
            os.symlink(candidate_validator, validator_link / "raw_diff_check.py")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "regular file",
            ):
                agent_handoff.load_coordinator_installation(root, validator_link)
            race_install = install_case("swap-race")
            replacement_manifest = installation_manifest(root)
            replacement_manifest["repository"] = "example/other"
            real_open = os.open
            swapped = False
            def swap_on_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                if path == "installation.json" and not swapped:
                    swapped = True
                    replacement_path = race_install / "replacement.json"
                    replacement_path.write_text(
                        json.dumps(replacement_manifest),
                        encoding="utf-8",
                    )
                    os.replace(
                        replacement_path,
                        race_install / "installation.json",
                    )
                return real_open(path, flags, mode, dir_fd=dir_fd)
            with mock.patch("scripts.workflow_pilot.agent_handoff.os.open", side_effect=swap_on_open):
                with self.assertRaisesRegex(
                    agent_handoff.HandoffDataError,
                    "changed before read",
                ):
                    agent_handoff.load_coordinator_installation(root, race_install)
            fifo_install = install_case("validator-fifo")
            (fifo_install / "raw_diff_check.py").unlink()
            os.mkfifo(fifo_install / "raw_diff_check.py")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "regular file",
            ):
                agent_handoff.load_coordinator_installation(root, fifo_install)
            hardlink_install = install_case("validator-hardlink")
            source_validator = hardlink_install / "validator-source.py"
            source_validator.write_bytes(
                (hardlink_install / "raw_diff_check.py").read_bytes()
            )
            (hardlink_install / "raw_diff_check.py").unlink()
            os.link(
                source_validator,
                hardlink_install / "raw_diff_check.py",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must not be hardlinked",
            ):
                agent_handoff.load_coordinator_installation(
                    root,
                    hardlink_install,
                )
    def test_multiple_authorized_coordinators_allow_one_actor_and_freeze_the_set(self):
        with handoff_repository(
            authorized_coordinators=[
                {"login": "coordinator", "database_id": 9001},
                {"login": "backup-coordinator", "database_id": 9002},
            ]
        ) as (root, _base, parent, result):
            backup = handoff_document(root, parent, result)
            backup["coordinators"][0] = {
                "id": "coordinator-2",
                "login": "backup-coordinator",
                "database_id": 9002,
            }
            refresh_coordinator_receipt(backup, root)
            backup_report = agent_handoff.validate_document(backup, root)
            self.assertTrue(backup_report["summary"]["trusted_push_eligible"])
            self.assertEqual(
                sorted(
                    actor["database_id"]
                    for actor in backup["coordinator_receipt"][
                        "authority_protection"
                    ]["response"]["bypass_actors"]
                ),
                [9001, 9002],
            )
            unauthorized = handoff_document(root, parent, result)
            unauthorized["coordinators"][0] = {
                "id": "coordinator-x",
                "login": "intruder",
                "database_id": 9999,
            }
            refresh_coordinator_receipt(unauthorized, root)
            unauthorized_report = agent_handoff.validate_document(
                unauthorized,
                root,
            )
            self.assertIn(
                "coordinator-actor-unauthorized",
                unauthorized_report["summary"]["rejection_codes"],
            )
            set_history_authority(
                root,
                1,
                document=backup,
                result=backup_report,
                handoff_id=backup["handoffs"][0]["id"],
            )
            for name, mutate in {
                "missing": lambda response: response["bypass_actors"].pop(),
                "extra": lambda response: response["bypass_actors"].append(
                    {
                        "actor_type": "User",
                        "actor_id": 9003,
                        "database_id": 9003,
                        "bypass_mode": "always",
                    }
                ),
            }.items():
                with self.subTest(name=name):
                    document = handoff_document(root, parent, result)
                    mutate(
                        document["coordinator_receipt"][
                            "authority_protection"
                        ]["response"]
                    )
                    sign_coordinator_document(document, root)
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "bypass actors do not match frozen authority",
                    ):
                        agent_handoff.validate_document(document, root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "authorized coordinator",
            ):
                bind_history_authority(root, coordinator_database_id=9999)
            bound = bind_history_authority(root, coordinator_database_id=9002)
            self.assertEqual(bound["pr_binding"]["coordinator_database_id"], 9002)
    def test_atomic_publication_rejects_split_and_stale_coordinators(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            report = agent_handoff.validate_document(document, root)
            current, history, plan = plan_advance_authority(root, document, report)
            owner = AUTHORITY_OWNERS[str(root)]
            def commits(message):
                authority_id = owner_create_record_commit(
                    owner,
                    plan["record"],
                    "authority.json",
                    current["object_id"],
                    message=message,
                )
                anchor = copy.deepcopy(plan["anchor_record_template"])
                anchor["authority_object_id"] = authority_id
                anchor_id = owner_create_record_commit(
                    owner,
                    anchor,
                    "anchor.json",
                    current["anchor_object_id"],
                    message=message,
                )
                return authority_id, anchor_id
            first_authority, first_anchor = commits(b"coordinator one\n")
            second_authority, second_anchor = commits(b"coordinator two\n")
            git(
                owner,
                "push",
                "-q",
                "--atomic",
                "origin",
                f"{first_authority}:{plan['ref']}",
                f"{first_anchor}:{plan['anchor_ref']}",
            )
            stale = subprocess.run(
                reporter.git_command(
                    owner,
                    "push",
                    "--atomic",
                    "origin",
                    f"{second_authority}:{plan['ref']}",
                    f"{second_anchor}:{plan['anchor_ref']}",
                ),
                cwd=owner,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(
                git(root, "ls-remote", "--refs", "origin", plan["ref"]).split()[0],
                first_authority,
            )
            split = subprocess.run(
                reporter.git_command(
                    owner,
                    "push",
                    "origin",
                    f"{second_authority}:{plan['ref']}",
                ),
                cwd=owner,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(split.returncode, 0)
            recovered = advance_history_authority(root)
            self.assertEqual(recovered["sequence"], 2)
        with handoff_repository() as (root, _base, _parent, _result):
            remote = Path(git(root, "remote", "get-url", "origin"))
            git(remote, "config", "receive.advertiseAtomic", "false")
            current = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            document = handoff_document(root, _parent, _result)
            report = agent_handoff.validate_document(document, root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not support the required atomic",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                    operation="advance",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    handoff_document=document,
                    handoff_result=report,
                    handoff_id="issue-178-round-1",
                )
    def test_external_attestation_binds_every_eligibility_input(self):
        with handoff_repository() as (root, _base, parent, result):
            installation = COORDINATOR_INSTALLATIONS[str(root)]
            self.assertFalse((installation / "receipt.key").exists())
            self.assertFalse((installation.parent / "external-signer" / "private.pem").exists())
            document = handoff_document(root, parent, result)
            mutations = (
                lambda value: value["handoffs"][0]["allowed_scope"].append(
                    "src/"
                ),
                lambda value: value["delivery_graph"]["workflow_runs"][0].update(
                    status="completed",
                    conclusion="success",
                ),
                lambda value: value["delivery_graph"]["watchers"][0].update(
                    process_state="completed"
                ),
            )
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    changed = copy.deepcopy(document)
                    mutate(changed)
                    report = agent_handoff.validate_document(changed, root)
                    self.assertIn(
                        "invalid-coordinator-attestation",
                        report["summary"]["rejection_codes"],
                    )
                    with self.assertRaisesRegex(agent_handoff.HandoffDataError, "no closed result to seal"):
                        agent_handoff.make_history_receipt(changed, report, "issue-178-round-1")
            absent = copy.deepcopy(document)
            del absent["coordinator_receipt"]["signature"]
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "missing fields: signature",
            ):
                agent_handoff.validate_document(absent, root)
            current = document["history_authority"]
            valid_result = agent_handoff.validate_document(
                document,
                root,
                current_time=datetime.fromisoformat(
                    document["coordinator_receipt"]["issued_at"].replace("Z", "+00:00")
                ),
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "requires external coordinator attestation",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                    operation="advance",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    handoff_document=document,
                    handoff_result=valid_result,
                    handoff_id="issue-178-round-1",
                    current_time=datetime.fromisoformat(
                        document["coordinator_receipt"]["issued_at"].replace(
                            "Z",
                            "+00:00",
                        )
                    ),
                )
    def test_signed_fields_require_canonical_base64_text(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["coordinator_receipt"]["signature"] = noncanonical_base64_alias(
                document["coordinator_receipt"]["signature"]
            )
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "invalid-coordinator-attestation",
                report["summary"]["rejection_codes"],
            )
            record_document = handoff_document(root, parent, result)
            record_result = agent_handoff.validate_document(record_document, root)
            record = reporter_record(
                root,
                record_document,
                record_result,
            )
            record["result_attestation"]["signature"] = noncanonical_base64_alias(
                record["result_attestation"]["signature"]
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                verify_reporter_record_offline(record)
            _, _, current, _, _ = protected_root_authority(root, parent, result)
            publish_bound_authority(root, current)
            bound = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            publication = copy.deepcopy(bound["publication_attestation"])
            publication["signature"] = noncanonical_base64_alias(
                publication["signature"]
            )
            self.assertEqual(
                base64.b64decode(publication["signature"], validate=True),
                base64.b64decode(
                    bound["publication_attestation"]["signature"],
                    validate=True,
                ),
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                agent_handoff.parse_publication_attestation(
                    publication,
                    signer=bound["signer"],
                    repository="example/workflow",
                    repository_database_id=7001,
                    issue=178,
                    authority_ref=bound["ref"],
                    anchor_ref=bound["anchor_ref"],
                    authority_object_id=bound["previous_object_id"],
                    anchor_object_id=bound["publication_attestation"][
                        "anchor_object_id"
                    ],
                    ruleset_id=bound["ruleset_id"],
                    authorized_bypass_actors=bound["authorized_bypass_actors"],
                )
            observation = copy.deepcopy(bound["pr_binding"])
            observation["signature"] = noncanonical_base64_alias(
                observation["signature"]
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                agent_handoff.parse_pull_request_observation(
                    observation,
                    signer=bound["signer"],
                    repository="example/workflow",
                    repository_database_id=7001,
                    authority_object_id=bound["pr_binding"][
                        "authority_object_id"
                    ],
                    anchor_object_id=bound["pr_binding"]["anchor_object_id"],
                )
    def test_interruption_snapshot_content_base64_must_be_canonical(self):
        with handoff_repository() as (root, _base, _parent, result):
            interrupted = interrupted_handoff_document(
                root,
                result,
            )
            canonical_file = interrupted["coordinator_receipt"][
                "runtime_telemetry"
            ][0]["interruption_snapshot"]["files"][0]
            self.assertEqual(canonical_file["content_base64"], "VEVTVA==")
            interrupted_report = agent_handoff.validate_document(
                interrupted,
                root,
            )
            self.assertEqual(
                interrupted_report["handoffs"][0]["outcome"],
                "interrupted",
            )
            interrupted_record = reporter_record(
                root,
                interrupted,
                interrupted_report,
            )
            self.assertEqual(
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(interrupted_record)
                )["implementation_handoffs"]["issue-178-round-1"][
                    "reported_outcome"
                ],
                "interrupted",
            )

            aliased_document = interrupted_handoff_document(root, result)
            aliased_file = aliased_document["coordinator_receipt"][
                "runtime_telemetry"
            ][0]["interruption_snapshot"]["files"][0]
            aliased_file["content_base64"] = noncanonical_base64_alias(
                aliased_file["content_base64"]
            )
            sign_coordinator_document(aliased_document, root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                agent_handoff.validate_document(aliased_document, root)

            history = agent_handoff.make_history_receipt(
                interrupted,
                interrupted_report,
                "issue-178-round-1",
            )
            agent_handoff.validate_prior_handoffs([history])
            aliased_history = copy.deepcopy(history)
            aliased_history["interruption_snapshot"]["files"][0][
                "content_base64"
            ] = noncanonical_base64_alias(
                aliased_history["interruption_snapshot"]["files"][0][
                    "content_base64"
                ]
            )
            aliased_history["seal"] = agent_handoff.seal_history_receipt(
                aliased_history
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                agent_handoff.validate_prior_handoffs([aliased_history])

        with handoff_repository() as (root, _base, _parent, result):
            interrupted = interrupted_handoff_document(root, result)
            interrupted_report = agent_handoff.validate_document(
                interrupted,
                root,
            )
            forged_result = copy.deepcopy(interrupted_report)
            forged_result["handoffs"][0]["interruption_snapshot"]["files"][0][
                "content_base64"
            ] = noncanonical_base64_alias(
                forged_result["handoffs"][0]["interruption_snapshot"]["files"][0][
                    "content_base64"
                ]
            )
            forged_result["result_seal"] = agent_handoff.seal_handoff_result(
                forged_result
            )
            forged_record = {
                "source_handoff_ids": sorted(
                    item["id"] for item in interrupted["handoffs"]
                ),
                "document": copy.deepcopy(interrupted),
                "input_seal": forged_result["input_seal"],
                "git_seal": forged_result["git_seal"],
                "result_seal": forged_result["result_seal"],
                "result": forged_result,
                "result_attestation": finalize_result_attestation(
                    root,
                    interrupted,
                    forged_result,
                ),
            }
            remember_reporter_trust(
                forged_record,
                trusted_reporter_anchor(
                    root,
                    interrupted,
                    forged_record["input_seal"],
                ),
                trusted_reporter_installation(root),
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "canonical base64",
            ):
                verify_reporter_record_offline(forged_record)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "canonical base64",
            ):
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(forged_record)
                )
        with handoff_repository() as (root, _base, _parent, result):
            empty_snapshot = interrupted_handoff_document(
                root,
                result,
                content=b"",
            )
            empty_file = empty_snapshot["coordinator_receipt"][
                "runtime_telemetry"
            ][0]["interruption_snapshot"]["files"][0]
            self.assertEqual(empty_file["content_base64"], "")
            self.assertEqual(
                empty_file["sha256"],
                hashlib.sha256(b"").hexdigest(),
            )
            empty_report = agent_handoff.validate_document(
                empty_snapshot,
                root,
            )
            self.assertEqual(
                empty_report["handoffs"][0]["outcome"],
                "interrupted",
            )
            empty_history = agent_handoff.make_history_receipt(
                empty_snapshot,
                empty_report,
                "issue-178-round-1",
            )
            agent_handoff.validate_prior_handoffs([empty_history])
            empty_record = reporter_record(root, empty_snapshot, empty_report)
            self.assertEqual(
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(empty_record)
                )["implementation_handoffs"]["issue-178-round-1"][
                    "reported_outcome"
                ],
                "interrupted",
            )
            empty_signature = handoff_document(root, result, result)
            empty_signature["coordinator_receipt"]["signature"] = ""
            report = agent_handoff.validate_document(empty_signature, root)
            self.assertIn(
                "invalid-coordinator-attestation",
                report["summary"]["rejection_codes"],
            )
    def test_verify_external_signature_rejects_same_width_representatives_ge_modulus(self):
        with handoff_repository() as (root, _base, _parent, _result):
            signer, payload, signature_text, alias_text = (
                signature_plus_modulus_alias(
                    root,
                    b"workflow-pilot-modulus-alias:",
                )
            )
            agent_handoff.verify_external_signature(
                signer,
                payload,
                signature_text,
                "signature",
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not verify",
            ):
                agent_handoff.verify_external_signature(
                    signer,
                    payload,
                    alias_text,
                    "signature",
                )
    def test_pr_binding_requires_exact_signed_api_response(self):
        with handoff_repository() as (root, _base, parent, result):
            _, _, current, _, _ = protected_root_authority(root, parent, result)
            for field, value, message in (
                ("repository_id", 999, "repository mismatch"),
                ("repository_full_name", "fork/repo", "repository mismatch"),
                ("head_repository_full_name", "fork/repo", "repository mismatch"),
                ("base_branch", "agent/issue-178", "frozen delivery inputs"),
                ("base_oid", "e" * 40, "live repository state"),
                ("head_oid", "f" * 40, "frozen delivery inputs"),
                ("delivery_branch", "other", "handoff/delivery branch"),
                ("state", "CLOSED", "OPEN and unmerged"),
                ("merged", True, "OPEN and unmerged"),
            ):
                with self.subTest(field=field):
                    observation = pull_request_observation(root, current)
                    observation[field] = value
                    observation["signature"] = external_sign(
                        root,
                        agent_handoff.signed_record_payload(
                            agent_handoff.PR_OBSERVATION_DOMAIN,
                            observation,
                        ),
                    )
                    publication = authority_publication(root, current, operation="bind", pr_observation=observation)
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        message,
                    ):
                        agent_handoff.plan_history_authority(
                            root,
                            "example/workflow",
                            178,
                            200,
                            operation="bind",
                            expected_object_id=current["object_id"],
                            expected_sequence=current["sequence"],
                            pull_request_observation=observation,
                            publication_attestation=publication,
                        )
            invented = pull_request_observation(root, current)
            invented["pull_request"] = 201
            publication = authority_publication(root, current, operation="bind", pr_observation=invented)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not verify|wrong pull request",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    201,
                    operation="bind",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    pull_request_observation=invented,
                    publication_attestation=publication,
                )
    def test_coordinator_receipt_requires_current_validation_time(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            issued_at = datetime.now(timezone.utc).replace(
                microsecond=0
            ) - timedelta(milliseconds=500)
            set_coordinator_receipt_time(document, iso_utc(issued_at))
            resign_coordinator_receipt(document, root)
            accepted = agent_handoff.validate_document(
                document,
                root,
                current_time=issued_at + timedelta(microseconds=250000),
            )
            history = agent_handoff.make_history_receipt(
                document,
                accepted,
                "issue-178-round-1",
            )
            future = agent_handoff.validate_document(
                document,
                root,
                current_time=issued_at - timedelta(microseconds=1),
            )
        self.assertTrue(accepted["summary"]["trusted_push_eligible"])
        self.assertEqual(history["input_seal"], accepted["input_seal"])
        self.assertNotIn(
            "invalid-coordinator-attestation",
            accepted["summary"]["rejection_codes"],
        )
        self.assertFalse(future["summary"]["trusted_push_eligible"])
        self.assertIn(
            "invalid-coordinator-attestation",
            future["summary"]["rejection_codes"],
        )
    def test_plan_history_authority_uses_live_current_time_for_receipts(self):
        with handoff_repository() as (root, _base, parent, result):
            fresh = handoff_document(root, parent, result)
            fresh_report = agent_handoff.validate_document(fresh, root)
            _current, fresh_history, fresh_plan = plan_advance_authority(
                root,
                fresh,
                fresh_report,
            )
            self.assertEqual(
                fresh_plan["record"]["head_seal"],
                fresh_history["seal"],
            )
        with handoff_repository() as (root, _base, parent, result):
            stale = handoff_document(root, parent, result)
            shift_handoff_times(stale, -600)
            refresh_coordinator_receipt(stale, root)
            issued_at = datetime.fromisoformat(
                stale["handoffs"][0]["states"][-1]["at"].replace("Z", "+00:00")
            ) + timedelta(seconds=1)
            set_coordinator_receipt_time(stale, iso_utc(issued_at))
            resign_coordinator_receipt(stale, root)
            accepted = agent_handoff.validate_document(
                stale,
                root,
                current_time=issued_at,
            )
            current = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            history = agent_handoff.make_history_receipt(
                stale,
                accepted,
                "issue-178-round-1",
                current_time=issued_at,
            )
            publication = authority_publication(
                root,
                current,
                issue=178,
                operation="advance",
                history_carrier=agent_handoff.make_history_carrier(
                    stale,
                    accepted,
                    "issue-178-round-1",
                ),
                history_receipt=history,
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "result does not match canonical validation output|no closed result to seal",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                    operation="advance",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    handoff_document=stale,
                    handoff_result=accepted,
                    handoff_id="issue-178-round-1",
                    publication_attestation=publication,
                    current_time=datetime.now(timezone.utc).replace(
                        microsecond=0
                    ),
                )
    def test_coordinator_receipt_default_live_now_preserves_fractional_precision(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            live_now = datetime.fromisoformat(
                document["coordinator_receipt"]["issued_at"].replace(
                    "Z",
                    "+00:00",
                )
            ).replace(microsecond=800000)
            class FixedNow(datetime):
                @classmethod
                def now(cls, tz=None):
                    if tz is None:
                        return live_now.replace(tzinfo=None)
                    return live_now.astimezone(tz)
            def validate_offset(offset):
                candidate = copy.deepcopy(document)
                set_coordinator_receipt_time(
                    candidate,
                    iso_utc(live_now + offset),
                )
                resign_coordinator_receipt(candidate, root)
                with mock.patch.object(agent_handoff, "datetime", FixedNow):
                    return candidate, agent_handoff.validate_document(
                        candidate,
                        root,
                    )
            accepted_doc, accepted = validate_offset(
                timedelta(microseconds=-300000)
            )
            history = agent_handoff.make_history_receipt(
                accepted_doc,
                accepted,
                "issue-178-round-1",
            )
            _future_doc, future = validate_offset(
                timedelta(microseconds=100000)
            )
            _boundary_doc, boundary = validate_offset(
                timedelta(
                    seconds=-agent_handoff.LIVE_ATTESTATION_MAX_AGE_SECONDS
                )
            )
            _stale_doc, stale = validate_offset(
                timedelta(
                    seconds=-agent_handoff.LIVE_ATTESTATION_MAX_AGE_SECONDS,
                    microseconds=-1,
                )
            )
        self.assertTrue(accepted["summary"]["trusted_push_eligible"])
        self.assertEqual(history["input_seal"], accepted["input_seal"])
        self.assertFalse(future["summary"]["trusted_push_eligible"])
        self.assertTrue(boundary["summary"]["trusted_push_eligible"])
        self.assertFalse(stale["summary"]["trusted_push_eligible"])
        for report in (future, stale):
            self.assertIn(
                "invalid-coordinator-attestation",
                report["summary"]["rejection_codes"],
            )
    def test_coordinator_receipt_repository_ids_must_match_authority(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["coordinator_receipt"]["repository_database_id"] = 7999
            document["coordinator_receipt"]["authority_protection"][
                "repository_id"
            ] = 7999
            sign_coordinator_document(document, root)
            report = agent_handoff.validate_document(document, root)
        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertIn(
            "invalid-coordinator-attestation",
            report["summary"]["rejection_codes"],
        )
    def test_pr_binding_accepts_fast_forwarded_base_with_live_observation(self):
        with handoff_repository() as (root, _base, parent, result):
            _, _, current, _, _ = protected_root_authority(root, parent, result)
            git(root, "switch", "-q", "master")
            readme = root / "README.md"
            readme.write_text("base\nparent\nfast-forward\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "test: fast-forward base")
            current_base_oid = git(root, "rev-parse", "HEAD")
            git(root, "switch", "-q", "agent/issue-178")
            observation = pull_request_observation(root, current)
            publication = authority_publication(root, current, operation="bind", pr_observation=observation)
            observed_at = datetime.fromisoformat(
                observation["observed_at"].replace("Z", "+00:00")
            )
            plan = agent_handoff.plan_history_authority(
                root,
                "example/workflow",
                178,
                200,
                operation="bind",
                expected_object_id=current["object_id"],
                expected_sequence=current["sequence"],
                pull_request_observation=observation,
                publication_attestation=publication,
                current_time=observed_at + timedelta(seconds=2),
            )
        self.assertNotEqual(current_base_oid, parent)
        self.assertEqual(
            plan["record"]["delivery_expectation"]["immediate_base_oid"],
            parent,
        )
        self.assertEqual(
            plan["record"]["pr_binding"]["base_oid"],
            current_base_oid,
        )
        self.assertEqual(
            plan["record"]["publication_attestation"]["binding_expectation"],
            frozen_binding_expectation(
                current,
                current_base_oid=current_base_oid,
            ),
        )
    def test_pr_binding_rejects_stale_or_rewritten_current_base(self):
        with handoff_repository() as (root, base, parent, result):
            _, _, current, _, _ = protected_root_authority(root, parent, result)
            stale_observation = pull_request_observation(root, current)
            stale_publication = authority_publication(root, current, operation="bind", pr_observation=stale_observation)
            git(root, "switch", "-q", "master")
            readme = root / "README.md"
            readme.write_text(
                "base\nparent\nstale-current-base\n",
                encoding="utf-8",
            )
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "test: stale current base")
            git(root, "switch", "-q", "agent/issue-178")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "live repository state",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                    operation="bind",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    pull_request_observation=stale_observation,
                    publication_attestation=stale_publication,
                )
            git(root, "update-ref", "refs/heads/master", base)
            rewritten_observation = pull_request_observation(root, current)
            rewritten_publication = authority_publication(root, current, operation="bind", pr_observation=rewritten_observation)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "not descended from the frozen delivery base",
            ):
                agent_handoff.plan_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                    operation="bind",
                    expected_object_id=current["object_id"],
                    expected_sequence=current["sequence"],
                    pull_request_observation=rewritten_observation,
                    publication_attestation=rewritten_publication,
                )
    def test_historical_bind_accepts_committed_fast_forwarded_base(self):
        with handoff_repository() as (root, _base, parent, result):
            _, _, current, _, _ = protected_root_authority(root, parent, result)
            git(root, "switch", "-q", "master")
            readme = root / "README.md"
            readme.write_text("base\nparent\nfast-forwarded-bound-base\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "test: fast-forward committed base")
            current_base_oid = git(root, "rev-parse", "HEAD")
            git(root, "switch", "-q", "agent/issue-178")
            publish_bound_authority(root, current, current_base_oid=current_base_oid)
            bound = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                200,
            )
        self.assertEqual(bound["sequence"], 2)
        self.assertEqual(
            bound["delivery_expectation"]["immediate_base_oid"],
            parent,
        )
        self.assertEqual(bound["pr_binding"]["base_oid"], current_base_oid)
        self.assertEqual(
            bound["publication_attestation"]["pull_request_observation_digest"],
            agent_handoff.publication_observation_digest(bound["pr_binding"]),
        )
        self.assertEqual(
            bound["publication_attestation"]["binding_expectation"],
            agent_handoff.publication_binding_expectation_for_observation(
                bound["delivery_expectation"],
                bound["pr_binding"],
            ),
        )
    def test_historical_handoff_requires_exact_publication_receipt_digest(self):
        for field, name, digest in (
            ("history_receipt_digest", "receipt-missing", None),
            ("history_receipt_digest", "receipt-wrong", "f" * 64),
            ("history_carrier_digest", "carrier-missing", None),
            ("history_carrier_digest", "carrier-wrong", "e" * 64),
        ):
            with self.subTest(field=field, digest=name):
                with handoff_repository() as (
                    root,
                    _base,
                    parent,
                    result,
                ):
                    document = handoff_document(root, parent, result)
                    report = agent_handoff.validate_document(document, root)
                    current, history, plan = plan_advance_authority(
                        root,
                        document,
                        report,
                    )
                    publication = copy.deepcopy(
                        plan["record"]["publication_attestation"]
                    )
                    publication[field] = digest
                    publication["signature"] = external_sign(
                        root,
                        agent_handoff.signed_record_payload(
                            agent_handoff.PUBLICATION_ATTESTATION_DOMAIN,
                            publication,
                        ),
                    )
                    plan["record"]["publication_attestation"] = publication
                    publish_authority_plan(
                        root,
                        AUTHORITY_OWNERS[str(root)],
                        plan,
                        current["object_id"],
                        issue=history["issue"],
                        pull_request=history["pull_request"],
                        read_back=False,
                    )
                    successor = copy.deepcopy(document)
                    configure_review_successor(
                        successor,
                        history,
                        handoff_id=f"issue-178-review-successor-{name}",
                        pull_request=None,
                    )
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "publication does not bind its event",
                    ):
                        agent_handoff.read_history_authority(
                            root,
                            "example/workflow",
                            history["issue"],
                            history["pull_request"],
                        )
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "publication does not bind its event",
                    ):
                        agent_handoff.validate_document(successor, root)
    def test_historical_handoff_rejects_receipt_only_forgery_in_direct_authority_commit(self):
        with handoff_repository() as (root, _base, parent, first_result):
            first = handoff_document(root, parent, first_result)
            shift_handoff_times(first, -60)
            refresh_coordinator_receipt(first, root)
            first_report = agent_handoff.validate_document(first, root)
            current, history, plan = plan_advance_authority(
                root,
                first,
                first_report,
            )
            forged_history = copy.deepcopy(history)
            forged_history.update(
                assigned_at="2025-12-31T23:59:59Z",
                input_seal="1" * 64,
                git_seal="2" * 64,
                result_seal="3" * 64,
            )
            forged_history["seal"] = agent_handoff.seal_history_receipt(
                forged_history
            )
            plan["record"]["head_seal"] = forged_history["seal"]
            plan["record"]["event"]["handoff_seal"] = forged_history["seal"]
            plan["record"]["event"]["history_receipt"] = forged_history
            plan["record"]["event"]["history_carrier"] = None
            plan["record"]["publication_attestation"] = authority_publication(
                root,
                current,
                operation="advance",
                history_carrier=None,
                history_receipt=forged_history,
            )
            publish_authority_plan(
                root,
                AUTHORITY_OWNERS[str(root)],
                plan,
                current["object_id"],
                issue=history["issue"],
                pull_request=history["pull_request"],
                read_back=False,
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("FORGED = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: forged successor\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            second_result = git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "history_carrier",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    history["issue"],
                    history["pull_request"],
                )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "history_carrier",
            ):
                handoff_document(root, first_result, second_result)
    def test_historical_handoff_rejects_mutated_signed_carrier(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            report = agent_handoff.validate_document(document, root)
            current, history, plan = plan_advance_authority(
                root,
                document,
                report,
            )
            mutated_carrier = copy.deepcopy(
                plan["record"]["event"]["history_carrier"]
            )
            mutated_carrier["document"]["handoffs"][0]["states"][0]["at"] = (
                "2025-12-31T23:59:58Z"
            )
            plan["record"]["event"]["history_carrier"] = mutated_carrier
            plan["record"]["publication_attestation"] = authority_publication(
                root,
                current,
                operation="advance",
                history_carrier=mutated_carrier,
                history_receipt=history,
            )
            publish_authority_plan(
                root,
                AUTHORITY_OWNERS[str(root)],
                plan,
                current["object_id"],
                issue=history["issue"],
                pull_request=history["pull_request"],
                read_back=False,
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not verify",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    history["issue"],
                    history["pull_request"],
                )
    def test_historical_handoff_rejects_self_consistent_public_authority_mutations(self):
        wrong_ref = agent_handoff.history_authority_ref(179, None)
        wrong_anchor_ref = agent_handoff.history_anchor_ref(179)

        def mutate_ref(authority):
            authority["ref"] = wrong_ref
            authority["observation"]["ref"] = wrong_ref
            reseal_history_authority_observation(authority)

        def mutate_object_id(authority):
            authority["object_id"] = "0" * 40
            authority["observation"]["object_id"] = authority["object_id"]
            reseal_history_authority_observation(authority)

        def mutate_anchor_ref(authority):
            authority["anchor_ref"] = wrong_anchor_ref
            authority["observation"]["anchor_ref"] = wrong_anchor_ref
            reseal_history_authority_observation(authority)

        def mutate_anchor_object_id(authority):
            authority["anchor_object_id"] = "1" * 40
            authority["observation"]["anchor_object_id"] = authority[
                "anchor_object_id"
            ]
            reseal_history_authority_observation(authority)

        def mutate_history_events(authority):
            authority["history_events"] = [copy.deepcopy(authority["event"])]

        def mutate_observation(authority):
            authority["observation"]["attempt"] = 2
            reseal_history_authority_observation(authority)

        def mutate_repository(authority):
            authority["repository"] = "example/attacker"

        for name, mutate in (
            ("ref", mutate_ref),
            ("object_id", mutate_object_id),
            ("anchor_ref", mutate_anchor_ref),
            ("anchor_object_id", mutate_anchor_object_id),
            ("history_events", mutate_history_events),
            ("observation-attempt-token", mutate_observation),
            ("repository", mutate_repository),
        ):
            with self.subTest(field=name):
                with handoff_repository() as (
                    root,
                    _base,
                    parent,
                    result,
                ):
                    document = handoff_document(root, parent, result)
                    report = agent_handoff.validate_document(document, root)
                    self.assertEqual(
                        document["history_authority"]["observation"][
                            "attempt"
                        ],
                        1,
                    )
                    current, _history, plan = plan_advance_authority(
                        root,
                        document,
                        report,
                    )
                    mutated_document = copy.deepcopy(document)
                    mutate(mutated_document["history_authority"])
                    sign_coordinator_document(mutated_document, root)
                    with self.assertRaises(agent_handoff.HandoffDataError):
                        agent_handoff.validate_document(mutated_document, root)
                    mutated_result = reseal_handoff_result(
                        mutated_document,
                        report,
                    )
                    publish_self_consistent_history_carrier(
                        root,
                        current,
                        plan,
                        mutated_document,
                        mutated_result,
                    )
                    with self.assertRaises(agent_handoff.HandoffDataError):
                        agent_handoff.read_history_authority(
                            root,
                            "example/workflow",
                            178,
                            None,
                        )
    def test_historical_bind_rejects_stale_signed_authority_observation_and_blocks_successor(self):
        with handoff_repository() as (root, _base, parent, result):
            stale_observation = pull_request_observation(
                root,
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    None,
                ),
            )
            document, report, current, history, bundle = protected_root_authority(
                root, parent, result, with_history=True, with_bundle=True
            )
            publication = authority_publication(root, current, operation="bind", pr_observation=stale_observation)
            publish_bound_history_authority(
                root,
                current,
                stale_observation,
                publication,
            )
            fixture = reporter_fixture_with_handoffs(bundle)
            successor = copy.deepcopy(document)
            configure_review_successor(
                successor,
                history,
                handoff_id=(
                    "issue-178-review-successor-stale-authority"
                ),
            )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "stale for authority state",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                )
            validate_reporter_fixture(fixture)
            verify_reporter_record_offline(bundle)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "stale for authority state",
            ):
                agent_handoff.verify_reporter_record(
                    bundle,
                    revalidate_git=True,
                    repository_root=root,
                )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "stale for authority state",
            ):
                agent_handoff.validate_document(successor, root)
    def test_historical_bind_rejects_out_of_band_head_advance_without_new_handoff(self):
        with handoff_repository() as (root, _base, parent, result):
            document, report, current, _history, bundle = protected_root_authority(root, parent, result, with_bundle=True)
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HANDOFF = 'out-of-band'\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: out-of-band branch advance\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            advanced_head = git(root, "rev-parse", "HEAD")
            self.assertNotEqual(advanced_head, result)
            publish_bound_authority(root, current)
            fixture = reporter_fixture_with_handoffs(bundle)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not match its immediately prior sealed handoff",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                )
            validate_reporter_fixture(fixture)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "does not match its immediately prior sealed handoff",
            ):
                agent_handoff.verify_reporter_record(
                    bundle,
                    revalidate_git=True,
                    repository_root=root,
                )
    def test_historical_bind_requires_matching_carried_handoff_sequence_and_seal(self):
        cases = (
            ("wrong-handoff-sequence", {"handoff_sequence": 0}),
            ("wrong-head-seal", {"head_seal": "f" * 64}),
        )
        for name, overrides in cases:
            with self.subTest(name=name):
                with handoff_repository() as (root, _base, parent, result):
                    _, _, current, _, _ = protected_root_authority(root, parent, result)
                    publish_bound_authority(root, current, **overrides)
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "lacks a carried sealed handoff|does not extend its immediately prior sealed handoff",
                    ):
                        agent_handoff.read_history_authority(
                            root,
                            "example/workflow",
                            178,
                            200,
                        )
    def test_historical_bind_mixed_signed_observation_rejects_read_reporter_and_eligibility(self):
        with handoff_repository() as (root, _base, parent, result):
            document, report, current, _history, bundle = protected_root_authority(root, parent, result, with_bundle=True)
            old_observation = pull_request_observation(root, current)
            git(root, "switch", "-q", "master")
            readme = root / "README.md"
            readme.write_text("base\nparent\nnew-live-base\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "test: new live base")
            current_base_oid = git(root, "rev-parse", "HEAD")
            git(root, "switch", "-q", "agent/issue-178")
            new_observation = pull_request_observation(root, current)
            publication = authority_publication(
                root, current, operation="bind",
                pr_observation=new_observation, current_base_oid=current_base_oid
            )
            publish_bound_history_authority(
                root,
                current,
                old_observation,
                publication,
            )
            fixture = reporter_fixture_with_handoffs(bundle)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "publication does not bind its event",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "publication does not bind its event",
            ):
                agent_handoff.validate_document(document, root)
            validate_reporter_fixture(fixture)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "publication does not bind its event",
            ):
                agent_handoff.verify_reporter_record(
                    bundle,
                    revalidate_git=True,
                    repository_root=root,
                )
    def test_historical_bind_rewritten_base_rejects_read_reporter_and_eligibility(self):
        with handoff_repository() as (root, base, parent, result):
            document, report, current, _history, bundle = protected_root_authority(root, parent, result, with_bundle=True)
            git(root, "switch", "-q", "master")
            git(root, "update-ref", "refs/heads/master", base)
            git(root, "switch", "-q", "agent/issue-178")
            publish_bound_authority(root, current)
            fixture = reporter_fixture_with_handoffs(bundle)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "not descended from the frozen delivery base",
            ):
                agent_handoff.read_history_authority(
                    root,
                    "example/workflow",
                    178,
                    200,
                )
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "not descended from the frozen delivery base",
            ):
                agent_handoff.validate_document(document, root)
            validate_reporter_fixture(fixture)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "not descended from the frozen delivery base",
            ):
                agent_handoff.verify_reporter_record(
                    bundle,
                    revalidate_git=True,
                    repository_root=root,
                )
    def test_review_successor_is_linear_causal_and_nonoverlapping(self):
        with handoff_repository() as (root, _base, parent, first_result):
            first = handoff_document(root, parent, first_result)
            shift_handoff_times(first, -60)
            refresh_coordinator_receipt(first, root)
            first_report = agent_handoff.validate_document(first, root)
            first_closed = datetime.fromisoformat(
                first_report["handoffs"][0]["closed_at"].replace("Z", "+00:00")
            )
            pending = copy.deepcopy(first)
            extra = copy.deepcopy(pending["handoffs"][0])
            extra.update(
                id="issue-178-review-successor",
                owner_id="owner-2",
                owner_database_id=102,
                handoff_kind="review_successor",
                replaces_handoff_id="issue-178-round-1",
                assigned_parent_sha=first_result,
                result=None,
                evidence=[],
                check_receipts=[],
            )
            extra["required_checks"][0]["receipt_id"] = None
            extra["states"] = [
                {"state": name, "at": iso_utc(first_closed + timedelta(seconds=index))}
                for index, name in enumerate(("assignment_sent", "assignment_received", "progressing"), 1)
            ]
            pending["handoffs"].append(extra)
            task = copy.deepcopy(next(item for item in pending["delivery_graph"]["tasks"] if item["phase"] == "implementation"))
            task.update(id="child-review-successor", handoff_id=extra["id"], candidate_sha=first_result, status="in_progress")
            pending["delivery_graph"]["tasks"].append(task)
            relationship = copy.deepcopy(pending["delivery_graph"]["relationships"][0])
            relationship["handoff_id"] = extra["id"]
            pending["delivery_graph"]["relationships"].append(relationship)
            dependency = copy.deepcopy(pending["delivery_graph"]["dependencies"][0])
            dependency["task"] = task["id"]
            pending["delivery_graph"]["dependencies"].append(dependency)
            refresh_coordinator_receipt(pending, root)
            pending_report = agent_handoff.validate_document(pending, root)
            self.assertFalse(pending_report["summary"]["trusted_push_eligible"])
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "no closed result to seal"):
                agent_handoff.make_history_receipt(pending, pending_report, "issue-178-round-1")
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "no closed result to seal"):
                agent_handoff.plan_history_authority(root, "example/workflow", 178, None, operation="advance", expected_object_id=first["history_authority"]["object_id"], expected_sequence=first["history_authority"]["sequence"], handoff_document=pending, handoff_result=pending_report, handoff_id="issue-178-round-1")
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "no closed result to seal"):
                set_history_authority(root, 1, document=pending, result=pending_report)
            first_history = agent_handoff.make_history_receipt(
                        first,
                        first_report,
                        "issue-178-round-1",
            )
            set_history_authority(
                        root,
                        1,
                        document=first,
                        result=first_report,
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("REVIEW_FIX = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                        root,
                        "commit",
                        "-q",
                        "-m",
                        "test: review successor\n\n"
                        + agent_handoff.COPILOT_TRAILER,
            )
            second_result = git(root, "rev-parse", "HEAD")
            successor = handoff_document(root, first_result, second_result)
            successor["prior_handoffs"] = [first_history]
            handoff = successor["handoffs"][0]
            handoff["id"] = "issue-178-review-successor"
            handoff["owner_id"] = "owner-2"
            handoff["owner_database_id"] = 102
            handoff["handoff_kind"] = "review_successor"
            handoff["replaces_handoff_id"] = "issue-178-round-1"
            relationship = successor["delivery_graph"]["relationships"][0]
            relationship["handoff_id"] = handoff["id"]
            task = next(
                        item
                        for item in successor["delivery_graph"]["tasks"]
                        if item["phase"] == "implementation"
            )
            task["handoff_id"] = handoff["id"]
            refresh_coordinator_receipt(successor, root)
            report = agent_handoff.validate_document(successor, root)
            self.assertTrue(
                        report["summary"]["trusted_push_eligible"],
                        report,
            )
            overlapping = copy.deepcopy(successor)
            first_closed = first_history["closed_at"]
            overlapping["handoffs"][0]["states"][0]["at"] = first_closed
            refresh_coordinator_receipt(overlapping, root)
            overlap_report = agent_handoff.validate_document(overlapping, root)
            self.assertIn(
                        "overlapping-lifecycle-successor",
                        overlap_report["summary"]["rejection_codes"],
            )
            wrong_parent = copy.deepcopy(successor)
            wrong_parent["handoffs"][0]["assigned_parent_sha"] = parent
            refresh_coordinator_receipt(wrong_parent, root)
            wrong_report = agent_handoff.validate_document(wrong_parent, root)
            self.assertIn(
                        "review-successor-parent-mismatch",
                        wrong_report["summary"]["rejection_codes"],
            )
    def test_terminal_consume_rejects_double_validation_and_after_sign_push(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            operation = document["coordinator_receipt"]["operation"]
            with self.assertRaisesRegex(
                ValueError,
                "nonce-spent-or-nonmonotonic",
            ):
                signer_request(
                    root,
                    {
                        "mode": "consume",
                        "payload": base64.b64encode(
                            agent_handoff.coordinator_attestation_payload(
                                document
                            )
                        ).decode("ascii"),
                        "nonce": operation["nonce"],
                        "sequence": operation["consume_sequence"],
                        "previous_anchor": operation[
                            "consume_previous_anchor"
                        ],
                        "anchor": operation["consume_anchor"],
                    },
                )
            after_sign = copy.deepcopy(document)
            late_push = {
                "id": "after-sign:push",
                "handoff_id": "issue-178-round-1",
                "actor_login": "owner-1",
                "actor_database_id": 101,
                "action": "push",
                "occurred_at": operation["eligibility_instant"],
                "source": "git-refs",
            }
            refs_source = next(
                source
                for source in after_sign["coordinator_receipt"][
                    "remote_coverage"
                ]["sources"]
                if source["name"] == "git-refs"
            )
            refs_source["events"].append(copy.deepcopy(late_push))
            refs_source["total_count"] += 1
            after_sign["coordinator_receipt"]["remote_coverage"][
                "observed_actions"
            ].append(late_push)
            after_report = agent_handoff.validate_document(after_sign, root)
            self.assertIn(
                "invalid-coordinator-attestation",
                after_report["summary"]["rejection_codes"],
            )
            eligibility = datetime.fromisoformat(
                        document["coordinator_receipt"]["operation"][
                            "eligibility_instant"
                        ].replace("Z", "+00:00")
            )
            action = {
                        "id": "late:comment",
                        "handoff_id": "issue-178-round-1",
                        "actor_login": "owner-1",
                        "actor_database_id": 101,
                        "action": "comment",
                        "occurred_at": (
                            eligibility + timedelta(minutes=1)
                        ).isoformat().replace("+00:00", "Z"),
                        "source": "github-timeline",
            }
            refresh_coordinator_receipt(document, root, actions=[action])
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "event-after-terminal-coverage",
                report["summary"]["rejection_codes"],
            )
    def test_local_history_ref_cannot_forge_remote_genesis(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            reference = agent_handoff.history_authority_ref(178, None)
            clone_root = root.parent / "normal-clone"
            subprocess.run(
                [
                    reporter.trusted_git_executable(),
                    "clone",
                    "--quiet",
                    "--no-local",
                    str(root),
                    str(clone_root),
                ],
                env=reporter.git_environment(offline=True),
                check=True,
                capture_output=True,
            )
            remote_url = git(root, "remote", "get-url", "origin")
            git(clone_root, "remote", "set-url", "origin", remote_url)
            before_fetch = subprocess.run(
                reporter.git_command(
                    clone_root,
                    "cat-file",
                    "-e",
                    document["history_authority"]["object_id"],
                ),
                cwd=clone_root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(before_fetch.returncode, 0)
            fetched = agent_handoff.read_history_authority(
                clone_root,
                "example/workflow",
                178,
                None,
            )
            self.assertEqual(
                fetched["object_id"],
                document["history_authority"]["object_id"],
            )
            forged = owner_create_record_commit(
                root,
                {
                    "schema_version": 2,
                    "repository": "example/workflow",
                    "issue": 178,
                    "sequence": 0,
                    "handoff_sequence": 0,
                    "head_seal": None,
                    "pr_binding": None,
                    "publication_attestation": publication_attestation(
                        root,
                        None,
                        None,
                        operation="bootstrap",
                    ),
                    "event": {
                        "kind": "genesis",
                        "handoff_seal": None,
                        "handoff_id": None,
                        "handoff_kind": None,
                        "lifecycle_state": None,
                        "candidate_sha": None,
                        "closed_at": None,
                        "operation_nonce": None,
                        "consume_store_id": None,
                        "consume_sequence": None,
                        "consume_anchor": None,
                        "assignment": None,
                        "interruption_snapshot": None,
                        "history_receipt": None,
                        "history_carrier": None,
                    },
                    "previous_object_id": None,
                },
                "authority.json",
            )
            git(
                root,
                "update-ref",
                reference,
                forged,
            )
            report = agent_handoff.validate_document(document, root)
            self.assertTrue(report["summary"]["trusted_push_eligible"])
            remote = Path(git(root, "remote", "get-url", "origin"))
            transaction_hook = remote / "hooks" / "reference-transaction"
            transaction_hook.chmod(0o600)
            git(remote, "update-ref", "-d", reference)
            transaction_hook.chmod(0o700)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "genesis is unknown",
            ):
                agent_handoff.validate_document(document, root)
    def test_watcher_timeout_defers_to_authoritative_success(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            add_run(document, result, process_result="timeout")
            report = agent_handoff.validate_document(document, root)
        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertEqual(
            report["watchers"],
            [
                {
                    "run_id": 9001,
                    "head_sha": result,
                    "watcher_process_result": "timeout",
                    "authoritative_outcome": "success",
                    "reconciled": True,
                }
            ],
        )
    def test_watcher_identity_and_observation_must_match_authority(self):
        with handoff_repository() as (root, _base, parent, result):
            wrong_head = handoff_document(root, parent, result)
            add_run(wrong_head, result)
            wrong_head["watchers"][0]["head_sha"] = parent
            report = agent_handoff.validate_document(wrong_head, root)
            self.assertIn(
                "watcher-run-mismatch",
                report["summary"]["rejection_codes"],
            )
            stale_observation = handoff_document(root, parent, result)
            add_run(stale_observation, result)
            stale_observation["workflow_runs"][0][
                "observed_at"
            ] = "2026-01-01T01:14:00Z"
            report = agent_handoff.validate_document(stale_observation, root)
            self.assertIn(
                "watcher-authority-stale",
                report["summary"]["rejection_codes"],
            )
    def test_true_failed_authoritative_run_stays_failed(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            add_run(document, result, conclusion="failure", process_result="error")
            report = agent_handoff.validate_document(document, root)
        self.assertTrue(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertIn(
            "authoritative-run-failed",
            report["summary"]["rejection_codes"],
        )
        self.assertEqual(
            report["watchers"][0]["authoritative_outcome"],
            "failure",
        )
    def test_sigkill_oom_preserves_worktree_and_assigns_one_replacement(self):
        with handoff_repository() as (root, _base, _parent, result):
            preserved = root / "scripts" / "workflow_pilot" / "recovery.py"
            preserved.write_text("RECOVER = True\n", encoding="utf-8")
            document = handoff_document(root, result, result)
            interrupted = document["handoffs"][0]
            interrupted["result"] = None
            interrupted_states = timestamped_states()[:3]
            interrupted_at = (
                datetime.fromisoformat(
                    interrupted_states[-1]["at"].replace("Z", "+00:00")
                )
                + timedelta(seconds=30)
            )
            interrupted_at_text = interrupted_at.isoformat().replace(
                "+00:00",
                "Z",
            )
            interrupted["states"] = interrupted_states + [
                {"state": "interrupted", "at": interrupted_at_text}
            ]
            interrupted["evidence"] = evidence("incomplete")
            interrupted["required_checks"][0]["receipt_id"] = None
            interrupted["check_receipts"] = []
            interrupted["interruption"] = {
                "kind": "sigkill_oom",
                "signal": 9,
                "occurred_at": interrupted_at_text,
                "kernel_evidence": (
                    "Fixture: kernel reports Out of memory and killed process."
                ),
                "interrupted_check_ids": ["focused-module"],
                "preserved_paths": ["scripts/workflow_pilot/recovery.py"],
                "replacement_handoff_id": "issue-178-round-1-replacement",
                "host_process_actions": [],
            }
            replacement = copy.deepcopy(interrupted)
            replacement["id"] = "issue-178-round-1-replacement"
            replacement["owner_id"] = "owner-2"
            replacement["owner_database_id"] = 102
            replacement["handoff_kind"] = "oom_replacement"
            replacement["replaces_handoff_id"] = interrupted["id"]
            replacement["states"] = [
                {
                    "state": "assignment_sent",
                    "at": (
                        interrupted_at + timedelta(seconds=30)
                    ).isoformat().replace("+00:00", "Z"),
                },
                {
                    "state": "assignment_received",
                    "at": (
                        interrupted_at + timedelta(seconds=60)
                    ).isoformat().replace("+00:00", "Z"),
                },
            ]
            replacement["evidence"] = []
            replacement["interruption"] = None
            document["handoffs"].append(replacement)
            primary_task = next(
                task
                for task in document["delivery_graph"]["tasks"]
                if task["id"] == "child-implement"
            )
            primary_task["status"] = "blocked"
            primary_task["status_reason"] = "owner_interrupted"
            replacement_task = copy.deepcopy(primary_task)
            replacement_task["id"] = "child-implement-replacement"
            replacement_task["status"] = "pending"
            replacement_task["status_reason"] = None
            replacement_task["handoff_id"] = replacement["id"]
            document["delivery_graph"]["tasks"].append(replacement_task)
            replacement_relationship = copy.deepcopy(
                document["delivery_graph"]["relationships"][0]
            )
            replacement_relationship["handoff_id"] = replacement["id"]
            document["delivery_graph"]["relationships"].append(
                replacement_relationship
            )
            document["delivery_graph"]["dependencies"].append(
                {
                    "task": replacement_task["id"],
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                }
            )
            refresh_coordinator_receipt(document, root)
            report = agent_handoff.validate_document(document, root)
            for replacement_at in (
                interrupted_at_text,
                (interrupted_at - timedelta(seconds=30))
                .isoformat()
                .replace("+00:00", "Z"),
            ):
                with self.subTest(replacement_at=replacement_at):
                    noncausal = copy.deepcopy(document)
                    noncausal["handoffs"][1]["states"][0][
                        "at"
                    ] = replacement_at
                    noncausal_report = agent_handoff.validate_document(
                        noncausal,
                        root,
                    )
                    self.assertIn(
                        "replacement-assignment-not-causal",
                        noncausal_report["summary"]["rejection_codes"],
                    )
            multiple = copy.deepcopy(document)
            extra = copy.deepcopy(multiple["handoffs"][1])
            extra["id"] = "issue-178-round-1-extra-replacement"
            extra["owner_id"] = "owner-3"
            extra["owner_database_id"] = 103
            multiple["handoffs"].append(extra)
            extra_task = copy.deepcopy(replacement_task)
            extra_task["id"] = "child-implement-extra-replacement"
            extra_task["handoff_id"] = extra["id"]
            multiple["delivery_graph"]["tasks"].append(extra_task)
            extra_relationship = copy.deepcopy(replacement_relationship)
            extra_relationship["handoff_id"] = extra["id"]
            multiple["delivery_graph"]["relationships"].append(
                extra_relationship
            )
            multiple["delivery_graph"]["dependencies"].append(
                {
                    "task": extra_task["id"],
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                }
            )
            multiple_report = agent_handoff.validate_document(multiple, root)
            self.assertIn(
                "replacement-owner-count",
                multiple_report["summary"]["rejection_codes"],
            )
        self.assertEqual(report["summary"]["recovery_count"], 1)
        self.assertEqual(report["summary"]["recovery_minutes"], 7)
        self.assertEqual(report["handoffs"][0]["outcome"], "interrupted")
        self.assertEqual(report["handoffs"][1]["outcome"], "in_progress")
        self.assertEqual(report["handoffs"][1]["rejection_codes"], [])
        self.assertFalse(report["summary"]["trusted_push_eligible"])
        self.assertFalse(report["summary"]["delivery_eligible"])
        self.assertNotIn(
            "incomplete-lifecycle",
            report["handoffs"][1]["rejection_codes"],
        )
        self.assertNotIn(
            "oom-worktree-not-preserved",
            report["handoffs"][0]["rejection_codes"],
        )
        self.assertNotIn(
            "replacement-owner-count",
            report["handoffs"][0]["rejection_codes"],
        )
    def test_hibernated_local_coordinator_fails_closed(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            unavailable = copy.deepcopy(
                document["coordinator_receipt"]["availability"]
            )
            unavailable["mode"] = "local"
            unavailable["autostop_enabled"] = True
            unavailable["stop_on_disconnect"] = True
            refresh_coordinator_receipt(
                document,
                root,
                availability=unavailable,
            )
            report = agent_handoff.validate_document(document, root)
            self.assertFalse(report["summary"]["trusted_push_eligible"])
            self.assertIn(
                "coordinator-unavailable",
                report["summary"]["rejection_codes"],
            )
            expired_availability = copy.deepcopy(unavailable)
            expired_availability["autostop_enabled"] = False
            expired_availability["stop_on_disconnect"] = False
            expired_availability["valid_until"] = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            refresh_coordinator_receipt(
                document,
                root,
                availability=expired_availability,
            )
            expired = agent_handoff.validate_document(document, root)
            self.assertIn(
                "coordinator-unavailable",
                expired["summary"]["rejection_codes"],
            )
            ineffective_availability = copy.deepcopy(unavailable)
            ineffective_availability["stop_on_disconnect"] = False
            refresh_coordinator_receipt(
                document,
                root,
                availability=ineffective_availability,
            )
            ineffective = agent_handoff.validate_document(document, root)
            self.assertIn(
                "coordinator-unavailable",
                ineffective["summary"]["rejection_codes"],
            )
            available_receipt = copy.deepcopy(
                document["coordinator_receipt"]["availability"]
            )
            available_receipt["autostop_enabled"] = False
            refresh_coordinator_receipt(
                document,
                root,
                availability=available_receipt,
            )
            available = agent_handoff.validate_document(document, root)
            self.assertTrue(available["summary"]["trusted_push_eligible"])
    def test_parent_blob_checker_defeats_candidate_replacement_and_bootstraps_closed(self):
        with handoff_repository() as (root, _base, _parent, result):
            checker = root / agent_handoff.RAW_DIFF_CHECK_REPOSITORY_PATH
            checker.write_text(
                "#!/usr/bin/env python3\nraise SystemExit(0)\n",
                encoding="utf-8",
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("CANDIDATE_BYPASS = True  \n", encoding="utf-8")
            git(
                root,
                "add",
                agent_handoff.RAW_DIFF_CHECK_REPOSITORY_PATH,
                "scripts/workflow_pilot/change.py",
            )
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: candidate checker bypass\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            receipt = agent_handoff.execute_allowed_check(
                receipt_id="candidate-checker",
                check_id="focused-module",
                contract="git-diff-check",
                repository_root=root,
                parent_sha=result,
                candidate_sha=candidate,
            )
            self.assertEqual(
                receipt["checker_trust"]["mode"],
                "trusted-parent-blob",
            )
            self.assertNotEqual(receipt["exit_code"], 0)
        with handoff_repository() as (root, _base, _parent, result):
            git(root, "rm", "-q", agent_handoff.RAW_DIFF_CHECK_REPOSITORY_PATH)
            git(root, "commit", "-q", "-m", "test: checker absent parent")
            introducing_parent = git(root, "rev-parse", "HEAD")
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("BOOTSTRAP = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: checker bootstrap\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            document = handoff_document(
                root,
                introducing_parent,
                candidate,
            )
            report = agent_handoff.validate_document(document, root)
            self.assertEqual(
                document["handoffs"][0]["check_receipts"][0][
                    "checker_trust"
                ]["mode"],
                "external-bootstrap",
            )
            self.assertIn(
                "checker-bootstrap-not-trusted-push-eligible",
                report["summary"]["rejection_codes"],
            )
            self.assertFalse(report["summary"]["trusted_push_eligible"])
    def test_remote_coverage_detects_omitted_mutations_and_requires_network_denial(self):
        action_names = ("push", "comment", "request_review", "dispatch_ci")
        with handoff_repository() as (root, _base, parent, result):
            for index, action_name in enumerate(action_names):
                with self.subTest(action=action_name):
                    document = handoff_document(root, parent, result)
                    action = {
                        "id": f"remote:{action_name}",
                        "handoff_id": "issue-178-round-1",
                        "actor_login": "owner-1",
                        "actor_database_id": 101,
                        "action": action_name,
                        "occurred_at": datetime.now(timezone.utc)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "source": agent_handoff.REMOTE_COVERAGE_SOURCES[
                            index % len(agent_handoff.REMOTE_COVERAGE_SOURCES)
                        ],
                    }
                    refresh_coordinator_receipt(
                        document,
                        root,
                        actions=[action],
                    )
                    report = agent_handoff.validate_document(document, root)
                    self.assertIn(
                        "implementation-owner-remote-action",
                        report["summary"]["rejection_codes"],
                    )
                    omitted = copy.deepcopy(document)
                    omitted["coordinator_receipt"]["remote_coverage"][
                        "observed_actions"
                    ] = []
                    sign_coordinator_document(omitted, root)
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "omits or invents",
                    ):
                        agent_handoff.validate_document(omitted, root)
            incomplete = handoff_document(root, parent, result)
            refresh_coordinator_receipt(
                incomplete,
                root,
                incomplete_sources=["github-audit-log"],
            )
            self.assertTrue(
                agent_handoff.validate_document(incomplete, root)[
                    "summary"
                ]["trusted_push_eligible"]
            )
            incomplete["coordinator_receipt"]["remote_coverage"][
                "implementation_processes"
            ][0]["credentials_available"] = True
            sign_coordinator_document(incomplete, root)
            report = agent_handoff.validate_document(incomplete, root)
            self.assertEqual(report["handoffs"][0]["outcome"], "accepted")
            self.assertFalse(report["summary"]["trusted_push_eligible"])
            self.assertIn(
                "remote-coverage-incomplete",
                report["summary"]["rejection_codes"],
            )
    def test_terminal_remote_reconciliation_rejects_post_snapshot_push(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            git(
                root,
                "push",
                "-q",
                "origin",
                f"{result}:refs/heads/{document['handoffs'][0]['expected_branch']}",
            )
            report = agent_handoff.validate_document(document, root)
            self.assertEqual(report["handoffs"][0]["outcome"], "accepted")
            self.assertFalse(report["summary"]["trusted_push_eligible"])
            self.assertIn(
                "remote-coverage-incomplete",
                report["summary"]["rejection_codes"],
            )
            record = reporter_record(root, document, report)
            fixture = reporter_fixture_with_handoffs(record)
            normalized = validate_reporter_fixture(fixture)[
                "implementation_handoffs"
            ][document["handoffs"][0]["id"]]
            self.assertEqual(normalized["reported_outcome"], "bundle_rejected")
            self.assertIn(
                "remote-coverage-incomplete",
                normalized["bundle_rejection_codes"],
            )
    def test_history_authority_requires_exact_installation_anchoring(self):
        authorized_non_user = [
            {
                "actor_type": "Integration",
                "actor_id": 7002,
                "bypass_mode": "always",
            }
        ]
        with handoff_repository(
            authorized_non_user_bypass_actors=authorized_non_user
        ) as (root, _base, _parent, _result):
            current = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            self.assertEqual(
                [
                    item
                    for item in current["authorized_bypass_actors"]
                    if item["actor_type"] != "User"
                ],
                authorized_non_user,
            )
            owner = AUTHORITY_OWNERS[str(root)]
            remote = Path(git(root, "remote", "get-url", "origin"))
            transaction_hook = remote / "hooks" / "reference-transaction"
            def publish_forged(*, signer=None, ruleset_id=None, bypass_actors=None):
                record = {
                    "schema_version": 2,
                    "repository": "example/workflow",
                    "issue": 178,
                    "sequence": 0,
                    "handoff_sequence": 0,
                    "head_seal": None,
                    "pr_binding": None,
                    "signer": (
                        copy.deepcopy(current["signer"])
                        if signer is None
                        else copy.deepcopy(signer)
                    ),
                    "ruleset_id": (
                        current["ruleset_id"]
                        if ruleset_id is None
                        else ruleset_id
                    ),
                    "authorized_bypass_actors": (
                        copy.deepcopy(current["authorized_bypass_actors"])
                        if bypass_actors is None
                        else copy.deepcopy(bypass_actors)
                    ),
                    "delivery_expectation": copy.deepcopy(
                        current["delivery_expectation"]
                    ),
                    "publication_attestation": publication_attestation(
                        root,
                        None,
                        None,
                        issue=178,
                        operation="bootstrap",
                    ),
                    "event": {
                        "kind": "genesis",
                        "handoff_seal": None,
                        "handoff_id": None,
                        "handoff_kind": None,
                        "lifecycle_state": None,
                        "candidate_sha": None,
                        "closed_at": None,
                        "operation_nonce": None,
                        "consume_store_id": None,
                        "consume_sequence": None,
                        "consume_anchor": None,
                        "assignment": None,
                        "interruption_snapshot": None,
                        "history_receipt": None,
                        "history_carrier": None,
                    },
                    "previous_object_id": None,
                }
                record["publication_attestation"]["ruleset_response"][
                    "id"
                ] = record["ruleset_id"]
                record["publication_attestation"]["ruleset_response"][
                    "bypass_actors"
                ] = copy.deepcopy(record["authorized_bypass_actors"])
                record["publication_attestation"]["signature"] = external_sign(
                    root,
                    agent_handoff.signed_record_payload(
                        agent_handoff.PUBLICATION_ATTESTATION_DOMAIN,
                        record["publication_attestation"],
                    ),
                )
                forged_authority = owner_create_record_commit(
                    owner,
                    record,
                    "authority.json",
                    message=b"forged authority\n",
                )
                forged_anchor = owner_create_record_commit(
                    owner,
                    {
                        "schema_version": 1,
                        "repository": "example/workflow",
                        "issue": 178,
                        "sequence": 0,
                        "authority_object_id": forged_authority,
                        "previous_object_id": None,
                    },
                    "anchor.json",
                    message=b"forged anchor\n",
                )
                transaction_hook.chmod(0o600)
                try:
                    git(
                        remote,
                        "fetch",
                        str(owner),
                        forged_authority,
                        forged_anchor,
                    )
                    git(remote, "update-ref", current["ref"], forged_authority)
                    git(
                        remote,
                        "update-ref",
                        current["anchor_ref"],
                        forged_anchor,
                    )
                finally:
                    transaction_hook.chmod(0o700)
            def restore_current():
                transaction_hook.chmod(0o600)
                try:
                    git(remote, "update-ref", current["ref"], current["object_id"])
                    git(
                        remote,
                        "update-ref",
                        current["anchor_ref"],
                        current["anchor_object_id"],
                    )
                finally:
                    transaction_hook.chmod(0o700)
            cases = (
                (
                    "signer",
                    signer_public_with_key_id(
                        {
                            **copy.deepcopy(current["signer"]),
                            "service_identity": "attacker-signer",
                        }
                    ),
                    None,
                    None,
                    "signer does not match coordinator installation",
                ),
                (
                    "ruleset",
                    None,
                    current["ruleset_id"] + 1,
                    None,
                    "ruleset_id does not match coordinator installation",
                ),
                (
                    "missing-non-user",
                    None,
                    None,
                    [
                        item
                        for item in current["authorized_bypass_actors"]
                        if item["actor_type"] == "User"
                    ],
                    "bypass actors do not match coordinator installation",
                ),
                (
                    "extra-non-user",
                    None,
                    None,
                    [
                        *copy.deepcopy(current["authorized_bypass_actors"]),
                        {
                            "actor_type": "DeployKey",
                            "actor_id": 8008,
                            "bypass_mode": "always",
                        },
                    ],
                    "bypass actors do not match coordinator installation",
                ),
            )
            for name, signer, ruleset_id, bypass_actors, pattern in cases:
                with self.subTest(case=name):
                    publish_forged(
                        signer=signer,
                        ruleset_id=ruleset_id,
                        bypass_actors=bypass_actors,
                    )
                    try:
                        with self.assertRaisesRegex(
                            agent_handoff.HandoffDataError,
                            pattern,
                        ):
                            agent_handoff.read_history_authority(
                                root,
                                "example/workflow",
                                178,
                                None,
                            )
                    finally:
                        restore_current()
    def test_remote_git_transport_timeouts_are_bounded(self):
        def invoke_authority_read(root):
            return agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
        def invoke_atomic_preflight(root):
            installation = installation_root_path(root)
            return agent_handoff.require_atomic_push_capability(
                root,
                installation,
                [
                    (
                        git(root, "rev-parse", "HEAD"),
                        agent_handoff.history_authority_ref(178, None),
                    ),
                    (
                        git(root, "rev-parse", "HEAD^"),
                        agent_handoff.history_anchor_ref(178),
                    ),
                ],
            )
        cases = (
            (
                "authority-read",
                invoke_authority_read,
                "remote-git-timeout: Git ls-remote",
            ),
            (
                "atomic-preflight",
                invoke_atomic_preflight,
                "remote-git-preflight-timeout: Git push",
            ),
        )
        for name, callback, pattern in cases:
            with self.subTest(case=name):
                with handoff_repository() as (root, _base, _parent, _result):
                    log_path, pid_path, transport = install_stalling_transport(root)
                    environment = reporter.git_environment(offline=False)
                    environment.update(
                        {
                            "GIT_SSH_COMMAND": str(transport),
                            "GIT_SSH_VARIANT": "simple",
                        }
                    )
                    started = time.monotonic()
                    with (
                        mock.patch.object(
                            reporter,
                            "git_environment",
                            return_value=environment,
                        ),
                        mock.patch.object(
                            agent_handoff,
                            "REMOTE_GIT_TIMEOUT_SECONDS",
                            0.2,
                        ),
                    ):
                        with self.assertRaisesRegex(
                            agent_handoff.HandoffDataError,
                            pattern,
                        ):
                            callback(root)
                    self.assertLess(time.monotonic() - started, 2.0)
                    invocations = [
                        line
                        for line in log_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                        if line
                    ]
                    self.assertEqual(len(invocations), 1)
                    self.assertIn(
                        (
                            "git-upload-pack"
                            if name == "authority-read"
                            else "git-receive-pack"
                        ),
                        invocations[0],
                    )
                    wait_for_pid_exit(pid_path)
    def test_allowed_check_subprocess_timeouts_are_bounded(self):
        with handoff_repository() as (root, _base, parent, result):
            pid_path = root.parent / "hanging-check.pid"
            with mock.patch.object(
                agent_handoff,
                "_allowed_check_execution",
                return_value=hanging_check_spec(pid_path),
            ), mock.patch.object(
                agent_handoff,
                "ALLOWED_CHECK_TIMEOUT_SECONDS",
                0.2,
            ):
                with self.assertRaisesRegex(
                    agent_handoff.HandoffDataError,
                    "allowed-check-timeout: allowed check 'focused-module' exceeded 0.2s",
                ):
                    agent_handoff.execute_allowed_check(
                        receipt_id="hanging-check",
                        check_id="focused-module",
                        contract="git-diff-check",
                        repository_root=root,
                        parent_sha=parent,
                        candidate_sha=result,
                    )
            wait_for_pid_exit(pid_path)
            verify_pid_path = root.parent / "hanging-check-verify.pid"
            receipt = {
                "id": "hanging-check",
                "check_id": "focused-module",
                "contract": "git-diff-check",
                "argv": ["/usr/bin/python3", "-I", "-"],
                "checker_trust": {"mode": "external-bootstrap", "sha256": "a" * 64},
                "parent_sha": parent,
                "candidate_sha": result,
                "worktree_identity": agent_handoff.worktree_identity(root),
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:01Z",
                "exit_code": 0,
                "output_sha256": "0" * 64,
            }
            receipt["seal"] = agent_handoff.seal_check_receipt(receipt)
            with mock.patch.object(
                agent_handoff,
                "_allowed_check_execution",
                return_value=hanging_check_spec(verify_pid_path),
            ), mock.patch.object(
                agent_handoff,
                "ALLOWED_CHECK_TIMEOUT_SECONDS",
                0.2,
            ):
                with self.assertRaisesRegex(
                    agent_handoff.HandoffDataError,
                    "allowed-check-reverify-timeout: allowed check 'focused-module' verification exceeded 0.2s",
                ):
                    agent_handoff._verify_check_receipt(
                        receipt,
                        check={"id": "focused-module", "contract": "git-diff-check"},
                        repository_root=root,
                        parent_sha=parent,
                        candidate_sha=result,
                    )
            wait_for_pid_exit(verify_pid_path)
    def test_actor_ids_and_coordinator_claims_fail_closed(self):
        with handoff_repository() as (root, _base, parent, result):
            missing = handoff_document(root, parent, result)
            missing["handoffs"][0]["owner_database_id"] = None
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must be an integer",
            ):
                agent_handoff.validate_document(missing, root)
            mixed = handoff_document(root, parent, result)
            action = {
                "id": "remote:read",
                "handoff_id": "issue-178-round-1",
                "actor_login": "renamed-owner",
                "actor_database_id": 101,
                "action": "read_github",
                "occurred_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "source": "github-timeline",
            }
            refresh_coordinator_receipt(mixed, root, actions=[action])
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "mixes actor logins",
            ):
                agent_handoff.validate_document(mixed, root)
            tampered = handoff_document(root, parent, result)
            tampered["coordinator_receipt"]["runtime_telemetry"][0][
                "peak_rss_bytes"
            ] = 1
            report = agent_handoff.validate_document(tampered, root)
            self.assertIn(
                "invalid-coordinator-attestation",
                report["summary"]["rejection_codes"],
            )
    def test_structural_budget_derivation_rejects_unclosed_resources_and_schema_claims(self):
        with handoff_repository() as (root, _base, _parent, result):
            document = handoff_document(root, git(root, "rev-parse", "HEAD^"), result)
            report = agent_handoff.validate_document(document, root)
            self.assertEqual(
                report["handoffs"][0]["budget_usage"],
                {
                    "rom_bytes": 0,
                    "ram_bytes": 0,
                    "protocol_changes": 0,
                },
            )
            source = root / "src" / "budget_probe.c"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("int gBudgetProbe;\n", encoding="utf-8")
            git(root, "add", "src/budget_probe.c")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: resource budget\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            resource_candidate = git(root, "rev-parse", "HEAD")
            missing = handoff_document(root, result, resource_candidate)
            missing["handoffs"][0]["allowed_scope"].append("src/")
            report = agent_handoff.validate_document(missing, root)
            self.assertIn(
                "missing-closed-resource-receipt",
                report["summary"]["rejection_codes"],
            )
            resource = {
                "handoff_id": "issue-178-round-1",
                "parent_sha": result,
                "candidate_sha": resource_candidate,
                "closed": True,
                "sources": ["build", "map", "resource"],
                "dependency_authority": "parsed-build-dependency-closure",
                "dependency_inputs": ["src/budget_probe.c"],
                "rom_bytes": 5,
                "ram_bytes": 3,
            }
            refresh_coordinator_receipt(
                missing,
                root,
                resource_receipts=[resource],
            )
            report = agent_handoff.validate_document(missing, root)
            self.assertIn(
                "rom-bytes-budget-exceeded",
                report["summary"]["rejection_codes"],
            )
            self.assertIn(
                "ram-bytes-budget-exceeded",
                report["summary"]["rejection_codes"],
            )
        with handoff_repository() as (root, _base, _parent, result):
            schema = root / agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH
            parsed = json.loads(schema.read_text(encoding="utf-8"))
            parsed["protocol_version"] += 1
            schema.write_text(
                json.dumps(parsed, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            git(root, "add", agent_handoff.HANDOFF_SCHEMA_REPOSITORY_PATH)
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: protocol advance\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            document = handoff_document(root, result, candidate)
            document["handoffs"][0]["budgets"]["changed_lines"] = 1000
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "protocol-changes-budget-exceeded",
                report["summary"]["rejection_codes"],
            )
    def test_completed_oom_replacement_uses_sealed_snapshot_after_clean_recovery(self):
        with handoff_repository() as (root, _base, _parent, result):
            preserved = root / "scripts" / "workflow_pilot" / "recovery.py"
            preserved.write_text("RECOVER = True\n", encoding="utf-8")
            interrupted = handoff_document(root, result, result)
            handoff = interrupted["handoffs"][0]
            handoff["result"] = None
            states = timestamped_states()[:3]
            interrupted_at = (
                datetime.fromisoformat(
                    states[-1]["at"].replace("Z", "+00:00")
                )
                + timedelta(seconds=30)
            ).isoformat().replace("+00:00", "Z")
            handoff["states"] = states + [
                {"state": "interrupted", "at": interrupted_at}
            ]
            handoff["evidence"] = evidence("incomplete")
            handoff["required_checks"][0]["receipt_id"] = None
            handoff["check_receipts"] = []
            handoff["interruption"] = {
                "kind": "sigkill_oom",
                "signal": 9,
                "occurred_at": interrupted_at,
                "kernel_evidence": "kernel OOM kill",
                "interrupted_check_ids": ["focused-module"],
                "preserved_paths": [
                    "scripts/workflow_pilot/recovery.py"
                ],
                "replacement_handoff_id": None,
                "host_process_actions": [],
            }
            task = next(
                item
                for item in interrupted["delivery_graph"]["tasks"]
                if item["phase"] == "implementation"
            )
            task["status"] = "blocked"
            task["status_reason"] = "owner_interrupted"
            task["candidate_sha"] = result
            refresh_coordinator_receipt(interrupted, root)
            interrupted_report = agent_handoff.validate_document(
                interrupted,
                root,
            )
            interrupted_record = reporter_record(
                root,
                interrupted,
                interrupted_report,
            )
            history = agent_handoff.make_history_receipt(
                interrupted,
                interrupted_report,
                "issue-178-round-1",
            )
            self.assertIsNotNone(history["interruption_snapshot"])
            set_history_authority(
                root,
                1,
                document=interrupted,
                result=interrupted_report,
            )
            protected = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            protected_file = protected["history_events"][0][
                "interruption_snapshot"
            ]["files"][0]
            self.assertEqual(
                base64.b64decode(protected_file["content_base64"]),
                b"RECOVER = True\n",
            )
            git(root, "add", "scripts/workflow_pilot/recovery.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: recovered replacement\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            replacement = handoff_document(root, result, candidate)
            replacement["prior_handoffs"] = [history]
            current = replacement["handoffs"][0]
            current["id"] = "issue-178-replacement"
            current["owner_id"] = "owner-2"
            current["owner_database_id"] = 102
            current["handoff_kind"] = "oom_replacement"
            current["replaces_handoff_id"] = "issue-178-round-1"
            recovered_file = history["interruption_snapshot"]["files"][0]
            current["recovery_resolution"] = [
                {
                    "path": recovered_file["path"],
                    "original_sha256": recovered_file["sha256"],
                    "disposition": "restored",
                    "result_path": recovered_file["path"],
                    "result_blob_oid": git(
                        root,
                        "rev-parse",
                        f"{candidate}:{recovered_file['path']}",
                    ),
                    "reason": None,
                }
            ]
            relationship = replacement["delivery_graph"]["relationships"][0]
            relationship["handoff_id"] = current["id"]
            task = next(
                item
                for item in replacement["delivery_graph"]["tasks"]
                if item["phase"] == "implementation"
            )
            task["handoff_id"] = current["id"]
            refresh_coordinator_receipt(replacement, root)
            report = agent_handoff.validate_document(replacement, root)
            self.assertTrue(
                report["summary"]["trusted_push_eligible"],
                report,
            )
            self.assertEqual(report["summary"]["recovery_count"], 0)
            replacement_record = reporter_record(root, replacement, report)
            lost = copy.deepcopy(replacement)
            lost["handoffs"][0]["recovery_resolution"][0][
                "original_sha256"
            ] = "0" * 64
            refresh_coordinator_receipt(lost, root)
            lost_report = agent_handoff.validate_document(lost, root)
            self.assertIn(
                "recovery-content-not-resolved",
                lost_report["summary"]["rejection_codes"],
            )
            replacement_history = agent_handoff.make_history_receipt(
                replacement,
                report,
                current["id"],
            )
            set_history_authority(
                root,
                2,
                document=replacement,
                result=report,
                handoff_id=current["id"],
            )
            verify_reporter_record_offline(interrupted_record)
            verify_reporter_record_offline(replacement_record)
    def test_linker_and_unclassified_inputs_require_resource_receipts(self):
        with handoff_repository() as (root, _base, _parent, result):
            linker = root / "ldscript.txt"
            linker.write_text("SECTIONS { .text : { *(.text) } }\n", encoding="utf-8")
            git(root, "add", "ldscript.txt")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: linker input\n\n" + agent_handoff.COPILOT_TRAILER,
            )
            candidate = git(root, "rev-parse", "HEAD")
            document = handoff_document(root, result, candidate)
            document["handoffs"][0]["allowed_scope"].append("ldscript.txt")
            refresh_coordinator_receipt(document, root)
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "missing-closed-resource-receipt",
                report["summary"]["rejection_codes"],
            )
    def test_availability_time_and_branch_protection_are_verified(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            future = copy.deepcopy(document)
            receipt = future["coordinator_receipt"]
            issued = datetime.now(timezone.utc) + timedelta(minutes=1)
            receipt["issued_at"] = issued.replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            receipt["operation"]["collected_through"] = receipt["issued_at"]
            receipt["operation"]["eligibility_instant"] = receipt["issued_at"]
            receipt["remote_coverage"]["interval_end"] = receipt["issued_at"]
            receipt["authority_protection"]["observed_at"] = receipt[
                "issued_at"
            ]
            for source in receipt["remote_coverage"]["sources"]:
                source["observed_at"] = receipt["issued_at"]
            with self.assertRaisesRegex(
                ValueError,
                "consume-time-not-current",
            ):
                sign_coordinator_document(future, root)
            stale = copy.deepcopy(document)
            receipt = stale["coordinator_receipt"]
            issued = datetime.now(timezone.utc) - timedelta(minutes=10)
            receipt["issued_at"] = issued.replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            receipt["remote_coverage"]["interval_end"] = receipt["issued_at"]
            with self.assertRaisesRegex(
                ValueError,
                "terminal-consume-contract|consume-time-not-current",
            ):
                sign_coordinator_document(stale, root)
            late = copy.deepcopy(
                document["coordinator_receipt"]["availability"]
            )
            late["observed_at"] = document["handoffs"][0]["states"][1]["at"]
            refresh_coordinator_receipt(
                document,
                root,
                availability=late,
            )
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "coordinator-unavailable",
                report["summary"]["rejection_codes"],
            )
            ineffective = handoff_document(root, parent, result)
            ineffective["coordinator_receipt"]["authority_protection"][
                "response"
            ]["update_restricted"] = False
            sign_coordinator_document(ineffective, root)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "unrelated or ineffective",
            ):
                agent_handoff.validate_document(ineffective, root)
    def test_two_distinct_root_owners_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            second = copy.deepcopy(document["handoffs"][0])
            second["id"] = "issue-178-second-root"
            second["owner_id"] = "owner-2"
            second["owner_database_id"] = 102
            document["handoffs"].append(second)
            second_task = copy.deepcopy(
                next(
                    item
                    for item in document["delivery_graph"]["tasks"]
                    if item["phase"] == "implementation"
                )
            )
            second_task["id"] = "child-second-root"
            second_task["handoff_id"] = second["id"]
            document["delivery_graph"]["tasks"].append(second_task)
            second_relationship = copy.deepcopy(
                document["delivery_graph"]["relationships"][0]
            )
            second_relationship["handoff_id"] = second["id"]
            document["delivery_graph"]["relationships"].append(
                second_relationship
            )
            document["delivery_graph"]["dependencies"].append(
                {
                    "task": second_task["id"],
                    "depends_on": "parent-merge",
                    "type": "code_contract",
                }
            )
            refresh_coordinator_receipt(document, root)
            report = agent_handoff.validate_document(document, root)
            self.assertIn(
                "root-owner-count",
                report["summary"]["rejection_codes"],
            )
    def test_duplicate_json_keys_and_non_exact_remote_boundary_fail_schema(self):
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "duplicate JSON key 'schema_version'",
        ):
            reporter.parse_json(
                '{"schema_version":1,"schema_version":1}',
                "handoff",
            )
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["handoffs"][0]["prohibited_remote_actions"].pop()
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "must exactly cover",
            ):
                agent_handoff.validate_document(document, root)
class ReporterHandoffExtensionTests(unittest.TestCase):
    def test_reporter_record_offline_verifies_after_source_removal(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            record = validated_record(root, document)
            fixture = reporter_fixture_with_handoffs(record)
            fixture_trust = reporter_fixture_trust(record)
            fixture_installation = reporter_fixture_installation(record)
            fixture_root = reporter_fixture_repository_root(record)
            wrong_trust = copy.deepcopy(fixture_trust)
            wrong_trust["anchors"][0]["ref"] = "refs/pull/999/head"
            sign_reporter_trust_anchor(root, wrong_trust["anchors"][0])
            expired_trust = copy.deepcopy(fixture_trust)
            expired_trust["anchors"][0]["issued_at"] = "1999-01-01T00:00:00Z"
            expired_trust["anchors"][0]["expires_at"] = "2000-01-01T00:00:00Z"
            sign_reporter_trust_anchor(root, expired_trust["anchors"][0])
            forged_fixture = reporter_fixture_with_handoffs(record)
            forged_bundle = forged_fixture["implementation_handoffs"][0]
            forged_signer = signer_public_with_key_id({**copy.deepcopy(forged_bundle["document"]["history_authority"]["signer"]), "service_identity": "self-authored-attacker"})
            forged_bundle["document"]["history_authority"]["signer"] = forged_signer
            forged_bundle["result_attestation"]["signer_key_id"] = forged_signer["key_id"]
            forged_bundle["input_seal"] = hashlib.sha256(agent_handoff.INPUT_SEAL_DOMAIN + agent_handoff.normalized_json(forged_bundle["document"])).hexdigest()
            forged_bundle["result"]["input_seal"] = forged_bundle["input_seal"]
            forged_bundle["result"]["result_seal"] = agent_handoff.seal_handoff_result(forged_bundle["result"])
            forged_bundle["result_seal"] = forged_bundle["result"]["result_seal"]
            self_trusting_fixture = copy.deepcopy(forged_fixture)
            self_trusting_fixture["implementation_handoff_trust"] = [trusted_reporter_anchor(root, forged_bundle["document"], forged_bundle["input_seal"], authority=forged_bundle["document"]["history_authority"], signer=forged_signer)]
            forged_trust = {"schema_version": 1, "anchors": []}
            with handoff_repository() as (attacker_root, _b, _p, _r):
                attacker_installation = trusted_reporter_installation(attacker_root)
                forged_trust["anchors"].append(trusted_reporter_anchor(attacker_root, forged_bundle["document"], forged_bundle["input_seal"], authority=forged_bundle["document"]["history_authority"], signer=forged_signer, signing_root=attacker_root))
            candidate_install = root / "candidate-installation"
            shutil.copytree(installation_root_path(root), candidate_install)
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "outside the candidate worktree"): agent_handoff.verify_reporter_record(record, revalidate_git=False, repository_root=root, trusted_anchor=fixture_trust["anchors"][0], trusted_installation=candidate_install)
        verify_reporter_record_offline(record, trusted_anchor=fixture_trust["anchors"][0], trusted_installation=fixture_installation)
        offline = validate_reporter_fixture(fixture, implementation_handoff_trust=fixture_trust, implementation_handoff_installation=fixture_installation)
        self.assertEqual(offline["implementation_handoffs"]["issue-178-round-1"]["reported_outcome"], "accepted")
        self.assertFalse(hasattr(agent_handoff, "_VerifiedReporterInstallation"))
        self.assertFalse(hasattr(agent_handoff, "_VERIFIED_REPORTER_INSTALLATION_TOKEN"))
        for trust, installation, pattern in (
            (None, fixture_installation, "require external trusted anchor attestations"),
            (fixture_trust, None, "require an external trusted installation"),
            (fixture_trust, {}, "must be a Path"),
            (fixture_trust, attacker_installation[0], "trusted anchor\\.signer does not match trusted installation"),
            (wrong_trust, fixture_installation, "trusted anchor does not match its record"),
            (expired_trust, fixture_installation, "future-dated or expired"),
        ):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(reporter.PilotDataError, pattern): reporter.validate_fixture(fixture, repository_root=fixture_root, implementation_handoff_trust=trust, implementation_handoff_installation=installation)
        with self.assertRaisesRegex(agent_handoff.HandoffDataError, "requires repository_root"): agent_handoff.verify_reporter_record(record, revalidate_git=True)
        with self.assertRaisesRegex(reporter.PilotDataError, "unknown fields"): reporter.validate_fixture(self_trusting_fixture, repository_root=fixture_root, implementation_handoff_trust=fixture_trust, implementation_handoff_installation=fixture_installation)
        with self.assertRaisesRegex(reporter.PilotDataError, "trusted anchor\\.signature does not verify|trusted anchor\\.signer does not match trusted installation"): reporter.validate_fixture(forged_fixture, repository_root=fixture_root, implementation_handoff_trust=forged_trust, implementation_handoff_installation=fixture_installation)
    def test_forged_signed_stale_record_cannot_claim_offline_acceptance(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            document["handoffs"][0]["result"]["sha"] = parent
            shift_handoff_times(document, -600)
            refresh_coordinator_receipt(document, root)
            stale_result = agent_handoff.validate_document(document, root)
            self.assertTrue(stale_result["handoffs"][0]["stale_response"])
            self.assertEqual(stale_result["handoffs"][0]["outcome"], "rejected")
            stale_record = reporter_record(root, document, stale_result)
            self.assertEqual(validate_reporter_fixture(reporter_fixture_with_handoffs(stale_record))["implementation_handoffs"]["issue-178-round-1"]["reported_outcome"], "rejected")
            forged_document = handoff_document(root, parent, result)
            forged_document["handoffs"][0]["result"]["sha"] = parent
            shift_handoff_times(forged_document, -1200)
            refresh_coordinator_receipt(forged_document, root)
            forged_result = agent_handoff.validate_document(
                forged_document,
                root,
            )
            forged_result["handoffs"][0]["outcome"] = "accepted"
            forged_result["handoffs"][0]["rejection_codes"] = []
            forged_result["summary"].update(
                {
                    "trusted_push_eligible": True,
                    "accepted_handoffs": 1,
                    "rejected_handoffs": 0,
                    "stale_responses": 1,
                    "rejection_codes": [],
                }
            )
            forged_result["result_seal"] = agent_handoff.seal_handoff_result(forged_result)
            forged_record = {
                "source_handoff_ids": sorted(
                    item["id"] for item in forged_document["handoffs"]
                ),
                "document": copy.deepcopy(forged_document),
                "input_seal": forged_result["input_seal"],
                "git_seal": forged_result["git_seal"],
                "result_seal": forged_result["result_seal"],
                "result": forged_result,
                "result_attestation": finalize_result_attestation(
                    root,
                    forged_document,
                    forged_result,
                ),
            }
            trusted_installation = trusted_reporter_installation(root)
            remember_reporter_trust(forged_record, trusted_reporter_anchor(root, forged_document, forged_record["input_seal"]), trusted_installation)
            with self.assertRaisesRegex(agent_handoff.HandoffDataError, "result handoffs do not verify|result summary does not verify"): agent_handoff.reporter_record(forged_document, forged_result, forged_record["result_attestation"], repository_root=trusted_installation[1], trusted_anchor=trusted_reporter_anchor(root, forged_document, forged_result["input_seal"]), trusted_installation=trusted_installation[0])
            with self.assertRaisesRegex(reporter.PilotDataError, "result handoffs do not verify|result summary does not verify"): validate_reporter_fixture(reporter_fixture_with_handoffs(forged_record))
    def test_historical_successors_aggregate_and_require_current_ancestry(self):
        with handoff_repository() as (root, _base, parent, first_result):
            first_document = handoff_document(root, parent, first_result)
            shift_handoff_times(first_document, -60)
            refresh_coordinator_receipt(first_document, root)
            first_result_report = agent_handoff.validate_document(
                first_document,
                root,
            )
            first_record = reporter_record(
                root,
                first_document,
                first_result_report,
            )
            with self.assertRaisesRegex(ValueError, "result-not-consumable"):
                finalize_result_attestation(
                    root,
                    first_document,
                    first_result_report,
                )
            first_history = agent_handoff.make_history_receipt(
                first_document,
                first_result_report,
                "issue-178-round-1",
            )
            set_history_authority(
                root,
                1,
                document=first_document,
                result=first_result_report,
            )
            change = root / "scripts" / "workflow_pilot" / "change.py"
            change.write_text("HISTORICAL_REVIEW = True\n", encoding="utf-8")
            git(root, "add", "scripts/workflow_pilot/change.py")
            git(
                root,
                "commit",
                "-q",
                "-m",
                "test: historical review successor\n\n"
                + agent_handoff.COPILOT_TRAILER,
            )
            second_result = git(root, "rev-parse", "HEAD")
            second_document = handoff_document(
                root,
                first_result,
                second_result,
            )
            second_document["prior_handoffs"] = [first_history]
            handoff = second_document["handoffs"][0]
            handoff["id"] = "issue-178-review-successor"
            handoff["owner_id"] = "owner-2"
            handoff["owner_database_id"] = 102
            handoff["handoff_kind"] = "review_successor"
            handoff["replaces_handoff_id"] = "issue-178-round-1"
            second_document["delivery_graph"]["relationships"][0][
                "handoff_id"
            ] = handoff["id"]
            next(
                task
                for task in second_document["delivery_graph"]["tasks"]
                if task["phase"] == "implementation"
            )["handoff_id"] = handoff["id"]
            refresh_coordinator_receipt(second_document, root)
            second_result_report = agent_handoff.validate_document(
                second_document,
                root,
            )
            second_record = reporter_record(
                root,
                second_document,
                second_result_report,
            )
            second_history = agent_handoff.make_history_receipt(
                second_document,
                second_result_report,
                handoff["id"],
            )
            set_history_authority(
                root,
                2,
                document=second_document,
                result=second_result_report,
                handoff_id=handoff["id"],
            )
            verify_reporter_record_offline(first_record)
            verify_reporter_record_offline(second_record)
            fixture = reporter_fixture_with_handoffs(
                first_record,
                second_record,
            )
            data = validate_reporter_fixture(fixture)
            self.assertEqual(
                sorted(data["implementation_handoffs"]),
                ["issue-178-review-successor", "issue-178-round-1"],
            )
            current = agent_handoff.read_history_authority(
                root,
                "example/workflow",
                178,
                None,
            )
            alternate_record = {
                key: copy.deepcopy(current[key])
                for key in (
                    "schema_version",
                    "repository",
                    "issue",
                    "signer",
                    "ruleset_id",
                    "authorized_bypass_actors",
                    "delivery_expectation",
                )
            }
            alternate_record.update(
                {
                    "sequence": 0,
                    "handoff_sequence": 0,
                    "head_seal": None,
                    "pr_binding": None,
                    "publication_attestation": publication_attestation(
                        root,
                        None,
                        None,
                        operation="bootstrap",
                    ),
                    "event": {
                        "kind": "genesis",
                        "handoff_seal": None,
                        "handoff_id": None,
                        "handoff_kind": None,
                        "lifecycle_state": None,
                        "candidate_sha": None,
                        "closed_at": None,
                        "operation_nonce": None,
                        "consume_store_id": None,
                        "consume_sequence": None,
                        "consume_anchor": None,
                        "assignment": None,
                        "interruption_snapshot": None,
                        "history_receipt": None,
                        "history_carrier": None,
                    },
                    "previous_object_id": None,
                }
            )
            owner = AUTHORITY_OWNERS[str(root)]
            alternate_authority = owner_create_record_commit(
                owner,
                alternate_record,
                "authority.json",
                message=b"alternate authority\n",
            )
            alternate_anchor = owner_create_record_commit(
                owner,
                {
                    "schema_version": 1,
                    "repository": "example/workflow",
                    "issue": 178,
                    "sequence": 0,
                    "authority_object_id": alternate_authority,
                    "previous_object_id": None,
                },
                "anchor.json",
                message=b"alternate anchor\n",
            )
            remote = Path(git(root, "remote", "get-url", "origin"))
            hook = remote / "hooks" / "reference-transaction"
            hook.chmod(0o600)
            git(
                remote,
                "fetch",
                str(owner),
                alternate_authority,
                alternate_anchor,
            )
            git(remote, "update-ref", current["ref"], alternate_authority)
            git(remote, "update-ref", current["anchor_ref"], alternate_anchor)
            hook.chmod(0o700)
            verify_reporter_record_offline(first_record)
            with self.assertRaisesRegex(
                agent_handoff.HandoffDataError,
                "not in current protected head",
            ):
                agent_handoff.verify_reporter_record(
                    first_record,
                    revalidate_git=True,
                    repository_root=root,
                )
    def test_version_two_fixture_reports_sealed_handoff_metrics(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted = validated_record(root, handoff_document(root, parent, result))
            stale_document = handoff_document(root, parent, result)
            rename_handoff(stale_document, "issue-178-stale", owner=("owner-2", 102))
            stale_document["handoffs"][0]["result"]["sha"] = parent
            shift_handoff_times(stale_document, -600)
            refresh_coordinator_receipt(stale_document, root)
            stale = validated_record(root, stale_document)
            fixture = reporter_fixture_with_handoffs(accepted, stale)
            trust = reporter_fixture_trust(accepted, stale)
            installation = reporter_fixture_installation(accepted, stale)
            command = [
                sys.executable,
                "-m",
                "scripts.workflow_pilot.reporter",
                "--repository-root",
                None,
                "--fixture",
                None,
                "--decisions",
                None,
                "--implementation-handoff-trust",
                None,
                "--implementation-handoff-installation",
                str(installation_root_path(root)),
            ]
            decisions = test_reporter.minimal_decisions()
            with test_reporter.git_authority(fixture, implementation_handoff_trust=trust, implementation_handoff_installation=installation) as (authoritative_fixture, authority_root):
                for bundle in authoritative_fixture["implementation_handoffs"]:
                    self.assertEqual(bundle["input_seal"], hashlib.sha256(agent_handoff.INPUT_SEAL_DOMAIN + agent_handoff.normalized_json(bundle["document"])).hexdigest())
                report = reporter.build_report(authoritative_fixture, decisions, authority_root, implementation_handoff_trust=trust, implementation_handoff_installation=installation)
                decisions_path = authority_root / ".github" / "workflow-pilot-decisions.json"
                decisions_path.parent.mkdir(parents=True)
                decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
                fixture_path = authority_root / "operational.json"
                fixture_path.write_text(json.dumps(authoritative_fixture), encoding="utf-8")
                with tempfile.TemporaryDirectory(prefix="workflow-pilot-offline-trust-", dir=TEST_ARTIFACTS) as temporary:
                    trust_root = Path(temporary)
                    trust_path = trust_root / "operational-trust.json"
                    trust_path.write_text(json.dumps(trust), encoding="utf-8")
                    command[4], command[6], command[8], command[10] = map(str, (authority_root, fixture_path, decisions_path, trust_path))
                    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True)
                    self.assertEqual(completed.returncode, 0, completed.stderr.decode())
                    self.assertEqual(completed.stdout, reporter.normalized_json(report))
                    link_path = trust_root / "operational-trust-link.json"
                    link_path.symlink_to(trust_path.name)
                    linked = subprocess.run([*command[:10], str(link_path), *command[11:]], cwd=ROOT, check=False, capture_output=True)
                    self.assertEqual(linked.returncode, 2)
                    self.assertIn(b"implementation handoff trust sidecar must be a regular file", linked.stderr)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["identities"]["implementation_handoffs"], ["issue-178-round-1", "issue-178-stale"])
        expected_lifetime = max(item["lifetime_seconds"] for item in (accepted["result"]["handoffs"][0], stale["result"]["handoffs"][0]))
        self.assertEqual(report["implementation_handoffs"], {"records": 2, "accepted": 1, "bundle_rejected": 0, "rejected": 1, "interrupted": 0, "in_progress": 0, "stale_responses": 1, "max_lifetime_seconds": expected_lifetime, "max_peak_rss_bytes": 134217728, "coordination_turns": 4, "recovery_minutes": 0, "rejection_codes": sorted(stale["result"]["summary"]["rejection_codes"])})
        tampered = copy.deepcopy(fixture)
        tampered["implementation_handoffs"][0]["result"]["summary"]["accepted_handoffs"] = 99
        with self.assertRaisesRegex(reporter.PilotDataError, "result seal does not verify"): validate_reporter_fixture(tampered)
        rehashed = copy.deepcopy(fixture)
        bundle = rehashed["implementation_handoffs"][0]
        bundle["result"]["git_authority"]["head_sha"] = "0" * 40
        bundle["result"]["git_seal"] = agent_handoff.seal_git_authority(bundle["result"]["git_authority"])
        bundle["git_seal"] = bundle["result"]["git_seal"]
        bundle["result"]["result_seal"] = agent_handoff.seal_handoff_result(bundle["result"])
        bundle["result_seal"] = bundle["result"]["result_seal"]
        with self.assertRaisesRegex(reporter.PilotDataError, "Git authority does not verify|result signature does not verify"): validate_reporter_fixture(rehashed)
    def test_reporter_counts_bundle_rejections_without_losing_failure_codes(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted_record = validated_record(
                root,
                handoff_document(root, parent, result),
            )
            set_history_authority(root, 0, None, issue=179)
            def duplicate_watcher(document):
                add_run(document, result)
                duplicate = copy.deepcopy(document["watchers"][0])
                duplicate["id"] = "watcher-9001-duplicate"
                document["watchers"].append(duplicate)
                refresh_coordinator_receipt(document, root)
            def watcher_owner_mismatch(document):
                add_run(document, result)
                document["watchers"][0]["coordinator_id"] = "coordinator-2"
                refresh_coordinator_receipt(document, root)
            def incomplete_coverage(document):
                refresh_coordinator_receipt(
                    document,
                    root,
                    incomplete_sources=["github-audit-log"],
                )
                document["coordinator_receipt"]["remote_coverage"][
                    "implementation_processes"
                ][0]["credentials_available"] = True
                sign_coordinator_document(document, root)
            def local_rejected_with_duplicate_watcher(document):
                duplicate_watcher(document)
                document["handoffs"][0]["result"]["sha"] = parent
                sign_coordinator_document(document, root)
            bundle_metrics = {"accepted": 1, "bundle_rejected": 1, "rejected": 0, "interrupted": 0, "in_progress": 0, "stale_responses": 0}
            rejected_metrics = {"accepted": 1, "bundle_rejected": 0, "rejected": 1, "interrupted": 0, "in_progress": 0, "stale_responses": 1}
            cases = {
                "duplicate-watcher": ("issue-178-duplicate-watcher", "owner-2", 102, "bundle_rejected", bundle_metrics, duplicate_watcher),
                "watcher-owner-mismatch": ("issue-178-watcher-owner", "owner-3", 103, "bundle_rejected", bundle_metrics, watcher_owner_mismatch),
                "remote-coverage-incomplete": ("issue-178-incomplete-coverage", "owner-4", 104, "bundle_rejected", bundle_metrics, incomplete_coverage),
                "local-rejected-duplicate-watcher": ("issue-178-local-rejected-duplicate-watcher", "owner-5", 105, "rejected", rejected_metrics, local_rejected_with_duplicate_watcher),
            }
            for code, (
                handoff_id,
                owner_id,
                owner_database_id,
                expected_reported_outcome,
                expected_metrics,
                mutate,
            ) in cases.items():
                with self.subTest(code=code):
                    document = handoff_document(
                        root,
                        parent,
                        result,
                        issue=179,
                        handoff_id=handoff_id,
                    )
                    document["handoffs"][0]["owner_id"] = owner_id
                    document["handoffs"][0]["owner_database_id"] = owner_database_id
                    mutate(document)
                    result_report = agent_handoff.validate_document(document, root)
                    (_expected_summary, expected_bundle_codes, _delivery_graph, _watchers) = agent_handoff.derive_reporter_result_summary(document, result_report)
                    record = reporter_record(root, document, result_report)
                    fixture = reporter_fixture_with_handoffs(accepted_record, record)
                    data = validate_reporter_fixture(fixture)
                    normalized = data["implementation_handoffs"][handoff_id]
                    self.assertEqual(normalized["reported_outcome"], expected_reported_outcome)
                    if expected_reported_outcome == "bundle_rejected":
                        self.assertEqual(normalized["outcome"], "accepted")
                    self.assertEqual(normalized["bundle_rejection_codes"], expected_bundle_codes)
                    report = test_reporter.authoritative_report(fixture, test_reporter.minimal_decisions(), implementation_handoff_trust=reporter_fixture_trust(accepted_record, record), implementation_handoff_installation=reporter_fixture_installation(accepted_record, record))
                    for field, expected_value in {"records": 2, "max_peak_rss_bytes": 134217728, "coordination_turns": 4, "recovery_minutes": 0}.items():
                        self.assertEqual(report["implementation_handoffs"][field], expected_value)
                    for field, expected_value in expected_metrics.items():
                        self.assertEqual(report["implementation_handoffs"][field], expected_value)
                    self.assertEqual(report["implementation_handoffs"]["rejection_codes"], sorted(result_report["summary"]["rejection_codes"]))
    def test_verify_reporter_record_rederives_rows_and_summary_from_signed_inputs(self):
        with handoff_repository() as (root, _base, parent, result):
            honest_document = handoff_document(root, parent, result)
            add_run(honest_document, result)
            duplicate = copy.deepcopy(honest_document["watchers"][0])
            duplicate["id"] = "watcher-9001-duplicate"
            honest_document["watchers"].append(duplicate)
            refresh_coordinator_receipt(honest_document, root)
            honest_result = agent_handoff.validate_document(honest_document, root)
            self.assertFalse(honest_result["summary"]["trusted_push_eligible"])
            self.assertIn(
                "duplicate-watcher",
                honest_result["summary"]["rejection_codes"],
            )
            honest_record = reporter_record(
                root,
                honest_document,
                honest_result,
            )
            verify_reporter_record_offline(honest_record)
        with handoff_repository() as (root, _base, parent, result):
            def make_tampered_record(*, row_mutator=None, result_mutator=None):
                document = handoff_document(root, parent, result)
                trusted_installation = trusted_reporter_installation(root)
                add_run(document, result)
                duplicate = copy.deepcopy(document["watchers"][0])
                duplicate["id"] = "watcher-9001-duplicate"
                document["watchers"].append(duplicate)
                refresh_coordinator_receipt(document, root)
                tampered_result = agent_handoff.validate_document(document, root)
                if row_mutator is not None:
                    row_mutator(tampered_result["handoffs"][0])
                if result_mutator is not None:
                    result_mutator(tampered_result)
                    tampered_result["git_seal"] = agent_handoff.seal_git_authority(
                        tampered_result["git_authority"]
                    )
                (
                    tampered_result["summary"],
                    _global_codes,
                    tampered_result["delivery_graph"],
                    tampered_result["watchers"],
                ) = agent_handoff.derive_reporter_result_summary(
                    document,
                    tampered_result,
                )
                tampered_result["result_seal"] = agent_handoff.seal_handoff_result(
                    tampered_result
                )
                trusted_anchor = trusted_reporter_anchor(
                    root,
                    document,
                    tampered_result["input_seal"],
                )
                return remember_reporter_trust(
                    {
                        "source_handoff_ids": sorted(
                            item["id"] for item in document["handoffs"]
                        ),
                        "document": copy.deepcopy(document),
                        "input_seal": tampered_result["input_seal"],
                        "git_seal": tampered_result["git_seal"],
                        "result_seal": tampered_result["result_seal"],
                        "result": tampered_result,
                        "result_attestation": finalize_result_attestation(
                            root,
                            document,
                            tampered_result,
                        ),
                    },
                    trusted_anchor,
                    trusted_installation,
                )
            for row_mutator in (
                lambda row: row.update(
                    {
                        "outcome": "rejected",
                        "rejection_codes": ["stale-result"],
                        "stale_response": True,
                    }
                ),
                lambda row: row.update(
                    {
                        "lifetime_seconds": 1,
                        "peak_rss_bytes": 999,
                        "coordination_turns": 42,
                    }
                ),
            ):
                with self.subTest(row_mutator=row_mutator.__code__.co_firstlineno):
                    with self.assertRaisesRegex(
                        agent_handoff.HandoffDataError,
                        "result handoffs do not verify",
                    ):
                        verify_reporter_record_offline(
                            make_tampered_record(row_mutator=row_mutator)
                        )
            for label, result_mutator in (
                ("head", lambda result: result["git_authority"].__setitem__("head_sha", "0" * 40)),
                ("branch", lambda result: result["git_authority"].__setitem__("branch", "agent/other")),
                (
                    "dirty",
                    lambda result: result["git_authority"].update(
                        clean=False,
                        dirty_paths=["scripts/workflow_pilot/change.py"],
                    ),
                ),
                (
                    "parent",
                    lambda result: result["git_authority"]["handoffs"][0].__setitem__(
                        "assigned_parent_sha",
                        "0" * 40,
                    ),
                ),
            ):
                with self.subTest(result_mutator=label):
                    record = make_tampered_record(result_mutator=result_mutator)
                    for revalidate_git in (False, True):
                        with self.assertRaisesRegex(
                            agent_handoff.HandoffDataError,
                            "Git authority does not verify",
                        ):
                            if revalidate_git:
                                agent_handoff.verify_reporter_record(
                                    copy.deepcopy(record),
                                    revalidate_git=True,
                                    repository_root=root,
                                )
                            else:
                                verify_reporter_record_offline(
                                    copy.deepcopy(record)
                                )
                    with self.assertRaisesRegex(
                        reporter.PilotDataError,
                        "Git authority does not verify",
                    ):
                        validate_reporter_fixture(
                            reporter_fixture_with_handoffs(record)
                        )
    def test_reporter_same_owner_retry_after_rejected_root_is_allowed(self):
        with handoff_repository() as (root178, _base178, parent178, result178):
            rejected178 = handoff_document(root178, parent178, result178)
            rename_handoff(rejected178, "issue-178-rejected-root")
            shift_handoff_times(rejected178, -600)
            rejected178["handoffs"][0]["result"] = None
            refresh_coordinator_receipt(rejected178, root178)
            rejected178_record = validated_record(root178, rejected178)
            accepted178_record = validated_record(
                root178,
                handoff_document(root178, parent178, result178),
            )
            set_history_authority(root178, 0, None, issue=179)
            accepted179 = handoff_document(
                root178,
                parent178,
                result178,
                issue=179,
                handoff_id="issue-179-round-1",
            )
            accepted179_record = validated_record(root178, accepted179)
            valid = validate_reporter_fixture(
                reporter_fixture_with_handoffs(
                    rejected178_record,
                    accepted178_record,
                    accepted179_record,
                )
            )
            self.assertEqual(
                sorted(valid["implementation_handoffs"]),
                [
                    "issue-178-rejected-root",
                    "issue-178-round-1",
                    "issue-179-round-1",
                ],
            )

    def test_reporter_same_issue_duplicates_overlap_and_conflicting_roots_reject(self):
        with handoff_repository() as (root, _base, parent, result):
            accepted_record = validated_record(
                root,
                handoff_document(root, parent, result),
            )
            duplicate = handoff_document(root, parent, result)
            duplicate["handoffs"][0]["result"] = None
            refresh_coordinator_receipt(duplicate, root)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "duplicate implementation handoff 'issue-178-round-1'",
            ):
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(
                        accepted_record,
                        validated_record(root, duplicate),
                    )
                )
            overlap = handoff_document(root, parent, result)
            rename_handoff(overlap, "issue-178-overlap")
            overlap["handoffs"][0]["result"] = None
            refresh_coordinator_receipt(overlap, root)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "overlaps another same-issue handoff",
            ):
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(
                        accepted_record,
                        validated_record(root, overlap),
                    )
                )
            conflicting = handoff_document(root, parent, result)
            rename_handoff(
                conflicting,
                "issue-178-conflicting-root",
                owner=("owner-2", 102),
            )
            shift_handoff_times(conflicting, -600)
            refresh_coordinator_receipt(conflicting, root)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "unrelated same-issue root",
            ):
                validate_reporter_fixture(
                    reporter_fixture_with_handoffs(
                        accepted_record,
                        validated_record(root, conflicting),
                    )
                )
    def test_handoff_fixtures_derive_lifecycle_cutoff_and_reject_future_assignments(self):
        with handoff_repository() as (root, _base, parent, result):
            document = handoff_document(root, parent, result)
            result_report = agent_handoff.validate_document(document, root)
            record = reporter_record(root, document, result_report)
            fixture = reporter_fixture_with_handoffs(record)
            validate_reporter_fixture(fixture)
            self.assertGreater(
                datetime.fromisoformat(
                    fixture["lifecycle_as_of"].replace("Z", "+00:00")
                ),
                datetime.fromisoformat(
                    record["result"]["handoffs"][0]["closed_at"].replace(
                        "Z",
                        "+00:00",
                    )
                ),
            )
            future = copy.deepcopy(fixture)
            assigned_at = datetime.fromisoformat(
                record["result"]["handoffs"][0]["assigned_at"].replace(
                    "Z",
                    "+00:00",
                )
            )
            future["lifecycle_as_of"] = iso_utc(
                assigned_at - timedelta(seconds=1)
            )
            future["review_thread_event_source"]["coverage_end"] = future[
                "lifecycle_as_of"
            ]
            trust = reporter_fixture_trust(record)
            trust["anchors"][0]["issued_at"] = iso_utc(
                assigned_at - timedelta(seconds=2)
            )
            trust["anchors"][0]["expires_at"] = iso_utc(
                assigned_at + timedelta(days=1)
            )
            sign_reporter_trust_anchor(root, trust["anchors"][0])
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "assigned_at follows lifecycle_as_of",
            ):
                validate_reporter_fixture(
                    future,
                    implementation_handoff_trust=trust,
                )
    def test_frozen_version_one_schema_remains_closed_and_unchanged(self):
        baseline = reporter.load_json(test_reporter.BASELINE)
        data = validate_reporter_fixture(baseline)
        self.assertEqual(baseline["schema_version"], 1)
        self.assertNotIn("implementation_handoffs", baseline)
        self.assertEqual(data["implementation_handoffs"], {})
        self.assertEqual(data["implementation_handoff_bundles"], {})
        changed = copy.deepcopy(baseline)
        changed["implementation_handoffs"] = []
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "unknown fields",
        ):
            validate_reporter_fixture(changed)
    def test_handoff_reporter_schema_rejects_unknown_or_incoherent_records(self):
        with handoff_repository() as (root, _base, parent, result):
            record = validated_record(root, handoff_document(root, parent, result))
            fixture = reporter_fixture_with_handoffs(record)
            fixture["implementation_handoffs"] = [
                {
                    "input_seal": record["input_seal"],
                    "id": "bad",
                    "owner_id": "owner-a",
                    "assigned_at": "2026-01-01T01:00:00Z",
                    "closed_at": "2026-01-01T01:05:00Z",
                    "outcome": "accepted",
                    "rejection_codes": ["stale-result"],
                    "peak_rss_bytes": 1,
                    "coordination_turns": 1,
                    "recovery_minutes": 0,
                }
            ]
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "is missing fields",
            ):
                reporter.validate_fixture(
                    fixture,
                    repository_root=reporter_fixture_repository_root(record),
                    implementation_handoff_trust=reporter_fixture_trust(record),
                    implementation_handoff_installation=reporter_fixture_installation(record),
                )

if __name__ == "__main__":
    unittest.main()
