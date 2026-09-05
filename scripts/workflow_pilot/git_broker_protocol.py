"""Closed issue-scoped publication protocol; no transport or credential authority."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from scripts.workflow_pilot.signed_records import (
    RecordError, canonical_json, coordinator_signer, digest, fields, integer, oid,
    parse_utc, signed_payload, verify_signature,
)


PROTOCOL = "workflow-pilot-authenticated-git-broker-v1"
PLAN_DOMAIN = b"workflow-pilot-exact-git-publication-plan-v1\0"
RESPONSE_DOMAIN = b"workflow-pilot-git-broker-response-v1\0"
HELLO_DOMAIN = b"workflow-pilot-git-broker-session-v1\0"
ATTESTATION_DOMAIN = b"workflow-pilot-authority-publication-v1\0"
MAX_JSON = 256 * 1024
MAX_RESPONSE = 8192
MAX_PACK = 16 * 1024 * 1024
MAX_OBJECT = 2 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_OBJECTS = 4096
MAX_SEQUENCE = 2**31 - 1
MAX_LIFETIME = 30
PROCESS_SECONDS = 8
MAX_PROCESS_OUTPUT = 4 * 1024 * 1024
MAX_JOURNAL_ROWS = 100000
PLAN_FIELDS = {
    "schema_version", "protocol", "deployment_id", "repository", "repository_id",
    "issue", "endpoint", "actor_id", "signer_key_id", "client_certificate_sha256",
    "operation", "sequence", "nonce", "issued_at", "expires_at", "updates",
    "pack", "signature",
}
AUTHORITY_FIELDS = {
    "schema_version", "repository", "issue", "sequence", "handoff_sequence",
    "head_seal", "pr_binding", "signer", "ruleset_id", "authorized_bypass_actors",
    "delivery_expectation", "publication_attestation", "event", "previous_object_id",
}
ANCHOR_FIELDS = {
    "schema_version", "repository", "issue", "sequence", "authority_object_id",
    "previous_object_id",
}
ATTESTATION_FIELDS = {
    "source", "repository", "repository_id", "ruleset_source", "issue",
    "authority_object_id", "anchor_object_id", "operation_nonce", "operation",
    "new_head_seal", "history_carrier_digest", "history_receipt_digest",
    "pull_request_observation_digest", "binding_expectation", "observed_at",
    "coordinator_database_id", "ruleset_response", "signature",
}
PR_OBSERVATION_FIELDS = {
    "source", "repository_id", "repository_full_name", "pull_request", "state", "merged",
    "base_branch", "head_branch", "head_repository_full_name", "base_oid", "head_oid",
    "created_at", "coordinator_database_id", "observed_at", "authority_object_id",
    "anchor_object_id", "expected_handoff_branch", "delivery_branch", "signature",
}


def issue_refs(issue: int) -> tuple[str, str]:
    integer(issue, 1, MAX_SEQUENCE)
    return (
        f"refs/heads/workflow-pilot/authority/issue-{issue}",
        f"refs/heads/workflow-pilot/authority-anchor/issue-{issue}",
    )


@dataclass(frozen=True)
class Policy:
    """Only a protected installation may supply this authority, never a request."""

    deployment_id: str
    repository: str
    repository_id: int
    issue: int
    endpoint: str
    actor_id: int
    client_certificate_sha256: str
    signer: dict
    ruleset_id: int
    authorized_bypass_actors: list

    @classmethod
    def parse(cls, value: Any) -> "Policy":
        fields(value, set(cls.__dataclass_fields__))
        for name in ("deployment_id", "client_certificate_sha256"):
            digest(value[name])
        for name in ("repository_id", "issue", "actor_id", "ruleset_id"):
            integer(value[name], 1, MAX_SEQUENCE)
        repository = value["repository"]
        if (
            not isinstance(repository, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository)
            is None or repository.rsplit("/", 1)[1] in {".", ".."}
        ):
            raise RecordError("invalid repository identity")
        endpoint = value["endpoint"]
        network_endpoints = {
            f"https://github.com/{repository}.git",
            f"ssh://git@github.com/{repository}.git",
        }
        if not isinstance(endpoint, str) or (
            endpoint not in network_endpoints
            and re.fullmatch(r"file:///[A-Za-z0-9_./-]+", endpoint) is None
        ):
            raise RecordError("endpoint is not canonical GitHub HTTPS/SSH or protected file")
        if endpoint.startswith("file:") and (
            "//" in endpoint[7:] or any(part in {".", "..", ""} for part in endpoint[8:].split("/"))
        ):
            raise RecordError("noncanonical local endpoint")
        coordinator_signer(value["signer"])
        actors = value["authorized_bypass_actors"]
        if not isinstance(actors, list) or not 1 <= len(actors) <= 16:
            raise RecordError("invalid bypass actor set")
        seen = set()
        for actor in actors:
            if not isinstance(actor, dict):
                raise RecordError("invalid bypass actor")
            expected = {"actor_type", "actor_id", "bypass_mode"}
            if actor.get("actor_type") == "User":
                expected.add("database_id")
            fields(actor, expected)
            if not isinstance(actor["actor_type"], str) or actor["actor_type"] not in {
                "User", "Integration", "DeployKey", "RepositoryRole", "OrganizationAdmin",
            }:
                raise RecordError("unknown bypass actor type")
            integer(actor["actor_id"], 1, MAX_SEQUENCE)
            if actor["actor_type"] == "User" and (
                type(actor["database_id"]) is not int or actor["database_id"] != actor["actor_id"]
            ):
                raise RecordError("GitHub User bypass identities disagree")
            if actor["bypass_mode"] != "always":
                raise RecordError("unsupported bypass mode")
            identity = canonical_json(actor)
            if identity in seen:
                raise RecordError("duplicate bypass actor")
            seen.add(identity)
        if not any(
            actor["actor_type"] == "User" and actor["actor_id"] == value["actor_id"]
            for actor in actors
        ):
            raise RecordError("coordinator is not authorized by installed policy")
        return cls(**value)

    @property
    def refs(self) -> tuple[str, str]:
        return issue_refs(self.issue)

    @property
    def signing_key(self) -> dict:
        return {name: self.signer[name] for name in ("algorithm", "modulus_hex", "exponent")}


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(PLAN_DOMAIN + canonical_json(plan)).hexdigest()


def unsigned_plan(
    policy: Policy, *, operation: str, sequence: int, nonce: str,
    issued_at: str, expires_at: str, old_authority: str | None,
    old_anchor: str | None, new_authority: str, new_anchor: str,
    pack: bytes, objects: list[str],
) -> dict:
    """PR191 integration seam: export exact objects, then sign PLAN_DOMAIN.

    The external signer must first approve PR191's canonical handoff/history
    result. This constructor grants no signing, credential or push authority.
    The broker independently parses and verifies the returned signed record.
    """
    if not isinstance(pack, bytes) or not 32 <= len(pack) <= MAX_PACK:
        raise RecordError("pack size bound")
    result = {
        "schema_version": 1, "protocol": PROTOCOL, "deployment_id": policy.deployment_id,
        "repository": policy.repository, "repository_id": policy.repository_id,
        "issue": policy.issue, "endpoint": policy.endpoint, "actor_id": policy.actor_id,
        "signer_key_id": policy.signer["key_id"],
        "client_certificate_sha256": policy.client_certificate_sha256,
        "operation": operation, "sequence": sequence, "nonce": nonce,
        "issued_at": issued_at, "expires_at": expires_at,
        "updates": [
            {"ref": policy.refs[0], "old": old_authority, "new": new_authority},
            {"ref": policy.refs[1], "old": old_anchor, "new": new_anchor},
        ],
        "pack": {
            "sha256": hashlib.sha256(pack).hexdigest(), "size": len(pack),
            "objects": sorted(objects),
        },
    }
    if len(canonical_json(result)) > MAX_JSON - 4096:
        raise RecordError("plan size bound")
    return result


def validate_plan(plan: Any, policy: Policy, peer: str, now: datetime) -> dict:
    fields(plan, PLAN_FIELDS)
    if (
        type(plan["schema_version"]) is not int or plan["schema_version"] != 1
        or plan["protocol"] != PROTOCOL
    ):
        raise RecordError("wrong publication protocol")
    for name in (
        "deployment_id", "repository", "repository_id", "issue", "endpoint",
        "actor_id", "client_certificate_sha256",
    ):
        if plan[name] != getattr(policy, name) or type(plan[name]) is not type(getattr(policy, name)):
            raise RecordError("plan is outside installed authority")
    if (
        peer != policy.client_certificate_sha256
        or plan["signer_key_id"] != policy.signer["key_id"]
    ):
        raise RecordError("wrong coordinator capability or plan signer")
    digest(plan["nonce"])
    integer(plan["sequence"], 0, MAX_SEQUENCE)
    issued, expires = parse_utc(plan["issued_at"]), parse_utc(plan["expires_at"])
    if not issued <= now < expires or not timedelta(0) < expires - issued <= timedelta(seconds=MAX_LIFETIME):
        raise RecordError("expired, future or overlong publication capability")
    if not isinstance(plan["operation"], str) or plan["operation"] not in {"bootstrap", "advance", "bind"}:
        raise RecordError("generic Git publication is not authorized")
    updates = plan["updates"]
    if not isinstance(updates, list) or len(updates) != 2:
        raise RecordError("publication requires exactly authority and anchor")
    refs = set()
    for update in updates:
        fields(update, {"ref", "old", "new"})
        if not isinstance(update["ref"], str) or update["ref"] not in policy.refs or update["ref"] in refs:
            raise RecordError("unauthorized or duplicate ref")
        refs.add(update["ref"])
        oid(update["old"], nullable=True)
        oid(update["new"])
        if update["old"] == update["new"]:
            raise RecordError("publication must advance both refs")
    if len({update["new"] for update in updates}) != 2:
        raise RecordError("authority and anchor cannot alias")
    if plan["operation"] == "bootstrap":
        if plan["sequence"] != 0 or any(update["old"] is not None for update in updates):
            raise RecordError("bootstrap must create the two absent refs")
    elif plan["sequence"] == 0 or any(update["old"] is None for update in updates):
        raise RecordError("update must extend both exact existing refs")
    pack = fields(plan["pack"], {"sha256", "size", "objects"})
    digest(pack["sha256"])
    integer(pack["size"], 32, MAX_PACK)
    objects = pack["objects"]
    if not isinstance(objects, list) or not 1 <= len(objects) <= MAX_OBJECTS:
        raise RecordError("object count bound")
    for object_id in objects:
        oid(object_id)
    if objects != sorted(set(objects)) or not all(update["new"] in objects for update in updates):
        raise RecordError("noncanonical or incomplete exact object set")
    verify_signature(policy.signing_key, signed_payload(PLAN_DOMAIN, plan), plan["signature"])
    return plan


def expected_refs(plan: dict, field: str) -> dict[str, str | None]:
    return {update["ref"]: update[field] for update in plan["updates"]}


def validate_authority_records(
    policy: Policy, plan: dict, authority: dict, anchor: dict,
    previous_authority: dict | None, previous_anchor: dict | None,
) -> None:
    """Check the publication layer without replacing PR191's handoff validator."""
    fields(authority, AUTHORITY_FIELDS)
    fields(anchor, ANCHOR_FIELDS)
    old, new = expected_refs(plan, "old"), expected_refs(plan, "new")
    authority_ref, anchor_ref = policy.refs
    for record, version, previous in (
        (authority, 2, old[authority_ref]), (anchor, 1, old[anchor_ref]),
    ):
        if (
            type(record["schema_version"]) is not int or record["schema_version"] != version
            or record["repository"] != policy.repository
            or type(record["issue"]) is not int or record["issue"] != policy.issue
            or type(record["sequence"]) is not int or record["sequence"] != plan["sequence"]
            or record["previous_object_id"] != previous
        ):
            raise RecordError("authority/anchor identity, predecessor or sequence mismatch")
    if anchor["authority_object_id"] != new[authority_ref]:
        raise RecordError("anchor does not bind exact new authority")
    if authority["signer"] != policy.signer:
        raise RecordError("authority key is not the installed external signer")
    if authority["ruleset_id"] != policy.ruleset_id or (
        not isinstance(authority["authorized_bypass_actors"], list)
        or sorted(map(canonical_json, authority["authorized_bypass_actors"]))
        != sorted(map(canonical_json, policy.authorized_bypass_actors))
    ):
        raise RecordError("authority protection policy was substituted")
    integer(authority["handoff_sequence"], 0, MAX_SEQUENCE)
    if authority["head_seal"] is not None:
        digest(authority["head_seal"])
    if not isinstance(authority["event"], dict) or not isinstance(authority["delivery_expectation"], dict):
        raise RecordError("authority requires typed event and delivery records")
    delivery = fields(authority["delivery_expectation"], {
        "repository_id", "repository_full_name", "immediate_base_branch",
        "immediate_base_oid", "delivery_branch", "head_repository_full_name",
    })
    if (
        type(delivery["repository_id"]) is not int or delivery["repository_id"] != policy.repository_id
        or delivery["repository_full_name"] != policy.repository
        or delivery["head_repository_full_name"] != policy.repository
    ):
        raise RecordError("delivery identity is not the installed repository")
    oid(delivery["immediate_base_oid"])
    for name in ("immediate_base_branch", "delivery_branch"):
        if not isinstance(delivery[name], str) or not 1 <= len(delivery[name]) <= 1024:
            raise RecordError("invalid frozen delivery branch")
    if plan["operation"] == "bootstrap":
        if (
            previous_authority is not None or previous_anchor is not None
            or authority["handoff_sequence"] != 0 or authority["head_seal"] is not None
            or authority["pr_binding"] is not None or authority["event"].get("kind") != "genesis"
        ):
            raise RecordError("invalid authority genesis")
    else:
        fields(previous_authority, AUTHORITY_FIELDS)
        fields(previous_anchor, ANCHOR_FIELDS)
        for record in (previous_authority, previous_anchor):
            if (
                record["repository"] != policy.repository or record["issue"] != policy.issue
                or type(record["sequence"]) is not int
                or record["sequence"] != plan["sequence"] - 1
            ):
                raise RecordError("remote sequence/issue mismatch")
        if previous_anchor["authority_object_id"] != old[authority_ref]:
            raise RecordError("old authority/anchor pair is inconsistent")
        for name in ("signer", "ruleset_id", "authorized_bypass_actors", "delivery_expectation"):
            if authority[name] != previous_authority[name]:
                raise RecordError("frozen authority policy changed")
        if plan["operation"] == "advance":
            if (
                authority["handoff_sequence"] != previous_authority["handoff_sequence"] + 1
                or authority["pr_binding"] != previous_authority["pr_binding"]
                or authority["head_seal"] is None
                or authority["head_seal"] == previous_authority["head_seal"]
                or authority["event"].get("kind") != "handoff"
            ):
                raise RecordError("invalid handoff advance")
        elif (
            previous_authority["pr_binding"] is not None
            or previous_authority["handoff_sequence"] < 1
            or previous_authority["head_seal"] is None
            or not isinstance(authority["pr_binding"], dict)
            or authority["handoff_sequence"] != previous_authority["handoff_sequence"]
            or authority["head_seal"] != previous_authority["head_seal"]
            or authority["event"].get("kind") != "pr_binding"
        ):
            raise RecordError("invalid one-time PR binding")
    attestation = fields(authority["publication_attestation"], ATTESTATION_FIELDS)
    wanted = {
        "source": "external-coordinator-service",
        "repository": policy.repository, "repository_id": policy.repository_id,
        "ruleset_source": "github-rulesets-api", "issue": policy.issue,
        "authority_object_id": old[authority_ref], "anchor_object_id": old[anchor_ref],
        "operation_nonce": plan["nonce"], "operation": plan["operation"],
        "coordinator_database_id": policy.actor_id,
    }
    if any(type(attestation[name]) is not type(value) or attestation[name] != value for name, value in wanted.items()):
        raise RecordError("publication attestation does not bind full plan")
    observed, issued = parse_utc(attestation["observed_at"]), parse_utc(plan["issued_at"])
    if not timedelta(0) <= issued - observed <= timedelta(seconds=2):
        raise RecordError("full plan was not signed from a fresh authority observation")
    event = authority["event"]
    expected_binding = {
        "new_head_seal": None, "history_carrier_digest": None,
        "history_receipt_digest": None, "pull_request_observation_digest": None,
        "binding_expectation": None,
    }
    if plan["operation"] == "advance":
        for name in ("history_carrier", "history_receipt"):
            if not isinstance(event.get(name), dict):
                raise RecordError("handoff publication requires receipt and carrier")
            expected_binding[name + "_digest"] = hashlib.sha256(canonical_json(event[name])).hexdigest()
        expected_binding["new_head_seal"] = authority["head_seal"]
        if (
            event.get("handoff_seal") != authority["head_seal"]
            or event["history_receipt"].get("seal") != authority["head_seal"]
        ):
            raise RecordError("handoff receipt/seal disagreement")
    elif plan["operation"] == "bind":
        observation = fields(authority["pr_binding"], PR_OBSERVATION_FIELDS)
        integer(observation["pull_request"], 1, MAX_SEQUENCE)
        integer(observation["repository_id"], 1, MAX_SEQUENCE)
        integer(observation["coordinator_database_id"], 1, MAX_SEQUENCE)
        for name in ("base_oid", "head_oid", "authority_object_id", "anchor_object_id"):
            oid(observation[name])
        pr_observed = parse_utc(observation["observed_at"])
        if (
            observation["source"] != "github-pull-request-api"
            or not parse_utc(observation["created_at"]) <= pr_observed <= issued
            or issued - pr_observed > timedelta(seconds=2)
            or observation["base_branch"] != delivery["immediate_base_branch"]
            or any(observation[name] != delivery["delivery_branch"] for name in (
                "head_branch", "expected_handoff_branch", "delivery_branch",
            ))
            or previous_authority["event"].get("candidate_sha") != observation["head_oid"]
        ):
            raise RecordError("PR observation does not bind the fresh frozen handoff")
        verify_signature(
            policy.signing_key, signed_payload(b"workflow-pilot-github-pr-observation-v1\0", observation),
            observation.get("signature"),
        )
        expected_binding["pull_request_observation_digest"] = hashlib.sha256(
            canonical_json(observation)
        ).hexdigest()
        try:
            expected_binding["binding_expectation"] = {
                "repository_id": delivery["repository_id"],
                "repository_full_name": delivery["repository_full_name"],
                "pull_request": observation["pull_request"], "state": "OPEN", "merged": False,
                "base_branch": delivery["immediate_base_branch"],
                "frozen_base_oid": delivery["immediate_base_oid"],
                "current_base_oid": observation["base_oid"],
                "head_branch": observation["head_branch"],
                "head_repository_full_name": delivery["head_repository_full_name"],
                "head_oid": observation["head_oid"],
                "coordinator_database_id": observation["coordinator_database_id"],
            }
        except KeyError as error:
            raise RecordError("incomplete signed PR binding") from error
        if (
            observation.get("authority_object_id") != old[authority_ref]
            or observation.get("anchor_object_id") != old[anchor_ref]
            or observation.get("repository_id") != policy.repository_id
            or observation.get("repository_full_name") != policy.repository
            or observation.get("head_repository_full_name") != policy.repository
            or observation.get("coordinator_database_id") != policy.actor_id
            or observation.get("state") != "OPEN" or observation.get("merged") is not False
        ):
            raise RecordError("PR binding targets another authority")
    if any(attestation[name] != value for name, value in expected_binding.items()):
        raise RecordError("publication attestation does not bind carrier/receipt/PR")
    rules = fields(attestation["ruleset_response"], {
        "id", "enforcement", "target", "include_refs", "exclude_refs",
        "update_restricted", "non_fast_forward_restricted", "deletion_restricted",
        "bypass_actors",
    })
    if (
        type(rules["id"]) is not int or rules["id"] != policy.ruleset_id
        or rules["enforcement"] != "active" or rules["target"] != "branch"
        or not isinstance(rules["include_refs"], list) or not all(isinstance(ref, str) for ref in rules["include_refs"])
        or sorted(rules["include_refs"]) != sorted(policy.refs) or rules["exclude_refs"] != []
        or any(rules[name] is not True for name in (
            "update_restricted", "non_fast_forward_restricted", "deletion_restricted",
        ))
        or not isinstance(rules["bypass_actors"], list)
        or sorted(map(canonical_json, rules["bypass_actors"]))
        != sorted(map(canonical_json, policy.authorized_bypass_actors))
    ):
        raise RecordError("signed ruleset does not protect both exact refs")
    verify_signature(
        policy.signing_key, signed_payload(ATTESTATION_DOMAIN, attestation), attestation["signature"],
    )


def validate_hello(hello: Any, deployment: str, response_key: dict, now: datetime) -> dict:
    fields(hello, {"protocol", "deployment_id", "session_nonce", "issued_at", "expires_at", "signature"})
    if hello["protocol"] != PROTOCOL or hello["deployment_id"] != deployment:
        raise RecordError("wrong broker protocol/deployment")
    digest(hello["session_nonce"])
    issued, expires = parse_utc(hello["issued_at"]), parse_utc(hello["expires_at"])
    if not issued <= now < expires or not timedelta(0) < expires - issued <= timedelta(seconds=MAX_LIFETIME):
        raise RecordError("stale broker session")
    verify_signature(response_key, signed_payload(HELLO_DOMAIN, hello), hello["signature"])
    return hello


def validate_response(
    response: Any, policy: Policy, response_key: dict, plan: dict,
    hello: dict, now: datetime, *, readback: bool = False,
) -> dict:
    fields(response, {
        "protocol", "deployment_id", "session_nonce", "request_digest", "nonce",
        "status", "refs", "observed_at", "completed_at", "deadline", "signature",
    })
    wanted = {
        "protocol": PROTOCOL, "deployment_id": policy.deployment_id,
        "session_nonce": hello["session_nonce"], "request_digest": plan_digest(plan),
        "nonce": plan["nonce"], "deadline": hello["expires_at"],
    }
    if any(response[name] != value for name, value in wanted.items()):
        raise RecordError("response is not bound to this request/session")
    observed, deadline = parse_utc(response["observed_at"]), parse_utc(response["deadline"])
    if not parse_utc(hello["issued_at"]) <= observed <= now < deadline:
        raise RecordError("broker response is stale or outside its deadline")
    verify_signature(response_key, signed_payload(RESPONSE_DOMAIN, response), response["signature"])
    if not isinstance(response["status"], str) or response["status"] not in {"published", "rejected", "uncertain", "not_found"}:
        raise RecordError("unknown broker result")
    refs = fields(response["refs"], set(policy.refs))
    for value in refs.values():
        oid(value, nullable=True)
    if response["status"] == "published":
        completed = parse_utc(response["completed_at"])
        if (
            refs != expected_refs(plan, "new")
            or not parse_utc(plan["issued_at"]) <= completed < parse_utc(plan["expires_at"])
            or completed > observed
            or (not readback and now >= parse_utc(plan["expires_at"]))
        ):
            raise RecordError("published result lacks exact timely remote readback")
    elif response["completed_at"] is not None:
        raise RecordError("failed result claims completion")
    return response
