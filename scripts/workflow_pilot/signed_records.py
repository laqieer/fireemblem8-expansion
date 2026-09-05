"""Canonical, bounded signed-record primitives shared by trusted consumers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any


# This is also the public JSON Schema pattern, not datetime.fromisoformat's
# runtime-dependent (and deliberately more permissive) ISO 8601 grammar.
UTC_PATTERN = (
    r"^(?:(?:(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|"
    r"(?:0[48]|[2468][048]|[13579][26])00)-02-29)|"
    r"(?:(?!0000)[0-9]{4}-(?:(?:01|03|05|07|08|10|12)-"
    r"(?:0[1-9]|[12][0-9]|3[01])|(?:04|06|09|11)-"
    r"(?:0[1-9]|[12][0-9]|30)|02-(?:0[1-9]|1[0-9]|2[0-8]))))"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?Z(?![\s\S])"
)
UTC_RE = re.compile(UTC_PATTERN)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
OID_RE = re.compile(r"[0-9a-f]{40}")
COORDINATOR_KEY_DOMAIN = b"workflow-pilot-agent-coordinator-attestation-v2\0"
RSA_DIGEST_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class RecordError(ValueError):
    """A record is not in the closed public protocol."""


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise RecordError("expected canonical Gregorian RFC 3339 UTC timestamp")
    fraction = value[19:-1]
    microsecond = int(fraction[1:].ljust(6, "0")) if fraction else 0
    return datetime(
        int(value[:4]), int(value[5:7]), int(value[8:10]),
        int(value[11:13]), int(value[14:16]), int(value[17:19]),
        microsecond, timezone.utc,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo != timezone.utc:
        raise RecordError("clock must use UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ) + "\n"
        ).encode("ascii")
    except (ValueError, TypeError, RecursionError) as error:
        raise RecordError("invalid canonical JSON") from error


def strict_json(raw: bytes, maximum: int) -> Any:
    if not raw or len(raw) > maximum:
        raise RecordError("JSON size bound")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RecordError("duplicate JSON key")
            result[key] = value
        return result

    def reject_number(value):
        raise RecordError("signed JSON permits integers, not floats/constants")

    try:
        value = json.loads(
            raw.decode("ascii"), object_pairs_hook=pairs,
            parse_float=reject_number, parse_constant=reject_number,
        )
        if canonical_json(value) != raw:
            raise RecordError("noncanonical JSON bytes")
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise RecordError("invalid canonical JSON") from error


def fields(value: Any, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise RecordError("unexpected record fields")
    return value


def integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RecordError("integer outside protocol bounds")
    return value


def digest(value: Any) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RecordError("expected SHA-256 identity")
    return value


def oid(value: Any, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str) or OID_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise RecordError("expected nonzero lowercase SHA-1 Git object identity")
    return value


def canonical_base64(value: Any) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise RecordError("invalid signature encoding")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise RecordError("invalid signature encoding") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise RecordError("noncanonical signature encoding")
    return decoded


def public_key(value: Any) -> dict:
    key = fields(value, {"algorithm", "modulus_hex", "exponent"})
    modulus = key["modulus_hex"]
    if (
        key["algorithm"] != "rsa-pkcs1v15-sha256"
        or not isinstance(modulus, str)
        or re.fullmatch(r"[89a-f][0-9a-f]{511,1023}", modulus) is None
        or len(modulus) % 2
        or int(modulus[-1], 16) % 2 == 0
        or type(key["exponent"]) is not int
        or key["exponent"] != 65537
    ):
        raise RecordError("expected canonical RSA-2048..4096 public key / e=65537")
    return dict(key)


def coordinator_signer(value: Any) -> dict:
    signer = fields(value, {
        "algorithm", "modulus_hex", "exponent", "key_id", "service_identity", "isolation_attestation",
    })
    public_key({name: signer[name] for name in ("algorithm", "modulus_hex", "exponent")})
    if not isinstance(signer["service_identity"], str) or not 1 <= len(signer["service_identity"]) <= 256:
        raise RecordError("external signer service identity required")
    isolation = fields(signer["isolation_attestation"], {
        "kind", "private_key_in_implementation_namespace", "signing_api",
    })
    if isolation != {
        "kind": "external-isolated-service",
        "private_key_in_implementation_namespace": False,
        "signing_api": "single-use-terminal-attestation",
    } or isolation["private_key_in_implementation_namespace"] is not False:
        raise RecordError("signer must be external to candidate authority")
    expected = hashlib.sha256(
        COORDINATOR_KEY_DOMAIN + canonical_json(
            {name: member for name, member in signer.items() if name != "key_id"}
        )
    ).hexdigest()
    if signer["key_id"] != expected:
        raise RecordError("coordinator key identity does not bind public material")
    return signer


def signed_payload(domain: bytes, record: dict) -> bytes:
    return domain + canonical_json(
        {name: value for name, value in record.items() if name != "signature"}
    )


def verify_signature(key: dict, payload: bytes, encoded_signature: str) -> None:
    key = public_key(key)
    signature = canonical_base64(encoded_signature)
    modulus = int(key["modulus_hex"], 16)
    size = len(key["modulus_hex"]) // 2
    representative = int.from_bytes(signature, "big")
    if len(signature) != size or representative >= modulus:
        raise RecordError("signature does not verify")
    actual = pow(representative, key["exponent"], modulus).to_bytes(size, "big")
    tail = RSA_DIGEST_PREFIX + hashlib.sha256(payload).digest()
    expected = b"\0\1" + b"\xff" * (size - len(tail) - 3) + b"\0" + tail
    if not hmac.compare_digest(actual, expected):
        raise RecordError("signature does not verify")
