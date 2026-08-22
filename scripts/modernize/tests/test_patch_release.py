"""Synthetic contract tests for issue #49's patch-only release tooling."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.modernize import bps_patch, patch_release


ROOT = Path(__file__).resolve().parents[3]


def synthetic_base() -> bytes:
    data = bytearray(0x400)
    data[0xA0:0xAC] = b"FIREEMBLEM2E"
    data[0xAC:0xB0] = b"BE8E"
    data[0xB0:0xB2] = b"01"
    data[0xB2] = 0x96
    data[0xBC] = 0
    data[0xBD] = patch_release.BASE_CHECKSUM
    return bytes(data)


def synthetic_contract(data: bytes) -> patch_release.BaseContract:
    return patch_release.BaseContract(
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        sha1=hashlib.sha1(data).hexdigest(),
    )


def profile_metadata(commit: str) -> dict:
    return {
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
        "config_fingerprint": "0123456789abcdef",
    }


class BpsTests(unittest.TestCase):
    def test_target_read_patch_round_trips_deterministically(self):
        source = b"approved base"
        target = b"expanded target with a different length"
        first = bps_patch.create_patch(source, target)
        self.assertEqual(first, bps_patch.create_patch(source, target))
        self.assertTrue(first.startswith(b"BPS1"))
        self.assertEqual(bps_patch.apply_patch(source, first), target)

    def test_corrupt_patch_and_wrong_base_fail_closed(self):
        source = b"base"
        patch = bps_patch.create_patch(source, b"target")
        with self.assertRaises(bps_patch.BpsError):
            bps_patch.apply_patch(b"wrong", patch)
        corrupt = patch[:-1] + bytes([patch[-1] ^ 1])
        with self.assertRaises(bps_patch.BpsError):
            bps_patch.apply_patch(source, corrupt)


class BaseContractTests(unittest.TestCase):
    def test_wrong_base_is_rejected_without_disclosing_content(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        with self.assertRaises(patch_release.PatchReleaseError) as context:
            patch_release.validate_base(base[:-1], contract)
        self.assertIn("size mismatch", str(context.exception))
        self.assertNotIn("FIREEMBLEM2E", str(context.exception))


class ArtifactTests(unittest.TestCase):
    def test_valid_three_file_artifact_is_deterministic_and_allowlisted(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        target = b"synthetic patched output"
        commit = "a" * 40
        metadata = profile_metadata(commit)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact"
            with mock.patch.object(patch_release, "validate_target"):
                manifest = patch_release.create_artifact(
                    base, target, metadata, artifact, commit, contract
                )
                self.assertEqual({item.name for item in artifact.iterdir()}, patch_release.ARTIFACT_FILES)
                self.assertEqual(manifest["patch"]["filename"], patch_release.PATCH_FILENAME)
                self.assertEqual(
                    json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))["commit"],
                    commit,
                )
                patch_release.verify_artifact(base, artifact, contract)

    def test_extra_file_or_wrong_base_prevents_verification(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        target = b"synthetic patched output"
        commit = "b" * 40
        metadata = profile_metadata(commit)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact"
            with mock.patch.object(patch_release, "validate_target"):
                patch_release.create_artifact(base, target, metadata, artifact, commit, contract)
                (artifact / "forbidden.bin").write_bytes(b"extra")
                with self.assertRaisesRegex(patch_release.PatchReleaseError, "allowlist mismatch"):
                    patch_release.verify_artifact(base, artifact, contract)

    def test_missing_item_cap_metadata_is_rejected(self):
        metadata = profile_metadata("c" * 40)
        del metadata["item_id_cap"]
        with self.assertRaisesRegex(patch_release.PatchReleaseError, "item_id_cap mismatch"):
            patch_release._validate_profile_metadata(metadata, "c" * 40)


class NamedProfileTests(unittest.TestCase):
    def test_named_profile_is_isolated_and_exact(self):
        modern_mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn(
            "MODERN_PATCH_RELEASE_ROOT := build/expansion-modern-all-locales-all-features",
            modern_mk,
        )
        self.assertIn(
            "GENERATED_DATA_OUT_DIR=$(MODERN_PATCH_RELEASE_GENERATED_DATA_DIR)",
            modern_mk,
        )
        for setting in (
            "MODERN_CONFIG=release",
            "MODERN_ABI=aapcs",
            "MODERN_ROM_SIZE=32M",
            "EXPANSION_ENABLED_LOCALES=en,ja,zh-Hans,fr,de,es,it",
            "EXPANSION_PSEUDO_LOCALE=0",
            "EXPANSION_MECHANICS_HOOKS=1",
            "EXPANSION_MECHANICS_SAMPLE=1",
            "EXPANSION_DANGER_OVERLAY_MENU=1",
            "EXPANSION_STARTER_CONTENT=1",
            "EXPANSION_AOE_REFERENCE=1",
            "EXPANSION_LOCALIZED_TEXT_AUTO_WRAP=1",
            "EXPANSION_CASUAL_MODE=1",
            "EXPANSION_BGM_CONTINUATION_POLICY=preserve",
            "FE8_ITEM_ID_CAP=0xCE",
        ):
            self.assertIn(setting, modern_mk)

    def test_starter_content_header_bare_alias_resolves_on_a_cold_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "generated-data"
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-n",
                    "items_expansion_content_text.h",
                    "EXPANSION_STARTER_CONTENT=1",
                    f"GENERATED_DATA_OUT_DIR={output_dir}",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("No rule to make target", result.stdout)
        self.assertIn(
            f"content-text --out-dir {output_dir}",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
