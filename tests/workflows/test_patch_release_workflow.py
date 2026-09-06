"""Build-once packaging evidence for the trusted-checkout CI model (#177)."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.modernize import bps_patch, patch_release
from scripts.modernize.tests.test_patch_release import profile_metadata, synthetic_base
from scripts.modernize.tests.test_verify_rom_header import (
    embed_metadata, make_metadata_dict, make_valid_header, vrh,
)
from scripts.upstream_port import verify
from scripts.workflow_pilot import candidate_evidence, pr_metadata


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build.yml"
PACKAGER = ROOT / "scripts/modernize/package_ci_patch.sh"
RELEASE = Path("build/expansion-modern-all-locales-all-features/release/aapcs")
BUILD_COMMAND = ("make", "expansion-modern-map-menu-presentation-check", "-j1")
PACKAGING_NAMES = ("Create and verify patch artifact", "Upload patch-only artifact")


def publisher_boundary_errors(workflow):
    try:
        verify._parse_workflow_structure_text(workflow)
    except ValueError as error:
        return [str(error)]
    return []


def workflow_jobs():
    _, _, jobs = verify._parse_workflow_structure_text(WORKFLOW.read_text())
    return {name: steps for name, _, steps in jobs}


class PatchReleaseWorkflowTests(unittest.TestCase):
    def test_packaging_is_in_the_existing_build_and_not_a_local_gate(self):
        jobs = workflow_jobs()
        self.assertEqual(len(verify.gates(jobs=1)), 30)
        self.assertNotIn("patch-release", jobs)
        steps = jobs["build"]
        canonical = []
        packages = []
        all_profile_commands = [
            command for job_steps in jobs.values()
            for _, _, fields in job_steps for command in dict(fields).get("run", ())
            if command == BUILD_COMMAND
        ]
        self.assertEqual(len(all_profile_commands), 1)
        for index, (role, name, fields) in enumerate(steps):
            fields = dict(fields)
            if BUILD_COMMAND in fields.get("run", ()):
                canonical.append(index)
            if role == "publisher":
                packages.append((index, name, fields))
        self.assertEqual(len(canonical), 1)
        self.assertEqual([name for _, name, _ in packages], list(PACKAGING_NAMES))
        self.assertTrue(all(index > canonical[0] for index, _, _ in packages))
        self.assertEqual(
            dict(packages[0][2]["env"]),
            {
                "BASEROM_URL": "${{ secrets.BASEROM_URL }}",
                "PATCH_COMMIT": "${{ needs.event-identity.outputs.fallback_sha }}",
                "PATCH_ARTIFACT_DIR": "${{ runner.temp }}/patch-artifact",
            },
        )
        upload = dict(packages[1][2]["with"])
        self.assertEqual(upload["retention-days"], "30")
        self.assertEqual(upload["path"], "${{ runner.temp }}/patch-artifact")
        self.assertEqual(upload["if-no-files-found"], "error")
        self.assertEqual(
            upload["name"],
            "modern-release-all-locales-all-features-aapcs-bps-${{ needs.event-identity.outputs.fallback_sha }}",
        )
        checkout = next(dict(fields) for _, _, fields in steps
                        if dict(fields).get("uses") == verify._CHECKOUT_USES)
        self.assertEqual(dict(checkout["with"])["persist-credentials"], "false")

    def test_current_and_historical_pr_job_shapes_require_every_real_check(self):
        from types import SimpleNamespace

        def record(names, legacy=False):
            contexts = [
                {"job_id": name, "name": name, "conclusion": "success"}
                for name in names
            ]
            if legacy:
                contexts.append({"job_id": "patch-release", "name": "patch-release", "conclusion": "skipped"})
            return {
                "run_id": 1, "event": "pull_request",
                "head_sha": "a" * 40, "base_sha": "b" * 40, "contexts": contexts,
            }

        for legacy in (False, True):
            run = record(candidate_evidence.KNOWN_JOB_IDS, legacy)
            self.assertTrue(candidate_evidence.evaluate_candidate_runs(
                [run], head_sha="a" * 40, base_sha="b" * 40,
            ).eligible)
            jobs = tuple(SimpleNamespace(
                name=c["name"], status="completed", conclusion=c["conclusion"],
                runner_name=None if c["conclusion"] == "skipped" else "runner",
            ) for c in run["contexts"])
            self.assertEqual(pr_metadata._run_mode(jobs, run_id=1, status="completed"), "full")
            for name in candidate_evidence.KNOWN_JOB_IDS:
                with self.subTest(legacy=legacy, missing=name):
                    changed = record(candidate_evidence.KNOWN_JOB_IDS - {name}, legacy)
                    with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                        candidate_evidence.evaluate_candidate_runs(
                            [changed], head_sha="a" * 40, base_sha="b" * 40,
                        )
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata._run_mode(
                            tuple(j for j in jobs if j.name != name), run_id=1, status="completed",
                        )
        for conclusion in ("failure", "success"):
            run = record(candidate_evidence.KNOWN_JOB_IDS, True)
            run["contexts"][-1]["conclusion"] = conclusion
            with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                candidate_evidence.evaluate_candidate_runs(
                    [run], head_sha="a" * 40, base_sha="b" * 40,
                )

    def test_packaging_and_upload_require_successful_authenticated_master_push(self):
        jobs = workflow_jobs()
        conditions = [
            dict(fields)["if"] for role, _, fields in jobs["build"]
            if role == "publisher"
        ]
        self.assertEqual(len(conditions), 2)
        facts = {
            "success()": True,
            "github.event_name": "push",
            "github.repository": "laqieer/fireemblem8-expansion",
            "github.ref": "refs/heads/master",
            "needs.event-identity.result": "success",
            "needs.event-identity.outputs.fallback_kind": "push",
            "needs.event-identity.outputs.fallback_sha": "a" * 40,
            "github.event.after": "a" * 40,
            "github.sha": "a" * 40,
        }

        def selected(expression, values):
            expression = expression.removeprefix("${{").removesuffix("}}").strip()
            terms = expression.split(" && ")
            for term in terms:
                if term == "success()":
                    if not values[term]:
                        return False
                    continue
                left, right = term.split(" == ")
                expected = ast.literal_eval(right) if right.startswith("'") else values[right]
                if values[left] != expected:
                    return False
            return True

        for condition in conditions:
            self.assertTrue(selected(condition, facts))
            for key, value in (
                ("success()", False), ("github.event_name", "pull_request"),
                ("github.event_name", "pull_request_target"), ("github.event_name", "workflow_dispatch"),
                ("github.repository", "fork-owner/fireemblem8-expansion"),
                ("github.ref", "refs/heads/topic"), ("needs.event-identity.result", "failure"),
                ("needs.event-identity.outputs.fallback_kind", "pull_request"),
                ("needs.event-identity.outputs.fallback_sha", "b" * 40),
                ("github.event.after", "b" * 40), ("github.sha", "b" * 40),
            ):
                with self.subTest(key=key, value=value):
                    self.assertFalse(selected(condition, {**facts, key: value}))

    def test_mirrored_contract_rejects_publication_drift(self):
        workflow = WORKFLOW.read_text()
        for old, new in (
            ("bash scripts/modernize/package_ci_patch.sh", "make expansion-modern-map-menu-presentation-check -j1"),
            ("${{ secrets.BASEROM_URL }}", "${{ github.token }}"),
            ("path: ${{ runner.temp }}/patch-artifact", "path: build"),
            ("retention-days: 30", "retention-days: 90"),
            (verify._PUBLISHER_CONDITION, "${{ always() }}"),
            ("name: Upload patch-only artifact", "name: Upload patch-only artifact\n      continue-on-error: true"),
        ):
            with self.subTest(mutation=old):
                changed = workflow.replace(old, new)
                self.assertNotEqual(changed, workflow)
                self.assertTrue(publisher_boundary_errors(changed))


class PackagingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        cls.base = synthetic_base() + b"PRIVATE-BASE-CONTENT-NOT-FOR-PUBLICATION"
        cls.metadata = make_metadata_dict(
            **profile_metadata(cls.commit), enabled_locale_mask=127,
        )
        rom = make_valid_header(patch_release.TARGET_ROM_SIZE)
        rom[0xBC] = 0
        rom[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(rom))
        cls.target = bytes(embed_metadata(rom, cls.metadata))

    def setUp(self):
        artifact_root = ROOT / "build/test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="build-once-", dir=artifact_root))
        self.addCleanup(shutil.rmtree, self.directory)
        self.release = self.directory / RELEASE
        (self.release / "generated").mkdir(parents=True)
        (self.release / "fireemblem8.gba").write_bytes(self.target)
        (self.release / "generated/expansion_build_metadata.json").write_text(json.dumps(self.metadata))
        (self.directory / "base-fixture").write_bytes(self.base)
        self.temporary = self.directory / "private"
        self.temporary.mkdir()
        self.output = self.directory / "artifact"
        tools = self.directory / "tools"
        tools.mkdir()
        self.environment = dict(
            os.environ, PATH=str(tools) + ":" + os.environ["PATH"],
            PATCH_COMMIT=self.commit, PATCH_ARTIFACT_DIR=str(self.output),
            RUNNER_TEMP=str(self.temporary), BASEROM_URL="https://private.invalid/secret-marker",
            FIXTURE_ROOT=str(self.directory), REPOSITORY_ROOT=str(ROOT),
        )
        self.tool("make", 'raise SystemExit("packaging must not build")\n')
        self.tool("curl", """
import json, os, pathlib, signal, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
(root / "download-arguments.json").write_text(json.dumps(sys.argv[1:]))
path = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
path.write_bytes((root / "base-fixture").read_bytes())
(root / "private-mode").write_text(oct(path.parent.stat().st_mode & 0o777))
if os.environ.get("SIGNAL_DOWNLOAD"):
    os.kill(os.getppid(), int(os.environ["SIGNAL_DOWNLOAD"]))
if os.environ.get("FAIL_DOWNLOAD"):
    print(os.environ["BASEROM_URL"], file=sys.stderr)
    raise SystemExit(7)
""")
        self.tool("python3", """
import hashlib, json, os, pathlib, sys
if sys.argv[1:3] != ["-m", "scripts.modernize.patch_release"]:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
sys.path.insert(0, os.environ["REPOSITORY_ROOT"])
from scripts.modernize import patch_release
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
# Only the approved base identity is substituted; real header, target,
# metadata, BPS creation, application and artifact verification all execute.
data = %r
contract = patch_release.BaseContract(len(data), hashlib.sha256(data).hexdigest(), hashlib.sha1(data).hexdigest())
patch_release.create_artifact.__defaults__ = (contract,)
patch_release.verify_artifact.__defaults__ = (contract,)
base = pathlib.Path(sys.argv[sys.argv.index("--base") + 1])
assert base.stat().st_mode & 0o777 == 0o400
assert "BASEROM_URL" not in os.environ
with (root / "producer-calls").open("a") as calls:
    calls.write(sys.argv[3] + "\\n")
if sys.argv[3] == "verify" and os.environ.get("EXTRA_ARTIFACT"):
    (pathlib.Path(os.environ["PATCH_ARTIFACT_DIR"]) / "unexpected.txt").write_text("reject")
raise SystemExit(patch_release.main(sys.argv[3:]))
""" % self.base)

    def tool(self, name, source):
        path = self.directory / "tools" / name
        path.write_text("#!" + sys.executable + "\n" + source)
        path.chmod(0o755)

    def package(self, *, cleaned=True, **environment):
        completed = subprocess.run(
            ["bash", str(PACKAGER)], cwd=self.directory,
            env={**self.environment, **environment},
            capture_output=True, check=False, timeout=180,
        )
        if cleaned:
            self.assertEqual(list(self.temporary.iterdir()), [])
        for stream in (completed.stdout, completed.stderr):
            self.assertNotIn(b"secret-marker", stream)
            self.assertNotIn(b"PRIVATE-BASE-CONTENT", stream)
        return completed

    def test_real_create_verify_roundtrip_uses_existing_target_without_make(self):
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / "producer-calls").read_text().splitlines(), ["create", "verify"])
        self.assertEqual((self.directory / "private-mode").read_text(), "0o700")
        self.assertEqual({p.name for p in self.output.iterdir()}, patch_release.ARTIFACT_FILES)
        patch = (self.output / patch_release.PATCH_FILENAME).read_bytes()
        self.assertEqual(bps_patch.apply_patch(self.base, patch), self.target)
        self.assertEqual((self.release / "fireemblem8.gba").read_bytes(), self.target)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["commit"], self.commit)
        self.assertEqual(manifest["profile"]["name"], patch_release.PROFILE_NAME)
        for path in self.output.iterdir():
            data = path.read_bytes()
            self.assertNotIn(b"secret-marker", data)
            self.assertNotIn(self.base, data)
            self.assertNotIn(self.target, data)
        arguments = json.loads((self.directory / "download-arguments.json").read_text())
        self.assertEqual(arguments[arguments.index("--proto") + 1], "=https")
        self.assertEqual(arguments[arguments.index("--proto-redir") + 1], "=https")
        self.assertEqual(arguments[arguments.index("--max-filesize") + 1], str(16 * 1024 * 1024))

    def test_oversized_download_is_rejected_before_producer_reads_it(self):
        with (self.directory / "base-fixture").open("wb") as fixture:
            fixture.truncate(16 * 1024 * 1024 + 1)
        result = self.package()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "producer-calls").exists())
        self.assertFalse(self.output.exists())

    def test_private_file_cleanup_failure_is_visible(self):
        self.tool("rm", "raise SystemExit(1)\n")
        result = self.package(cleaned=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"private patch input cleanup failed", result.stderr)
        directories = list(self.temporary.iterdir())
        self.assertEqual(len(directories), 1)
        self.assertEqual((directories[0] / "base.gba").read_bytes(), self.base)

    def test_private_directory_cleanup_failure_is_visible(self):
        self.tool("rmdir", "raise SystemExit(1)\n")
        result = self.package(cleaned=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"private patch input cleanup failed", result.stderr)
        directories = list(self.temporary.iterdir())
        self.assertEqual(len(directories), 1)
        self.assertEqual(list(directories[0].iterdir()), [])

    def test_download_failure_is_private_and_cleans_partial_input(self):
        result = self.package(FAIL_DOWNLOAD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"download failed", result.stderr)
        self.assertFalse(self.output.exists())

    def test_signals_clean_partial_input_and_remain_failures(self):
        import signal
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                result = self.package(SIGNAL_DOWNLOAD=str(int(number)))
                self.assertEqual(result.returncode, 128 + number)
                self.assertFalse(self.output.exists())

    def test_missing_existing_outputs_fail_before_private_download(self):
        for path in (
            self.release / "fireemblem8.gba",
            self.release / "generated/expansion_build_metadata.json",
        ):
            saved = path.read_bytes()
            path.unlink()
            try:
                result = self.package()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "download-arguments.json").exists())
            finally:
                path.write_bytes(saved)

    def test_missing_base_configuration_fails_without_private_files(self):
        result = self.package(BASEROM_URL="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "download-arguments.json").exists())
        self.assertFalse(self.output.exists())

    def test_wrong_base_target_metadata_and_commit_fail_without_upload(self):
        for kind in ("base", "target", "embedded-commit", "metadata", "metadata-commit", "commit"):
            with self.subTest(kind=kind):
                (self.directory / "base-fixture").write_bytes(self.base)
                (self.release / "fireemblem8.gba").write_bytes(self.target)
                metadata = dict(self.metadata)
                if kind == "base":
                    (self.directory / "base-fixture").write_bytes(self.base[:-1] + b"?")
                if kind == "target":
                    (self.release / "fireemblem8.gba").write_bytes(self.target[:-1])
                if kind == "embedded-commit":
                    (self.release / "fireemblem8.gba").write_bytes(bytes(embed_metadata(
                        bytearray(self.target), {**self.metadata, "build_commit": "b" * 40},
                    )))
                if kind == "metadata":
                    metadata["config_preset"] = "debug"
                if kind == "metadata-commit":
                    metadata["build_commit"] = "b" * 40
                (self.release / "generated/expansion_build_metadata.json").write_text(json.dumps(metadata))
                result = self.package(PATCH_COMMIT="b" * 40 if kind == "commit" else self.commit)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_unexpected_artifact_fails_verification_and_cleans_base(self):
        result = self.package(EXTRA_ARTIFACT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"allowlist mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
