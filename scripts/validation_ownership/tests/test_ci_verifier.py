from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.validation_ownership import ci_verifier, reporter


ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = ROOT / "build" / "test-artifacts" / "validation-ownership"


class BasePinnedVerifierTests(unittest.TestCase):
    def test_base_verifier_step_is_a_pinned_security_boundary(self):
        step = (
            "    - name: Validate ownership with exact PR-base verifier\n"
            "      run: /usr/bin/git archive \"$EXPECTED_BASE_SHA\"\n"
        )
        workflow = "jobs:\n  host:\n    steps:\n" + step + (
            "    - name: Candidate tests\n"
            "      run: true\n"
        )
        self.assertEqual(ci_verifier._base_step(workflow), step)
        changed = workflow.replace(
            "$EXPECTED_BASE_SHA",
            "$EXPECTED_CANDIDATE_SHA",
        )
        self.assertNotEqual(
            ci_verifier._base_step(changed),
            ci_verifier._base_step(workflow),
        )

    def test_candidate_reporter_and_interceptor_cannot_replace_base_authority(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            environment = {
                "GIT_AUTHOR_NAME": "Verifier Test",
                "GIT_AUTHOR_EMAIL": "verifier@example.invalid",
                "GIT_COMMITTER_NAME": "Verifier Test",
                "GIT_COMMITTER_EMAIL": "verifier@example.invalid",
                "HOME": "/nonexistent",
                "PATH": "/usr/bin:/bin",
            }
            trusted = {
                "scripts/validation_ownership/reporter.py": "BASE_REPORTER\n",
                "scripts/validation_ownership/shell_interceptor.c": "BASE_INTERCEPTOR\n",
            }
            for path, content in trusted.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="ascii")
            (root / "candidate.txt").write_text("base\n", encoding="ascii")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"],
                cwd=root,
                env=environment,
                check=True,
            )
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            for path in trusted:
                (root / path).write_text(
                    "CANDIDATE_COMPROMISE\n",
                    encoding="ascii",
                )
            (root / "candidate.txt").write_text("candidate\n", encoding="ascii")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "candidate"],
                cwd=root,
                env=environment,
                check=True,
            )
            candidate_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            base_entries = reporter.git_tree_entries(root, base_sha)
            base_loader = reporter.AuthorityLoader(
                root,
                base_entries,
                base_sha,
            )
            entries, loader, changes = ci_verifier._pinned_loader(
                root,
                candidate_sha,
                base_loader,
                set(trusted),
            )
            self.assertEqual(changes, sorted(trusted))
            for path, content in trusted.items():
                self.assertEqual(
                    loader.read_blob(path, "trusted test"),
                    content.encode("ascii"),
                )
                self.assertEqual(entries[path], base_entries[path])
            self.assertEqual(
                loader.read_blob("candidate.txt", "candidate test"),
                b"candidate\n",
            )

    def test_base_gate_rejects_candidate_make_controls(self):
        gate = ROOT / "scripts/validation_ownership/ci_gate.mk"
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "VO_BASE_SHA": "1" * 40,
            "VO_CANDIDATE_SHA": "2" * 40,
            "VO_REPOSITORY_ROOT": str(ROOT),
            "VO_TRUSTED_ROOT": str(ROOT),
        }
        cases = (
            ["MAKECMDGOALS=", "-n", "validation-ownership-check"],
            ["SHELL=/bin/false", "validation-ownership-check"],
            ["validation-ownership-check", "other"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    ["/usr/bin/make", "-f", str(gate), *arguments],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
