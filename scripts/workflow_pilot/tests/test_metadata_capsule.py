"""Closed metadata-event launcher integration with exact Git source authority."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.workflow_pilot import event_classifier, isolated_launcher, metadata_event, sealed_capsule
from scripts.workflow_pilot.tests.test_event_classifier import _launcher_command, _load_fixture
from scripts.workflow_pilot.tests.test_pr_metadata import REPOSITORY, _metadata_event_payload


ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    "scripts/workflow_pilot/__init__.py",
    "scripts/workflow_pilot/isolated_launcher.py",
    "scripts/workflow_pilot/sealed_capsule.py",
    "scripts/workflow_pilot/event_classifier.py",
    "scripts/workflow_pilot/metadata_event.py",
    "scripts/workflow_pilot/pr_metadata.py",
    "scripts/workflow_pilot/candidate_evidence.py",
)


def make_source_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                   "GIT_CONFIG_NOSYSTEM": "1"}
    for arguments in (
        ("init", "-q", "-b", "master"),
        ("config", "user.name", "Metadata capsule fixture"),
        ("config", "user.email", "metadata-capsule@example.invalid"),
        ("add", "."),
        ("commit", "-qm", "Metadata source fixture"),
    ):
        subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *arguments],
            env=environment, check=True, capture_output=True)


@unittest.skipUnless(sys.platform == "linux" and os.uname().machine == "x86_64",
                     "Linux x86-64 metadata capsule")
class MetadataCapsuleTests(unittest.TestCase):
    def setUp(self):
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="metadata-capsule-", dir=artifacts)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        make_source_fixture(self.root)
        self.event = _metadata_event_payload()
        self.event_path = self.root / "event.json"
        self.output = self.root / "output"
        self.event_path.write_text(json.dumps(self.event))

    def git(self, *args):
        return subprocess.check_output(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(self.root), *args],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": "/dev/null",
                 "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        ).decode().strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Metadata source fixture")

    def run_mode(self):
        return subprocess.run(
            ["/usr/bin/python3", "-I", str(self.root / SOURCES[1]), "attest-metadata-event",
             "--event-path", str(self.event_path), "--repository", REPOSITORY,
             "--run-id", "202", "--run-number", "11", "--run-attempt", "1",
             "--output", str(self.output)],
            cwd=self.root, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            capture_output=True, text=True, timeout=30,
        )

    def test_metadata_mode_uses_exact_helper_closure_after_worktree_substitution(self):
        for name in SOURCES[2:]:
            (self.root / name).write_text("raise RuntimeError('substituted metadata source')\n")
        result = self.run_mode()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = metadata_event.event_digest(
            self.event, repository=REPOSITORY, run_id=202, run_number=11, run_attempt=1)
        self.assertEqual(self.output.read_text(), f"digest={expected}\n")

    def test_both_sealed_modes_ignore_checkout_hash_and_runtime_module_shadows(self):
        marker = self.root / "shadow-executed"
        case = _load_fixture()["cases"][0]
        for module in ("hashlib", "hmac"):
            for mode in ("classify-event", "attest-metadata-event"):
                with self.subTest(module=module, mode=mode):
                    shadow = self.root / (module + ".py")
                    shadow.write_text(
                        f"with open({str(marker)!r},'w') as output:\n    output.write({module!r})\n"
                        "raise SystemExit(86)\n")
                    marker.unlink(missing_ok=True)
                    self.output.unlink(missing_ok=True)
                    try:
                        if mode == "attest-metadata-event":
                            self.event_path.write_text(json.dumps(self.event))
                            result = self.run_mode()
                            expected = metadata_event.event_digest(
                                self.event, repository=REPOSITORY, run_id=202,
                                run_number=11, run_attempt=1)
                            expected_output = f"digest={expected}\n"
                        else:
                            self.event_path.write_text(json.dumps(case["payload"]))
                            command = _launcher_command(case, self.event_path, self.output)
                            command[2] = str(self.root / SOURCES[1])
                            result = subprocess.run(
                                command, cwd=self.root,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                capture_output=True, text=True, timeout=30)
                            expected = event_classifier.classify_event(
                                case["event_name"], case["payload"], **case["runner"])
                            self.assertEqual(result.stdout, expected.canonical_json())
                            expected_output = "classification=" + expected.classification
                        self.assertEqual(result.returncode, 0,
                                         f"shadow_executed={marker.exists()}: {result.stderr}")
                        self.assertFalse(marker.exists(), f"{module} executed for {mode}")
                        self.assertIn(expected_output, self.output.read_text())
                    finally:
                        shadow.unlink()
                        marker.unlink(missing_ok=True)

    def test_missing_committed_helper_never_uses_a_worktree_or_legacy_proof(self):
        for name in SOURCES[2:]:
            with self.subTest(missing=name):
                source = (ROOT / name).read_bytes()
                (self.root / name).unlink()
                self.commit()
                (self.root / name).write_bytes(source)
                self.output.unlink(missing_ok=True)
                result = self.run_mode()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.commit()

    def test_only_named_sealed_event_modes_can_reach_bootstrap(self):
        with mock.patch.object(isolated_launcher, "_bootstrap_git", side_effect=AssertionError) as read:
            for mode in ("pr-metadata", "../metadata_event.py", "unknown"):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    isolated_launcher.run_sealed_classifier([], mode=mode)
            read.assert_not_called()

    def test_metadata_timestamp_dependency_is_explicit_not_a_generic_import_bypass(self):
        revision = self.git("rev-parse", "HEAD")
        request = {"payload": self.event, "repository": REPOSITORY,
                   "run_id": 202, "run_number": 11, "run_attempt": 1}
        for modules in ((), ("time",)):
            spec = sealed_capsule.CapsuleSpec(
                trees={"base": revision},
                programs={"metadata": "scripts/workflow_pilot/metadata_event.py"},
                modules=modules)
            with self.subTest(modules=modules), sealed_capsule.prepare(self.root, spec) as prepared:
                if not modules:
                    with self.assertRaisesRegex(sealed_capsule.CapsuleError, "outside sealed closure: time"):
                        prepared.execute("metadata", request)
                else:
                    self.assertEqual(prepared.execute("metadata", request).value,
                                     metadata_event.event_digest(**request))


if __name__ == "__main__":
    unittest.main()
