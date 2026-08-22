"""Synthetic contract tests for issue #49's patch-only release tooling."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    def test_source_and_target_reads_are_deterministic_and_require_base(self):
        source = b"unchanged-prefix-" * 8 + b"source" + b"-unchanged-tail" * 8
        target = b"unchanged-prefix-" * 8 + b"target" + b"-unchanged-tail" * 8
        first = bps_patch.create_patch(source, target)
        self.assertEqual(first, bps_patch.create_patch(source, target))
        self.assertTrue(first.startswith(b"BPS1"))
        self.assertEqual(bps_patch.apply_patch(source, first), target)
        self.assertNotIn(target, first)
        self.assertLess(len(first), len(target))
        with self.assertRaisesRegex(bps_patch.BpsError, "source checksum mismatch"):
            bps_patch.apply_patch(source[:-1] + b"?", first)

    def test_corrupt_patch_and_wrong_base_fail_closed(self):
        source = b"base"
        patch = bps_patch.create_patch(source, b"target")
        with self.assertRaises(bps_patch.BpsError):
            bps_patch.apply_patch(b"wrong", patch)
        corrupt = patch[:-1] + bytes([patch[-1] ^ 1])
        with self.assertRaises(bps_patch.BpsError):
            bps_patch.apply_patch(source, corrupt)

    def test_apply_cli_writes_a_distinct_requested_output(self):
        source = b"legal base"
        target = b"patched output"
        patch = bps_patch.create_patch(source, target)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "base.gba"
            patch_path = root / "artifact.bps"
            output_path = root / "patched.gba"
            source_path.write_bytes(source)
            patch_path.write_bytes(patch)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = bps_patch.main(
                    [
                        "apply",
                        "--source",
                        str(source_path),
                        "--patch",
                        str(patch_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(source_path.read_bytes(), source)
            self.assertEqual(output_path.read_bytes(), target)

    def test_apply_cli_rejects_source_and_symlink_alias_before_io(self):
        source = b"legal base"
        patch = bps_patch.create_patch(source, b"patched output")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "base.gba"
            patch_path = root / "artifact.bps"
            symlink_path = root / "base-alias.gba"
            source_path.write_bytes(source)
            patch_path.write_bytes(patch)
            symlink_path.symlink_to(source_path)

            for output_path in (source_path, symlink_path):
                with self.subTest(output=output_path.name):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(Path, "read_bytes") as read_bytes,
                        mock.patch.object(Path, "write_bytes") as write_bytes,
                        redirect_stdout(stdout),
                        redirect_stderr(stderr),
                    ):
                        result = bps_patch.main(
                            [
                                "apply",
                                "--source",
                                str(source_path),
                                "--patch",
                                str(patch_path),
                                "--output",
                                str(output_path),
                            ]
                        )
                    self.assertEqual(result, 1)
                    self.assertEqual(read_bytes.call_count, 0)
                    self.assertEqual(write_bytes.call_count, 0)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn("output path must differ from source", stderr.getvalue())
                    self.assertEqual(source_path.read_bytes(), source)
                    self.assertEqual(output_path.read_bytes(), source)
            self.assertTrue(symlink_path.is_symlink())

    def test_apply_cli_errors_write_stderr_without_mutating_paths(self):
        source = b"legal base"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "base.gba"
            invalid_patch_path = root / "invalid.bps"
            missing_patch_path = root / "missing.bps"
            source_path.write_bytes(source)
            invalid_patch_path.write_bytes(b"not a BPS patch")

            for patch_path in (invalid_patch_path, missing_patch_path):
                with self.subTest(patch=patch_path.name):
                    output_path = root / (patch_path.stem + ".gba")
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = bps_patch.main(
                            [
                                "apply",
                                "--source",
                                str(source_path),
                                "--patch",
                                str(patch_path),
                                "--output",
                                str(output_path),
                            ]
                        )
                    self.assertEqual(result, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertTrue(stderr.getvalue().startswith("error: "))
                    self.assertEqual(source_path.read_bytes(), source)
                    self.assertFalse(output_path.exists())


class BaseContractTests(unittest.TestCase):
    def test_wrong_base_is_rejected_without_disclosing_content(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        with self.assertRaises(patch_release.PatchReleaseError) as context:
            patch_release.validate_base(base[:-1], contract)
        self.assertIn("size mismatch", str(context.exception))
        self.assertNotIn("FIREEMBLEM2E", str(context.exception))

    def test_one_byte_base_mutation_is_rejected(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        mutated = bytearray(base)
        mutated[-1] ^= 1
        with self.assertRaisesRegex(patch_release.PatchReleaseError, "SHA-256 mismatch"):
            patch_release.validate_base(bytes(mutated), contract)

    def test_matching_digest_with_wrong_header_is_rejected(self):
        mutated = bytearray(synthetic_base())
        mutated[0xA0] = ord("X")
        data = bytes(mutated)
        with self.assertRaisesRegex(
            patch_release.PatchReleaseError, "base validation failed: header title mismatch"
        ):
            patch_release.validate_base(data, synthetic_contract(data))


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

    def test_extra_file_directory_or_symlink_prevents_verification(self):
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
                (artifact / "forbidden.bin").unlink()
                (artifact / "forbidden-dir").mkdir()
                with self.assertRaisesRegex(patch_release.PatchReleaseError, "allowlist mismatch"):
                    patch_release.verify_artifact(base, artifact, contract)
                (artifact / "forbidden-dir").rmdir()
                (artifact / "README.txt").unlink()
                os.symlink(artifact / "manifest.json", artifact / "README.txt")
                with self.assertRaisesRegex(patch_release.PatchReleaseError, "allowlist mismatch"):
                    patch_release.verify_artifact(base, artifact, contract)

    def test_manifest_requires_a_valid_commit_and_complete_base_record(self):
        base = synthetic_base()
        contract = synthetic_contract(base)
        target = b"synthetic patched output"
        commit = "d" * 40
        metadata = profile_metadata(commit)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "artifact"
            with mock.patch.object(patch_release, "validate_target"):
                patch_release.create_artifact(base, target, metadata, artifact, commit, contract)
                manifest_path = artifact / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                del manifest["commit"]
                manifest_path.write_bytes(patch_release.canonical_json(manifest))
                with self.assertRaisesRegex(patch_release.PatchReleaseError, "commit"):
                    patch_release.verify_artifact(base, artifact, contract)

                manifest["commit"] = commit
                for key, value in (
                    ("size", len(base) + 1),
                    ("sha256", "0" * 64),
                    ("sha1", "0" * 40),
                ):
                    with self.subTest(key=key):
                        manifest["base"][key] = value
                        manifest_path.write_bytes(patch_release.canonical_json(manifest))
                        with self.assertRaisesRegex(
                            patch_release.PatchReleaseError, "base record mismatch"
                        ):
                            patch_release.verify_artifact(base, artifact, contract)
                        manifest["base"] = patch_release._base_record(base)

                manifest["base"]["header"]["revision"] = 1
                manifest_path.write_bytes(patch_release.canonical_json(manifest))
                with self.assertRaisesRegex(patch_release.PatchReleaseError, "base record mismatch"):
                    patch_release.verify_artifact(base, artifact, contract)

    def test_missing_item_cap_metadata_is_rejected(self):
        metadata = profile_metadata("c" * 40)
        del metadata["item_id_cap"]
        with self.assertRaisesRegex(patch_release.PatchReleaseError, "item_id_cap mismatch"):
            patch_release.validate_profile_metadata(metadata, "c" * 40)

    def test_cli_read_errors_do_not_disclose_paths(self):
        stderr = io.StringIO()
        error = OSError(2, "No such file or directory", "/restricted/legal-base.gba")
        with mock.patch.object(Path, "read_bytes", side_effect=error), redirect_stderr(stderr):
            result = patch_release.main(
                [
                    "create",
                    "--base",
                    "/restricted/legal-base.gba",
                    "--target",
                    "/restricted/target.gba",
                    "--metadata",
                    "/restricted/metadata.json",
                    "--output-dir",
                    "/restricted/artifact",
                    "--commit",
                    "e" * 40,
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("base image unreadable", stderr.getvalue())
        self.assertNotIn("/restricted", stderr.getvalue())

    def test_generated_readme_distinguishes_validation_from_output_writing(self):
        text = patch_release.readme("f" * 40).decode("ascii")
        self.assertIn(
            "`python3 -m scripts.modernize.patch_release verify`; it reconstructs in memory and\n"
            "does not write an output ROM.",
            text,
        )
        self.assertIn(
            "python3 -m scripts.modernize.bps_patch apply --source /path/to/legal-fe8u-rev0 "
            "--patch fireemblem8-expansion-all-locales-all-features-aapcs.bps "
            "--output /path/to/patched-fireemblem8-expansion.gba",
            text,
        )
        self.assertNotIn("Apply the fixed BPS file with\n", text)


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

    def test_profile_make_contract_uses_the_public_metadata_validator(self):
        modern_mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn(
            "patch_release.validate_profile_metadata(metadata, metadata[\"build_commit\"])",
            modern_mk,
        )
        self.assertNotIn("patch_release._validate_profile_metadata(", modern_mk)

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
