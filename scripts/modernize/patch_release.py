#!/usr/bin/env python3
"""Build and verify the issue #49 patch-only release artifact.

This module never downloads a base image and never logs its path, bytes, or
origin.  A trusted workflow may supply a legal local input; this tool validates
the immutable FE8U revision-0 contract before it produces any artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.modernize import bps_patch, verify_rom_header


ARTIFACT_FILES = frozenset(
    {
        "fireemblem8-expansion-all-locales-all-features-aapcs.bps",
        "manifest.json",
        "README.txt",
    }
)
PATCH_FILENAME = "fireemblem8-expansion-all-locales-all-features-aapcs.bps"
PROFILE_NAME = "modern-release-all-locales-all-features-aapcs"
SCHEMA_VERSION = 1
BASE_TITLE = "FIREEMBLEM2E"
BASE_GAME_CODE = "BE8E"
BASE_MAKER_CODE = "01"
BASE_FIXED_BYTE = 0x96
BASE_REVISION = 0
BASE_CHECKSUM = 0x9D
PRODUCER = bps_patch.IDENTITY


@dataclass(frozen=True)
class BaseContract:
    size: int
    sha256: str
    sha1: str
    title: str = BASE_TITLE
    game_code: str = BASE_GAME_CODE
    maker_code: str = BASE_MAKER_CODE
    fixed_byte: int = BASE_FIXED_BYTE
    revision: int = BASE_REVISION
    checksum: int = BASE_CHECKSUM


FE8U_REV0 = BaseContract(
    size=16_777_216,
    sha256="638cda9d9b72657220fbf7e7a500cd3b64d9686c36e8a56fca69d26d13886f2f",
    sha1="c25b145e37456171ada4b0d440bf88a19f4d509f",
)

PROFILE_SETTINGS = {
    "MODERN_CONFIG": "release",
    "MODERN_ABI": "aapcs",
    "MODERN_ROM_SIZE": "32M",
    "EXPANSION_ENABLED_LOCALES": "en,ja,zh-Hans,fr,de,es,it",
    "EXPANSION_DEFAULT_LOCALE": "en",
    "EXPANSION_PSEUDO_LOCALE": 0,
    "EXPANSION_MECHANICS_HOOKS": 1,
    "EXPANSION_MECHANICS_SAMPLE": 1,
    "EXPANSION_DANGER_OVERLAY_MENU": 1,
    "EXPANSION_STARTER_CONTENT": 1,
    "EXPANSION_AOE_REFERENCE": 1,
    "EXPANSION_LOCALIZED_TEXT_AUTO_WRAP": 1,
    "EXPANSION_CASUAL_MODE": 1,
    "EXPANSION_BGM_CONTINUATION_POLICY": "preserve",
    "FE8_ITEM_ID_CAP": "0xCE",
}


class PatchReleaseError(ValueError):
    """A release input, artifact, or identity contract is invalid."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _header(data: bytes) -> dict[str, object]:
    if len(data) < verify_rom_header.HEADER_END:
        raise PatchReleaseError("base validation failed: header is truncated")
    try:
        return {
            "title": verify_rom_header.decode_ascii_field(data[0xA0:0xAC], "title"),
            "game_code": verify_rom_header.decode_ascii_field(data[0xAC:0xB0], "game code"),
            "maker_code": verify_rom_header.decode_ascii_field(data[0xB0:0xB2], "maker code"),
            "fixed_byte": data[0xB2],
            "revision": data[0xBC],
            "checksum": data[0xBD],
            "computed_checksum": verify_rom_header.compute_checksum(data),
        }
    except verify_rom_header.RomHeaderError as error:
        raise PatchReleaseError(f"base validation failed: {error}") from error


def validate_base(data: bytes, contract: BaseContract = FE8U_REV0) -> None:
    if len(data) != contract.size:
        raise PatchReleaseError("base validation failed: size mismatch")
    if sha256(data) != contract.sha256:
        raise PatchReleaseError("base validation failed: SHA-256 mismatch")
    if hashlib.sha1(data).hexdigest() != contract.sha1:
        raise PatchReleaseError("base validation failed: SHA-1 mismatch")
    header = _header(data)
    expected = {
        "title": contract.title,
        "game_code": contract.game_code,
        "maker_code": contract.maker_code,
        "fixed_byte": contract.fixed_byte,
        "revision": contract.revision,
        "checksum": contract.checksum,
        "computed_checksum": contract.checksum,
    }
    for key, value in expected.items():
        if header[key] != value:
            raise PatchReleaseError(f"base validation failed: header {key} mismatch")


def _validate_profile_metadata(metadata: dict, commit: str) -> None:
    expected = {
        "build_commit": commit,
        "config_preset": "release",
        "abi": "aapcs",
        "rom_size_bytes": 32 * 1024 * 1024,
        "enabled_locales": ["en", "ja", "zh-Hans", "fr", "de", "es", "it"],
        "default_locale_id": 0,
        "pseudo_locale_enabled": 0,
        "mechanics_hooks": 1,
        "mechanics_sample": 1,
        "danger_overlay_menu": 1,
        "starter_content": 1,
        "aoe_reference": 1,
        "localized_text_auto_wrap": 1,
        "casual_mode": 1,
        "bgm_continuation_policy": "preserve",
        "item_id_cap": 0xCE,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise PatchReleaseError(f"profile validation failed: metadata {key} mismatch")
    fingerprint = metadata.get("config_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 16:
        raise PatchReleaseError("profile validation failed: metadata fingerprint missing")


def validate_target(data: bytes, metadata: dict) -> dict:
    if len(data) != 32 * 1024 * 1024:
        raise PatchReleaseError("target validation failed: size mismatch")
    try:
        facts = _header(data)
        if (
            facts["title"] != BASE_TITLE
            or facts["game_code"] != BASE_GAME_CODE
            or facts["maker_code"] != BASE_MAKER_CODE
            or facts["fixed_byte"] != BASE_FIXED_BYTE
            or facts["revision"] != BASE_REVISION
            or facts["checksum"] != facts["computed_checksum"]
        ):
            raise PatchReleaseError("target validation failed: GBA header mismatch")
        embedded = verify_rom_header.verify_expansion_metadata(data, metadata)
    except (verify_rom_header.ExpansionMetadataError, PatchReleaseError) as error:
        raise PatchReleaseError(str(error)) from error
    return {"header": facts, "embedded_metadata": embedded}


def artifact_manifest(base: bytes, target: bytes, patch: bytes, metadata: dict, commit: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "commit": commit,
        "profile": {"name": PROFILE_NAME, "settings": PROFILE_SETTINGS},
        "base": {
            "size": len(base),
            "sha256": sha256(base),
            "sha1": hashlib.sha1(base).hexdigest(),
            "header": {
                "title": BASE_TITLE,
                "game_code": BASE_GAME_CODE,
                "maker_code": BASE_MAKER_CODE,
                "fixed_byte": BASE_FIXED_BYTE,
                "revision": BASE_REVISION,
                "checksum": BASE_CHECKSUM,
            },
        },
        "output": {
            "size": len(target),
            "sha256": sha256(target),
            "metadata": metadata,
        },
        "patch": {
            "filename": PATCH_FILENAME,
            "size": len(patch),
            "sha256": sha256(patch),
            "producer_applier": PRODUCER,
        },
    }


def readme(commit: str) -> bytes:
    return (
        "Fire Emblem 8 Expansion patch-only artifact\n"
        f"Commit: {commit}\n"
        f"Profile: {PROFILE_NAME}\n\n"
        "This artifact distributes no ROM, ELF, map, save, base image, or base-image location.\n"
        "Obtain a legal Fire Emblem: The Sacred Stones (USA) revision 0 image independently.\n"
        "Before applying, verify the base identity in manifest.json. Apply the fixed BPS file with\n"
        f"the audited {PRODUCER} implementation (`python3 -m scripts.modernize.patch_release verify`).\n"
        "Verify the reconstructed output against manifest.json and the embedded ExpansionMetadata.\n"
        "The Actions artifact is retained for 30 days; source builds using the named profile are equivalent.\n"
    ).encode("ascii")


def create_artifact(
    base: bytes, target: bytes, metadata: dict, output_dir: Path, commit: str,
    contract: BaseContract = FE8U_REV0,
) -> dict:
    if output_dir.exists():
        raise PatchReleaseError("artifact validation failed: output directory already exists")
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise PatchReleaseError("artifact validation failed: commit must be a lowercase SHA-1")
    validate_base(base, contract)
    _validate_profile_metadata(metadata, commit)
    validate_target(target, metadata)
    patch = bps_patch.create_patch(base, target)
    if bps_patch.apply_patch(base, patch) != target:
        raise PatchReleaseError("patch validation failed: round trip mismatch")
    manifest = artifact_manifest(base, target, patch, metadata, commit)
    output_dir.mkdir(parents=True)
    (output_dir / PATCH_FILENAME).write_bytes(patch)
    (output_dir / "manifest.json").write_bytes(canonical_json(manifest))
    (output_dir / "README.txt").write_bytes(readme(commit))
    verify_artifact(base, output_dir, contract)
    return manifest


def verify_artifact(base: bytes, artifact_dir: Path, contract: BaseContract = FE8U_REV0) -> dict:
    if not artifact_dir.is_dir():
        raise PatchReleaseError("artifact validation failed: staging directory missing")
    actual_files = {path.name for path in artifact_dir.iterdir() if path.is_file()}
    if actual_files != ARTIFACT_FILES:
        raise PatchReleaseError("artifact validation failed: allowlist mismatch")
    manifest_bytes = (artifact_dir / "manifest.json").read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError as error:
        raise PatchReleaseError("artifact validation failed: manifest is malformed") from error
    if canonical_json(manifest) != manifest_bytes:
        raise PatchReleaseError("artifact validation failed: manifest is not canonical")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PatchReleaseError("artifact validation failed: manifest schema mismatch")
    if manifest.get("profile") != {"name": PROFILE_NAME, "settings": PROFILE_SETTINGS}:
        raise PatchReleaseError("artifact validation failed: profile mismatch")
    validate_base(base, contract)
    if manifest.get("base", {}).get("sha256") != sha256(base):
        raise PatchReleaseError("artifact validation failed: base digest mismatch")
    patch = (artifact_dir / PATCH_FILENAME).read_bytes()
    patch_record = manifest.get("patch", {})
    if patch_record.get("filename") != PATCH_FILENAME or patch_record.get("sha256") != sha256(patch):
        raise PatchReleaseError("artifact validation failed: patch digest mismatch")
    if patch_record.get("size") != len(patch) or patch_record.get("producer_applier") != PRODUCER:
        raise PatchReleaseError("artifact validation failed: patch identity mismatch")
    target = bps_patch.apply_patch(base, patch)
    output = manifest.get("output", {})
    if output.get("size") != len(target) or output.get("sha256") != sha256(target):
        raise PatchReleaseError("artifact validation failed: output digest mismatch")
    metadata = output.get("metadata")
    if not isinstance(metadata, dict):
        raise PatchReleaseError("artifact validation failed: metadata missing")
    _validate_profile_metadata(metadata, manifest.get("commit", ""))
    validate_target(target, metadata)
    if (artifact_dir / "README.txt").read_bytes() != readme(manifest["commit"]):
        raise PatchReleaseError("artifact validation failed: README mismatch")
    return manifest


def _metadata(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PatchReleaseError("profile validation failed: metadata is unreadable") from error
    if not isinstance(parsed, dict):
        raise PatchReleaseError("profile validation failed: metadata is not an object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--base", type=Path, required=True)
    create.add_argument("--target", type=Path, required=True)
    create.add_argument("--metadata", type=Path, required=True)
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--commit", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--base", type=Path, required=True)
    verify.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create_artifact(
                args.base.read_bytes(), args.target.read_bytes(), _metadata(args.metadata),
                args.output_dir, args.commit,
            )
        else:
            verify_artifact(args.base.read_bytes(), args.artifact_dir)
    except (PatchReleaseError, bps_patch.BpsError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("patch release artifact verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
