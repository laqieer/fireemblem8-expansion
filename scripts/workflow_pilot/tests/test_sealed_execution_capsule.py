from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import sealed_execution_capsule as capsule


ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = ROOT / "build" / "test-artifacts" / "sealed-execution-capsule"
PROGRAM = """\
import json
import os
import sys
import time

from sealed_capsule import credentials, read_artifact, request
from trustedpkg.helper import trusted_value

mode = request.get("mode", "positive")
if mode == "positive":
    result = {
        "context": request["context"],
        "trusted": trusted_value(),
        "base": read_artifact("inputs/base.json", authority="base").decode("utf-8"),
        "origin": read_artifact("inputs/origin.json", authority="origin").decode("utf-8"),
        "head": read_artifact("inputs/head.json", authority="head").decode("utf-8"),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
elif mode == "authority":
    print(json.dumps({"authority_binding": "exact", "pass": False}, sort_keys=True))
elif mode == "credentials":
    print(json.dumps({"credentials": credentials}, sort_keys=True))
elif mode == "fds":
    inherited = []
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            os.fstat(fd)
        except (ValueError, OSError):
            continue
        inherited.append(fd)
    print(json.dumps({"fds": sorted(inherited)}))
elif mode == "unexpected-import":
    import candidate_only
elif mode == "unexpected-data":
    print(read_artifact("inputs/not-declared.json", authority="head"))
elif mode == "path-read":
    from pathlib import Path
    print(Path(request["path"]).read_text())
elif mode == "spec-fallback":
    import importlib.util
    spec = importlib.util.spec_from_file_location("candidate_only", request["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(json.dumps({"value": module.value}))
elif mode == "crash":
    os._exit(17)
elif mode == "timeout":
    time.sleep(30)
elif mode == "fork-timeout":
    child = os.fork()
    if child == 0:
        time.sleep(30)
        os._exit(0)
    os.write(1, ('{"child_pid":%d' % child).encode("ascii"))
    time.sleep(30)
elif mode == "fork-success":
    child = os.fork()
    if child == 0:
        os.close(1)
        os.close(2)
        time.sleep(30)
        os._exit(0)
    print(json.dumps({"child_pid": child}))
elif mode == "oversized":
    os.write(1, b'{"value":"' + b"x" * (2 * 1024 * 1024) + b'"}')
elif mode == "malformed":
    os.write(1, b'{"pass":NaN}')
elif mode == "partial":
    os.write(1, b'{"pass":')
else:
    raise RuntimeError("unknown test mode")
"""
FORGED_PROGRAM = """\
import json
print(json.dumps({"authority_binding": "forged", "pass": True}, sort_keys=True))
"""


def run_git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        (
            capsule.GIT,
            "--no-replace-objects",
            "-C",
            str(root),
            *arguments,
        ),
        env={
            "HOME": str(root),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
        },
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class SealedExecutionCapsuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        capsule._require_platform()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
        TEST_ROOT.mkdir(parents=True)
        cls.repository = TEST_ROOT / "authority"
        cls.repository.mkdir()
        run_git(cls.repository, "init", "-q")
        run_git(cls.repository, "config", "user.email", "capsule@example.com")
        run_git(cls.repository, "config", "user.name", "Capsule Test")
        (cls.repository / "trustedpkg").mkdir()
        (cls.repository / "inputs").mkdir()
        (cls.repository / "trusted_program.py").write_text(PROGRAM, encoding="utf-8")
        (cls.repository / "trusted_checker.py").write_text(PROGRAM, encoding="utf-8")
        (cls.repository / "trustedpkg" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        (cls.repository / "trustedpkg" / "helper.py").write_text(
            "def trusted_value():\n    return 'sealed-module'\n",
            encoding="utf-8",
        )
        (cls.repository / "inputs" / "base.json").write_text(
            "base-exact", encoding="utf-8"
        )
        (cls.repository / "inputs" / "origin.json").write_text(
            "origin-old", encoding="utf-8"
        )
        (cls.repository / "inputs" / "head.json").write_text(
            "head-old", encoding="utf-8"
        )
        (cls.repository / "candidate_only.py").write_text(
            "value = 'materialized'\n", encoding="utf-8"
        )
        run_git(cls.repository, "add", ".")
        run_git(cls.repository, "commit", "-q", "-m", "base")
        cls.base_sha = run_git(cls.repository, "rev-parse", "HEAD").decode().strip()
        (cls.repository / "inputs" / "origin.json").write_text(
            "origin-exact", encoding="utf-8"
        )
        run_git(cls.repository, "add", "inputs/origin.json")
        run_git(cls.repository, "commit", "-q", "-m", "origin")
        cls.origin_sha = run_git(cls.repository, "rev-parse", "HEAD").decode().strip()
        (cls.repository / "inputs" / "head.json").write_text(
            "head-exact", encoding="utf-8"
        )
        run_git(cls.repository, "add", "inputs/head.json")
        run_git(cls.repository, "commit", "-q", "-m", "head")
        cls.head_sha = run_git(cls.repository, "rev-parse", "HEAD").decode().strip()
        cls.specs = (
            capsule.ArtifactSpec(
                "base", cls.base_sha, "trusted_program.py", "program"
            ),
            capsule.ArtifactSpec(
                "base", cls.base_sha, "trusted_checker.py", "program"
            ),
            capsule.ArtifactSpec(
                "base",
                cls.base_sha,
                "trustedpkg/__init__.py",
                "package",
                "trustedpkg",
            ),
            capsule.ArtifactSpec(
                "base",
                cls.base_sha,
                "trustedpkg/helper.py",
                "module",
                "trustedpkg.helper",
            ),
            capsule.ArtifactSpec(
                "base", cls.base_sha, "inputs/base.json", "data"
            ),
            capsule.ArtifactSpec(
                "origin", cls.origin_sha, "inputs/origin.json", "data"
            ),
            capsule.ArtifactSpec(
                "head", cls.head_sha, "inputs/head.json", "data"
            ),
        )
        cls.bundle = capsule.build_artifact_bundle(cls.repository, cls.specs)
        parsed = capsule.validate_artifact_bundle(
            cls.bundle.payload, expected_artifact_ids=cls.bundle.artifact_ids
        )
        cls.program_ids = {
            record["path"]: record["artifact_id"]
            for record in parsed["artifacts"]
            if record["role"] == "program"
        }

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def execute(self, mode: str = "positive", context: str = "outer", **kwargs):
        return capsule.execute_capsule(
            self.bundle,
            program_artifact_id=self.program_ids["trusted_program.py"],
            request={"mode": mode, "context": context, **kwargs},
            timeout=0.8 if "timeout" in mode else 10,
        )

    def test_old_path_launch_reproduces_pr189_swap_boundary(self):
        path = self.repository / "trusted_program.py"
        trusted = run_git(
            self.repository, "show", f"{self.base_sha}:trusted_program.py"
        )
        forged = FORGED_PROGRAM.encode("utf-8")
        original = path.read_bytes()
        self.assertEqual(hashlib.sha256(trusted).digest(), hashlib.sha256(original).digest())
        path.write_bytes(forged)
        try:
            completed = subprocess.run(
                [capsule.PYTHON, "-I", str(path)],
                input=b"{}\n",
                capture_output=True,
                check=True,
            )
        finally:
            path.write_bytes(original)
        self.assertEqual(
            json.loads(completed.stdout),
            {"authority_binding": "forged", "pass": True},
        )
        self.assertEqual(path.read_bytes(), trusted)

    def test_exact_bundle_is_deterministic_and_bound_to_executed_bytes(self):
        rebuilt = capsule.build_artifact_bundle(self.repository, self.specs)
        self.assertEqual(rebuilt, self.bundle)
        first = self.execute(context="outer")
        second = self.execute(context="outer")
        self.assertEqual(first, second)
        self.assertEqual(
            first.output,
            {
                "base": "base-exact",
                "context": "outer",
                "head": "head-exact",
                "origin": "origin-exact",
                "trusted": "sealed-module",
            },
        )
        self.assertEqual(
            first.receipt["program_sha256"],
            hashlib.sha256(PROGRAM.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            first.receipt["artifact_bundle_sha256"],
            hashlib.sha256(self.bundle.payload).hexdigest(),
        )
        self.assertEqual(
            first.receipt_sha256,
            hashlib.sha256(capsule.normalized_json(first.receipt)).hexdigest(),
        )

    def test_all_former_path_roles_are_immutable_after_construction(self):
        paths = [
            self.repository / "trusted_program.py",
            self.repository / "trusted_checker.py",
            self.repository / "trustedpkg" / "__init__.py",
            self.repository / "trustedpkg" / "helper.py",
            self.repository / "inputs" / "base.json",
            self.repository / "inputs" / "origin.json",
            self.repository / "inputs" / "head.json",
        ]
        originals = {path: path.read_bytes() for path in paths}

        def swap_paths() -> None:
            for path in paths:
                path.unlink()
                path.symlink_to(self.repository / "candidate_only.py")

        try:
            result = capsule.execute_capsule(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "positive", "context": "artifact"},
                _before_spawn=swap_paths,
            )
        finally:
            for path, content in originals.items():
                path.unlink(missing_ok=True)
                path.write_bytes(content)
        self.assertEqual(result.output["trusted"], "sealed-module")
        self.assertEqual(result.output["base"], "base-exact")
        self.assertEqual(result.output["origin"], "origin-exact")
        self.assertEqual(result.output["head"], "head-exact")

    def test_outer_inner_member_remote_local_and_artifact_roles_share_capsule(self):
        contexts = (
            "outer-checker",
            "inner-assertion",
            "member-action",
            "member-generated",
            "member-lifecycle",
            "member-resource",
            "member-wire",
            "remote-round",
            "local-remediation",
            "artifact-read",
        )
        for context in contexts:
            with self.subTest(context=context):
                result = self.execute(context=context)
                self.assertEqual(result.output["context"], context)
                self.assertEqual(result.output["trusted"], "sealed-module")

    def test_forged_assertion_and_outer_checker_path_swaps_cannot_pass(self):
        for program_path in ("trusted_program.py", "trusted_checker.py"):
            with self.subTest(program=program_path):
                materialized = self.repository / program_path
                original = materialized.read_bytes()

                def swap() -> None:
                    materialized.write_text(FORGED_PROGRAM, encoding="utf-8")

                try:
                    result = capsule.execute_capsule(
                        self.bundle,
                        program_artifact_id=self.program_ids[program_path],
                        request={"mode": "authority"},
                        _before_spawn=swap,
                    )
                finally:
                    materialized.write_bytes(original)
                self.assertEqual(
                    result.output,
                    {"authority_binding": "exact", "pass": False},
                )

    def mutated_bundle(self, mutate) -> capsule.ArtifactBundle:
        data = json.loads(self.bundle.payload)
        mutate(data)
        return capsule.ArtifactBundle(
            capsule.normalized_json(data),
            self.bundle.artifact_ids,
        )

    def test_closed_bundle_rejects_membership_and_metadata_mutations(self):
        cases = {
            "missing": lambda data: data["artifacts"].pop(),
            "extra": lambda data: data["artifacts"].append(
                copy.deepcopy(data["artifacts"][-1])
            ),
            "duplicate": lambda data: data["artifacts"].insert(
                0, copy.deepcopy(data["artifacts"][0])
            ),
            "wrong-mode": lambda data: data["artifacts"][0].update(mode="100755"),
            "wrong-blob": lambda data: data["artifacts"][0].update(
                blob_oid="0" * len(data["artifacts"][0]["blob_oid"])
            ),
            "wrong-role": lambda data: next(
                record
                for record in data["artifacts"]
                if record["role"] == "program"
            ).update(role="data"),
            "unknown-role": lambda data: data["artifacts"][0].update(role="unknown"),
            "content": lambda data: data["artifacts"][0].update(
                content_b64=base64.b64encode(b"mutated").decode("ascii")
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.execute_capsule(
                        self.mutated_bundle(mutate),
                        program_artifact_id=self.program_ids["trusted_program.py"],
                        request={"mode": "positive", "context": name},
                    )

    def test_missing_wrong_mode_blob_and_symlink_tree_entries_reject(self):
        blob = run_git(
            self.repository, "rev-parse", f"{self.base_sha}:trusted_program.py"
        ).decode().strip()
        cases = (
            capsule.ArtifactSpec(
                "base", self.base_sha, "missing.py", "program"
            ),
            capsule.ArtifactSpec(
                "base",
                self.base_sha,
                "trusted_program.py",
                "program",
                expected_mode="100755",
            ),
            capsule.ArtifactSpec(
                "base",
                self.base_sha,
                "trusted_program.py",
                "program",
                expected_blob_oid="0" * len(blob),
            ),
        )
        for spec in cases:
            with self.subTest(spec=spec):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.build_artifact_bundle(self.repository, [spec])

        (self.repository / "unsafe.py").symlink_to("trusted_program.py")
        run_git(self.repository, "add", "unsafe.py")
        run_git(self.repository, "commit", "-q", "-m", "symlink")
        symlink_sha = run_git(self.repository, "rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(capsule.CapsuleError, "unsafe Git tree"):
            capsule.build_artifact_bundle(
                self.repository,
                [capsule.ArtifactSpec("base", symlink_sha, "unsafe.py", "program")],
            )

    def test_exact_tree_authority_rejects_redirection_and_ignores_ambient_git(self):
        hostile = {
            "GIT_DIR": str(ROOT / ".git"),
            "GIT_OBJECT_DIRECTORY": str(ROOT / ".git" / "objects"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
            "GIT_CONFIG_VALUE_0": "999",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            rebuilt = capsule.build_artifact_bundle(self.repository, self.specs)
        self.assertEqual(rebuilt, self.bundle)

        alternates = self.repository / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(ROOT / ".git" / "objects") + "\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(capsule.CapsuleError, "alternate object"):
                capsule.build_artifact_bundle(self.repository, self.specs)
        finally:
            alternates.unlink()

    def test_unexpected_import_and_materialized_path_fallback_reject(self):
        with self.assertRaises(capsule.CapsuleExecutionError) as import_error:
            self.execute(mode="unexpected-import")
        self.assertIn(
            b"not present in sealed artifact closure",
            import_error.exception.stderr,
        )
        with self.assertRaises(capsule.CapsuleExecutionError) as data_error:
            self.execute(mode="unexpected-data")
        self.assertIn(
            b"artifact is not present in sealed closure",
            data_error.exception.stderr,
        )
        with self.assertRaises(capsule.CapsuleExecutionError) as path_error:
            self.execute(
                mode="path-read",
                path=str(self.repository / "candidate_only.py"),
            )
        self.assertIn(b"filesystem access is forbidden", path_error.exception.stderr)
        with self.assertRaises(capsule.CapsuleExecutionError) as spec_error:
            self.execute(
                mode="spec-fallback",
                path=str(self.repository / "candidate_only.py"),
            )
        self.assertIn(b"filesystem access is forbidden", spec_error.exception.stderr)

    def launch_with_descriptor_variant(self, variant: str) -> subprocess.CompletedProcess:
        parsed = capsule.validate_artifact_bundle(self.bundle.payload)
        program = capsule._program_from_bundle(
            parsed, self.program_ids["trusted_program.py"]
        )
        request = capsule.normalized_json({"mode": "authority"})
        nonce = "a" * 64
        fds: list[int] = []
        try:
            bootstrap_fd, bootstrap_digest = capsule._create_sealed_raw_descriptor(
                "bootstrap", capsule._BOOTSTRAP_SOURCE.encode("utf-8")
            )
            fds.append(bootstrap_fd)
            program_fd, program_digest = capsule._create_sealed_descriptor(
                "program", nonce, program
            )
            fds.append(program_fd)
            request_fd, request_digest = capsule._create_sealed_descriptor(
                "request", nonce, request
            )
            fds.append(request_fd)
            bundle_fd, bundle_digest = capsule._create_sealed_descriptor(
                "bundle", nonce, self.bundle.payload
            )
            fds.append(bundle_fd)
            pass_fds = list(fds)
            if variant == "unsealed":
                os.close(request_fd)
                fds.remove(request_fd)
                request_fd = os.memfd_create(
                    "unsealed-request", flags=os.MFD_ALLOW_SEALING
                )
                fds.append(request_fd)
                envelope = capsule._descriptor_envelope("request", nonce, request)
                capsule._write_all(request_fd, envelope)
                request_digest = hashlib.sha256(envelope).hexdigest()
            elif variant == "reused":
                request_fd = program_fd
                request_digest = program_digest
            elif variant == "wrong":
                os.close(request_fd)
                fds.remove(request_fd)
                request_fd = os.open("/dev/null", os.O_RDONLY)
                fds.append(request_fd)
                request_digest = "0" * 64
            elif variant == "unexpected":
                extra = os.open("/dev/null", os.O_RDONLY)
                fds.append(extra)
                pass_fds.append(extra)
            command = capsule._bootstrap_command(
                bootstrap_fd=bootstrap_fd,
                bootstrap_digest=bootstrap_digest,
                program_fd=program_fd,
                program_digest=program_digest,
                request_fd=request_fd,
                request_digest=request_digest,
                bundle_fd=bundle_fd,
                bundle_digest=bundle_digest,
                nonce=nonce,
                program_artifact_id=self.program_ids["trusted_program.py"],
            )
            return subprocess.run(
                command,
                cwd="/",
                env={"HOME": "/", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                close_fds=True,
                pass_fds=tuple(pass_fds),
                timeout=10,
            )
        finally:
            for fd in set(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_mutable_unsealed_reused_wrong_and_unexpected_fds_reject(self):
        for variant in ("unsealed", "reused", "wrong", "unexpected"):
            with self.subTest(variant=variant):
                completed = self.launch_with_descriptor_variant(variant)
                self.assertNotEqual(completed.returncode, 0)
        fd, _digest = capsule._create_sealed_descriptor(
            "request", "b" * 64, b"{}\n"
        )
        try:
            self.assertEqual(
                fcntl.fcntl(fd, fcntl.F_GET_SEALS) & capsule.REQUIRED_SEALS,
                capsule.REQUIRED_SEALS,
            )
            with self.assertRaises(OSError):
                os.write(fd, b"x")
        finally:
            os.close(fd)

    def test_timeout_crash_oversized_malformed_and_partial_leave_no_receipt(self):
        for mode in ("timeout", "crash", "oversized", "malformed", "partial"):
            with self.subTest(mode=mode):
                with self.assertRaises(
                    (capsule.CapsuleExecutionError, capsule.CapsuleError)
                ):
                    self.execute(mode=mode)

    def test_timeout_kills_process_group_and_closes_descriptors(self):
        before = len(os.listdir("/proc/self/fd"))
        with self.assertRaises(capsule.CapsuleExecutionError) as caught:
            self.execute(mode="fork-timeout")
        match = __import__("re").search(rb'"child_pid":(\d+)', caught.exception.stdout)
        self.assertIsNotNone(match)
        child_pid = int(match.group(1))
        for _ in range(50):
            if not pid_exists(child_pid):
                break
            time.sleep(0.02)
        self.assertFalse(pid_exists(child_pid))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_success_kills_closed_pipe_descendants(self):
        result = self.execute(mode="fork-success")
        child_pid = result.output["child_pid"]
        for _ in range(50):
            if not pid_exists(child_pid):
                break
            time.sleep(0.02)
        self.assertFalse(pid_exists(child_pid))

    def test_parent_interruption_kills_child_and_closes_descriptors(self):
        before = len(os.listdir("/proc/self/fd"))
        child_pid = None

        def interrupt(pid: int) -> None:
            nonlocal child_pid
            child_pid = pid
            raise KeyboardInterrupt

        with self.assertRaisesRegex(
            capsule.CapsuleExecutionError, "launch was interrupted"
        ):
            capsule.execute_capsule(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "timeout"},
                _after_spawn=interrupt,
            )
        self.assertIsNotNone(child_pid)
        self.assertFalse(pid_exists(child_pid))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_bounds_strict_json_and_fail_closed_platform(self):
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request=b'{"x":1,"x":2}',
            )
        with self.assertRaises(capsule.CapsuleError):
            capsule.build_artifact_bundle(
                self.repository,
                [self.specs[0]] * (capsule.MAX_ARTIFACTS + 1),
            )
        with mock.patch.object(capsule.sys, "platform", "darwin"):
            with self.assertRaisesRegex(capsule.CapsuleError, "require Linux"):
                capsule._require_platform()

    def test_credentials_are_added_only_as_non_runtime_control_names(self):
        result = capsule.execute_capsule(
            self.bundle,
            program_artifact_id=self.program_ids["trusted_program.py"],
            request={"mode": "credentials"},
            credentials={
                "GH_TOKEN": "secret-token",
                "PYTHONPATH": "/candidate/control",
            },
        )
        self.assertEqual(
            result.output,
            {
                "credentials": {
                    "GH_TOKEN": "secret-token",
                    "PYTHONPATH": "/candidate/control",
                }
            },
        )
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "credentials"},
                credentials={"lowercase": "invalid"},
            )

    def test_program_runs_after_authority_descriptors_are_closed(self):
        result = self.execute(mode="fds")
        self.assertEqual(result.output, {"fds": [0, 1, 2]})

    def test_sha256_git_object_format_when_supported(self):
        repository = TEST_ROOT / "sha256-authority"
        repository.mkdir()
        initialized = subprocess.run(
            [capsule.GIT, "-C", str(repository), "init", "--object-format=sha256", "-q"],
            env={
                "HOME": str(repository),
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            },
            capture_output=True,
        )
        if initialized.returncode != 0:
            self.skipTest("installed Git does not support SHA-256 repositories")
        run_git(repository, "config", "user.email", "capsule@example.com")
        run_git(repository, "config", "user.name", "Capsule Test")
        (repository / "program.py").write_text(
            'print(\'{"object_format":"sha256"}\')\n', encoding="utf-8"
        )
        run_git(repository, "add", "program.py")
        run_git(repository, "commit", "-q", "-m", "sha256")
        revision = run_git(repository, "rev-parse", "HEAD").decode().strip()
        bundle = capsule.build_artifact_bundle(
            repository,
            [capsule.ArtifactSpec("base", revision, "program.py", "program")],
        )
        parsed = capsule.validate_artifact_bundle(bundle.payload)
        record = parsed["artifacts"][0]
        self.assertEqual(record["object_format"], "sha256")
        self.assertEqual(len(record["blob_oid"]), 64)
        result = capsule.execute_capsule(
            bundle,
            program_artifact_id=record["artifact_id"],
            request={},
        )
        self.assertEqual(result.output, {"object_format": "sha256"})


if __name__ == "__main__":
    unittest.main()
