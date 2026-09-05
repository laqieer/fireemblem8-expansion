from __future__ import annotations

import base64
import copy
import dataclasses
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

from sealed_capsule import read_artifact, request
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
elif mode == "fds":
    inherited = []
    for fd in range(3, 32):
        try:
            os.fstat(fd)
        except OSError:
            continue
        inherited.append(fd)
    print(json.dumps({"fds": sorted(inherited)}))
elif mode == "unexpected-import":
    __import__("candidate_only")
elif mode == "unexpected-data":
    print(read_artifact("inputs/not-declared.json", authority="head"))
elif mode == "path-read":
    print(open(request["path"], "r", encoding="utf-8").read())
elif mode == "spec-fallback":
    importlib = __import__("importlib")
    spec = importlib.util.spec_from_file_location("candidate_only", request["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(json.dumps({"value": module.value}))
elif mode == "subprocess":
    __import__("subprocess").run(["/bin/cat", "/etc/hostname"], check=True)
elif mode == "socket":
    __import__("socket").socket()
elif mode == "ctypes":
    __import__("ctypes")
elif mode == "mmap":
    __import__("mmap")
elif mode == "native-loader":
    importlib = __import__("importlib")
    machinery = __import__("importlib.machinery").machinery
    specification = machinery.ModuleSpec(
        "_ctypes",
        machinery.ExtensionFileLoader("_ctypes", request["path"]),
        origin=request["path"],
    )
    __import__("_imp").create_dynamic(specification)
elif mode == "fork":
    os.fork()
elif mode == "exec":
    os.execve("/usr/bin/python3", ["/usr/bin/python3", "-c", "print('forged')"], {})
elif mode == "setsid-double-fork":
    os.setsid()
    os.fork()
elif mode == "double-fork":
    os.fork()
    os.fork()
elif mode == "filesystem":
    write_blocked = False
    try:
        os.mkdir("/capsule-write")
    except OSError:
        write_blocked = True
    print(json.dumps({
        "environment": dict(os.environ),
        "etc_hostname": os.path.exists("/etc/hostname"),
        "proc": os.path.exists("/proc/self/environ"),
        "write_blocked": write_blocked,
    }, sort_keys=True))
elif mode == "import-hook":
    sys.meta_path[:] = []
    __import__("ctypes")
elif mode == "crash":
    os._exit(17)
elif mode == "timeout":
    time.sleep(30)
elif mode == "closed-stdio-timeout":
    os.close(1)
    os.close(2)
    time.sleep(30)
elif mode == "oversized":
    os.write(1, b'{"value":"' + b"x" * (2 * 1024 * 1024) + b'"}')
elif mode == "malformed":
    os.write(1, b'{"pass":NaN}')
elif mode == "partial":
    os.write(1, b'{"pass":')
elif mode == "stderr-success":
    os.write(2, b"unexpected diagnostic")
    print('{"pass":true}')
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
        (cls.repository / "candidate_program.py").write_text(
            FORGED_PROGRAM, encoding="utf-8"
        )
        run_git(cls.repository, "add", "candidate_program.py")
        run_git(cls.repository, "commit", "-q", "-m", "candidate program")
        cls.candidate_sha = run_git(
            cls.repository, "rev-parse", "HEAD"
        ).decode().strip()
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
        cls.stdlib_modules = (
            "json",
            "os",
            "socket",
            "subprocess",
            "sys",
            "time",
        )
        cls.bundle = capsule.build_artifact_bundle(
            cls.repository,
            cls.specs,
            stdlib_modules=cls.stdlib_modules,
        )
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
            self.repository,
            self.bundle,
            authority_map=self.bundle.authorities,
            stdlib_modules=self.stdlib_modules,
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
        rebuilt = capsule.build_artifact_bundle(
            self.repository,
            self.specs,
            stdlib_modules=self.stdlib_modules,
        )
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
        self.assertEqual(
            first.receipt["sandbox_profile"],
            "linux-x86_64-bwrap-pid-net-mount-seccomp-v1",
        )
        self.assertRegex(first.receipt["sandbox_launcher_sha256"], r"^[0-9a-f]{64}$")

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
                self.repository,
                self.bundle,
                authority_map=self.bundle.authorities,
                stdlib_modules=self.stdlib_modules,
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
                        self.repository,
                        self.bundle,
                        authority_map=self.bundle.authorities,
                        stdlib_modules=self.stdlib_modules,
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
            self.bundle.authorities,
            self.bundle.stdlib_modules,
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
                        self.repository,
                        self.mutated_bundle(mutate),
                        authority_map=self.bundle.authorities,
                        stdlib_modules=self.stdlib_modules,
                        program_artifact_id=self.program_ids["trusted_program.py"],
                        request={"mode": "positive", "context": name},
                    )

    def test_public_execution_rejects_self_authenticated_and_fake_authority(self):
        data = json.loads(self.bundle.payload)
        target = next(
            record for record in data["artifacts"] if record["role"] == "program"
        )
        forged = FORGED_PROGRAM.encode("utf-8")
        target["content_b64"] = base64.b64encode(forged).decode("ascii")
        target["sha256"] = hashlib.sha256(forged).hexdigest()
        target["blob_oid"] = "0" * len(target["blob_oid"])
        target["artifact_id"] = capsule._artifact_id(target)
        policies = capsule._authority_policies(data["artifacts"])
        data["authorities"] = [
            capsule._policy_json(policy) for policy in policies
        ]
        forged_bundle = capsule.ArtifactBundle(
            capsule.normalized_json(data),
            tuple(record["artifact_id"] for record in data["artifacts"]),
            policies,
            self.stdlib_modules,
        )
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.repository,
                forged_bundle,
                authority_map=forged_bundle.authorities,
                stdlib_modules=self.stdlib_modules,
                program_artifact_id=target["artifact_id"],
                request={"mode": "authority"},
            )

        wrong_tree = dataclasses.replace(
            self.bundle.authorities[0],
            tree="0" * len(self.bundle.authorities[0].tree),
        )
        zero_revision = dataclasses.replace(
            self.bundle.authorities[0],
            revision="0" * len(self.bundle.authorities[0].revision),
        )
        wrong_format = dataclasses.replace(
            self.bundle.authorities[0],
            object_format="sha256",
            revision="0" * 64,
            tree="0" * 64,
        )
        for changed in (wrong_tree, zero_revision, wrong_format):
            authority_map = (changed, *self.bundle.authorities[1:])
            with self.subTest(policy=changed):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.execute_capsule(
                        self.repository,
                        self.bundle,
                        authority_map=authority_map,
                        stdlib_modules=self.stdlib_modules,
                        program_artifact_id=self.program_ids["trusted_program.py"],
                        request={"mode": "authority"},
                    )

    def test_mixed_revision_and_candidate_under_base_authority_reject(self):
        mixed_specs = list(self.specs)
        mixed_specs.append(
            capsule.ArtifactSpec(
                "base", self.origin_sha, "inputs/origin.json", "data"
            )
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "one revision"):
            capsule.build_artifact_bundle(
                self.repository,
                mixed_specs,
                stdlib_modules=self.stdlib_modules,
            )

        candidate_bundle = capsule.build_artifact_bundle(
            self.repository,
            [
                capsule.ArtifactSpec(
                    "base",
                    self.candidate_sha,
                    "candidate_program.py",
                    "program",
                )
            ],
            stdlib_modules=("json",),
        )
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.repository,
                candidate_bundle,
                authority_map=self.bundle.authorities,
                stdlib_modules=self.stdlib_modules,
                program_artifact_id=candidate_bundle.artifact_ids[0],
                request={},
            )

    def test_import_closure_and_stdlib_policy_are_closed(self):
        without_helper = tuple(
            spec
            for spec in self.specs
            if spec.module_name != "trustedpkg.helper"
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "closed bundle"):
            capsule.build_artifact_bundle(
                self.repository,
                without_helper,
                stdlib_modules=self.stdlib_modules,
            )
        with self.assertRaisesRegex(capsule.CapsuleError, "closed bundle"):
            capsule.build_artifact_bundle(
                self.repository,
                self.specs,
                stdlib_modules=(),
            )
        with self.assertRaisesRegex(capsule.CapsuleError, "unsafe"):
            capsule.build_artifact_bundle(
                self.repository,
                self.specs,
                stdlib_modules=("ctypes",),
            )

    def test_execution_reloads_every_blob_from_git_before_sealing(self):
        program_record = next(
            record
            for record in capsule.validate_artifact_bundle(self.bundle.payload)[
                "artifacts"
            ]
            if record["path"] == "trusted_program.py"
        )
        object_path = (
            self.repository
            / ".git"
            / "objects"
            / program_record["blob_oid"][:2]
            / program_record["blob_oid"][2:]
        )
        self.assertTrue(object_path.is_file())
        content = object_path.read_bytes()
        object_path.unlink()
        try:
            with self.assertRaises(capsule.CapsuleError):
                self.execute(mode="authority")
        finally:
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_bytes(content)

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
            rebuilt = capsule.build_artifact_bundle(
                self.repository,
                self.specs,
                stdlib_modules=self.stdlib_modules,
            )
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
        self.assertIn(b"sealed import policy", import_error.exception.stderr)
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
        ready_read_fd = -1
        ready_write_fd = -1
        try:
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
            launcher_fd, _launcher_digest = capsule._compile_sandbox_launcher()
            fds.append(launcher_fd)
            ready_read_fd, ready_write_fd = os.pipe2(os.O_CLOEXEC)
            fds.extend((ready_read_fd, ready_write_fd))
            pass_fds = [
                program_fd,
                request_fd,
                bundle_fd,
                launcher_fd,
                ready_write_fd,
            ]
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
            bootstrap_arguments = capsule._bootstrap_arguments(
                program_fd=program_fd,
                program_digest=program_digest,
                request_fd=request_fd,
                request_digest=request_digest,
                bundle_fd=bundle_fd,
                bundle_digest=bundle_digest,
                nonce=nonce,
                program_artifact_id=self.program_ids["trusted_program.py"],
            )
            command = capsule._sandbox_command(
                launcher_fd=launcher_fd,
                program_fd=program_fd,
                request_fd=request_fd,
                bundle_fd=bundle_fd,
                ready_fd=ready_write_fd,
                bootstrap_arguments=bootstrap_arguments,
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

    def test_c_launcher_compiles_directly_to_a_sealed_memfd(self):
        fd, digest = capsule._compile_sandbox_launcher()
        try:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(
                fcntl.fcntl(fd, fcntl.F_GET_SEALS) & capsule.REQUIRED_SEALS,
                capsule.REQUIRED_SEALS,
            )
            os.lseek(fd, 0, os.SEEK_SET)
            self.assertEqual(os.read(fd, 4), b"\x7fELF")
        finally:
            os.close(fd)

    def test_timeout_crash_oversized_malformed_and_partial_leave_no_receipt(self):
        for mode in (
            "timeout",
            "crash",
            "oversized",
            "malformed",
            "partial",
            "stderr-success",
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(
                    (capsule.CapsuleExecutionError, capsule.CapsuleError)
                ):
                    self.execute(mode=mode)

    def test_timeout_and_closed_stdio_leave_no_process_or_descriptor(self):
        before = len(os.listdir("/proc/self/fd"))
        for mode in ("timeout", "closed-stdio-timeout"):
            with self.subTest(mode=mode):
                with self.assertRaises(capsule.CapsuleExecutionError):
                    self.execute(mode=mode)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_kernel_sandbox_denies_process_network_native_and_escape_paths(self):
        for mode in (
            "subprocess",
            "socket",
            "fork",
            "exec",
            "setsid-double-fork",
            "double-fork",
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(capsule.CapsuleExecutionError) as caught:
                    self.execute(mode=mode)
                self.assertIn(b"Operation not permitted", caught.exception.stderr)
                if mode == "subprocess":
                    self.assertIn(b"/bin/cat", caught.exception.stderr)
        for mode in (
            "ctypes",
            "mmap",
            "native-loader",
            "import-hook",
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(capsule.CapsuleExecutionError) as caught:
                    if mode == "native-loader":
                        spec = __import__("importlib.util").util.find_spec("_ctypes")
                        self.assertIsNotNone(spec)
                        self.execute(mode=mode, path=spec.origin)
                    else:
                        self.execute(mode=mode)
                self.assertTrue(
                    b"sealed import policy" in caught.exception.stderr
                    or b"cannot open shared object file" in caught.exception.stderr
                    or b"file too short" in caught.exception.stderr
                )

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
                self.repository,
                self.bundle,
                authority_map=self.bundle.authorities,
                stdlib_modules=self.stdlib_modules,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "timeout"},
                _after_spawn=interrupt,
            )
        self.assertIsNotNone(child_pid)
        self.assertFalse(pid_exists(child_pid))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_receipt_is_withheld_until_containment_is_empty(self):
        with mock.patch.object(
            capsule,
            "_verify_containment_empty",
            side_effect=capsule.CapsuleExecutionError(
                "sandbox PID namespace did not terminate"
            ),
        ):
            with self.assertRaisesRegex(
                capsule.CapsuleExecutionError,
                "PID namespace did not terminate",
            ):
                self.execute(mode="positive")

    def test_bounds_strict_json_and_fail_closed_platform(self):
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.repository,
                self.bundle.payload,
                authority_map=self.bundle.authorities,
                stdlib_modules=self.stdlib_modules,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={},
            )
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.repository,
                self.bundle,
                authority_map=self.bundle.authorities,
                stdlib_modules=self.stdlib_modules,
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
        with mock.patch.object(capsule, "BWRAP", "/missing/bwrap"):
            with self.assertRaisesRegex(capsule.CapsuleError, "bwrap"):
                capsule._require_platform()

    def test_environment_credentials_proc_and_writes_are_absent(self):
        with mock.patch.dict(
            os.environ,
            {"GH_TOKEN": "secret-token", "PYTHONPATH": "/candidate/control"},
            clear=False,
        ):
            result = self.execute(mode="filesystem")
        self.assertEqual(
            result.output,
            {
                "environment": {},
                "etc_hostname": False,
                "proc": False,
                "write_blocked": True,
            },
        )

    def test_program_runs_after_authority_descriptors_are_closed(self):
        result = self.execute(mode="fds")
        self.assertEqual(result.output, {"fds": []})

    def test_abrupt_parent_sigkill_terminates_pid_namespace(self):
        read_fd, write_fd = os.pipe()
        helper_pid = os.fork()
        if helper_pid == 0:
            os.close(read_fd)
            try:
                capsule.execute_capsule(
                    self.repository,
                    self.bundle,
                    authority_map=self.bundle.authorities,
                    stdlib_modules=self.stdlib_modules,
                    program_artifact_id=self.program_ids["trusted_program.py"],
                    request={"mode": "timeout"},
                    timeout=60,
                    _after_spawn=lambda pid: os.write(
                        write_fd, f"{pid}\n".encode("ascii")
                    ),
                )
            finally:
                os._exit(99)
        os.close(write_fd)
        try:
            raw = os.read(read_fd, 64)
            self.assertTrue(raw.endswith(b"\n"))
            supervisor_pid = int(raw)
            self.assertTrue(pid_exists(supervisor_pid))
            os.kill(helper_pid, __import__("signal").SIGKILL)
            os.waitpid(helper_pid, 0)
            for _ in range(200):
                if not pid_exists(supervisor_pid):
                    break
                status_path = Path(f"/proc/{supervisor_pid}/status")
                try:
                    state = next(
                        line
                        for line in status_path.read_text(encoding="utf-8").splitlines()
                        if line.startswith("State:")
                    )
                except (FileNotFoundError, ProcessLookupError, StopIteration):
                    break
                if "\tZ " in state:
                    break
                time.sleep(0.05)
            else:
                self.fail("sandbox supervisor remained live after abrupt parent death")
        finally:
            os.close(read_fd)
            if pid_exists(helper_pid):
                os.kill(helper_pid, __import__("signal").SIGKILL)
                os.waitpid(helper_pid, 0)

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
            repository,
            bundle,
            authority_map=bundle.authorities,
            stdlib_modules=(),
            program_artifact_id=record["artifact_id"],
            request={},
        )
        self.assertEqual(result.output, {"object_format": "sha256"})


if __name__ == "__main__":
    unittest.main()
