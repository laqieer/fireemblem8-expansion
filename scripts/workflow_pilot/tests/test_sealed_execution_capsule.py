from __future__ import annotations

import base64
import ast
import copy
import fcntl
import hashlib
import inspect
import json
import os
import pickle
import shutil
import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone
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
elif mode == "namespace":
    print(json.dumps({
        "hostname": os.uname().nodename,
        "pid": os.getpid(),
    }, sort_keys=True))
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
elif mode == "raw-open":
    os.open("/etc/hostname", os.O_RDONLY)
elif mode == "raw-ioctl":
    __import__("fcntl").ioctl(1, 0)
elif mode == "raw-syscall":
    ctypes = __import__("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(request["number"], 0, 0, 0, 0, 0, 0)
    if result == -1:
        raise OSError(ctypes.get_errno(), "raw syscall denied")
    raise RuntimeError("raw syscall unexpectedly succeeded")
elif mode == "raw-mmap-exec":
    ctypes = __import__("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    address = libc.mmap(None, 4096, 7, 0x22, -1, 0)
    if address == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_errno(), "executable mmap denied")
    raise RuntimeError("executable mmap unexpectedly succeeded")
elif mode == "raw-mprotect-exec":
    ctypes = __import__("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    address = libc.mmap(None, 4096, 3, 0x22, -1, 0)
    if address == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_errno(), "writable mmap failed")
    if libc.mprotect(ctypes.c_void_p(address), 4096, 5) == -1:
        raise OSError(ctypes.get_errno(), "executable mprotect denied")
    raise RuntimeError("executable mprotect unexpectedly succeeded")
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
elif mode == "thread":
    __import__("_thread").start_new_thread(lambda: None, ())
elif mode == "module-scrub":
    print(json.dumps({
        "imp": "_imp" in sys.modules,
        "posix": "posix" in sys.modules,
    }, sort_keys=True))
elif mode == "filesystem":
    write_blocked = False
    etc_blocked = False
    proc_blocked = False
    try:
        os.mkdir("/capsule-write")
    except OSError:
        write_blocked = True
    try:
        open("/etc/hostname", "rb").read()
    except OSError:
        etc_blocked = True
    try:
        os.listdir("/proc")
    except OSError:
        proc_blocked = True
    print(json.dumps({
        "environment": dict(os.environ),
        "etc_blocked": etc_blocked,
        "proc_blocked": proc_blocked,
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


def authenticated_contract(
    repository: Path,
    *,
    repository_name: str,
    context: str,
    specs: tuple[capsule._ArtifactSpec, ...],
    relationships: tuple[tuple[str, str], ...],
    stdlib_modules: tuple[str, ...],
    private_key: Path,
    signer: str,
    nonce: str,
) -> bytes:
    grouped: dict[str, list[capsule._ArtifactSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.authority, []).append(spec)
    authorities = []
    object_format = run_git(
        repository, "rev-parse", "--show-object-format"
    ).decode().strip()
    for authority in sorted(grouped):
        selected = grouped[authority]
        revision = selected[0].revision
        tree = run_git(repository, "rev-parse", f"{revision}^{{tree}}").decode().strip()
        artifacts = []
        for spec in sorted(selected, key=lambda item: item.path):
            raw = run_git(
                repository,
                "ls-tree",
                revision,
                "--",
                spec.path,
            ).decode().strip()
            metadata, path = raw.split("\t", 1)
            mode, kind, blob_oid = metadata.split()
            if kind != "blob" or path != spec.path:
                raise AssertionError("test authority rule is not a blob")
            artifacts.append(
                {
                    "path": spec.path,
                    "role": spec.role,
                    "module_name": spec.module_name,
                    "mode": mode,
                    "blob_oid": blob_oid,
                }
            )
        authorities.append(
            {
                "authority": authority,
                "revision": revision,
                "tree": tree,
                "artifacts": artifacts,
            }
        )
    payload = capsule.normalized_json(
        {
            "schema_version": 1,
            "repository": repository_name,
            "context": context,
            "signer": signer,
            "signature_namespace": capsule.AUTHORITY_SIGNATURE_NAMESPACE,
            "nonce": nonce,
            "not_before": (
                datetime.now(timezone.utc) - timedelta(seconds=30)
            ).isoformat().replace("+00:00", "Z"),
            "not_after": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat().replace("+00:00", "Z"),
            "program_role": "python-program",
            "request_role": "canonical-json-request",
            "object_format": object_format,
            "authorities": authorities,
            "relationships": [
                {"ancestor": ancestor, "descendant": descendant}
                for ancestor, descendant in relationships
            ],
            "stdlib_modules": list(stdlib_modules),
        }
    )
    completed = subprocess.run(
        (
            capsule.SSH_KEYGEN,
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            capsule.AUTHORITY_SIGNATURE_NAMESPACE,
        ),
        input=payload,
        env={"HOME": "/", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    return capsule.normalized_json(
        {
            "schema_version": 1,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "signature_b64": base64.b64encode(completed.stdout).decode("ascii"),
        }
    )


def mutate_authenticated_contract(
    envelope_bytes: bytes,
    private_key: Path,
    mutate,
) -> bytes:
    envelope = json.loads(envelope_bytes)
    payload = json.loads(base64.b64decode(envelope["payload_b64"]))
    mutate(payload)
    payload_bytes = capsule.normalized_json(payload)
    completed = subprocess.run(
        (
            capsule.SSH_KEYGEN,
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            capsule.AUTHORITY_SIGNATURE_NAMESPACE,
        ),
        input=payload_bytes,
        env={"HOME": "/", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    return capsule.normalized_json(
        {
            "schema_version": 1,
            "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
            "signature_b64": base64.b64encode(completed.stdout).decode("ascii"),
        }
    )


def sealed_raw_descriptor(name: str, payload: bytes) -> int:
    fd = os.memfd_create(
        name,
        flags=os.MFD_ALLOW_SEALING | getattr(os, "MFD_CLOEXEC", 0),
    )
    offset = 0
    while offset < len(payload):
        offset += os.write(fd, payload[offset:])
    os.lseek(fd, 0, os.SEEK_SET)
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, capsule.REQUIRED_SEALS)
    return fd


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
        run_git(
            cls.repository,
            "remote",
            "add",
            "origin",
            "https://github.com/example/capsule.git",
        )
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
            capsule._ArtifactSpec(
                "base", cls.base_sha, "trusted_program.py", "program"
            ),
            capsule._ArtifactSpec(
                "base", cls.base_sha, "trusted_checker.py", "program"
            ),
            capsule._ArtifactSpec(
                "base",
                cls.base_sha,
                "trustedpkg/__init__.py",
                "package",
                "trustedpkg",
            ),
            capsule._ArtifactSpec(
                "base",
                cls.base_sha,
                "trustedpkg/helper.py",
                "module",
                "trustedpkg.helper",
            ),
            capsule._ArtifactSpec(
                "base", cls.base_sha, "inputs/base.json", "data"
            ),
            capsule._ArtifactSpec(
                "origin", cls.origin_sha, "inputs/origin.json", "data"
            ),
            capsule._ArtifactSpec(
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
        cls.signer = "workflow-capsule-authority"
        cls.nonce = "issue-204-authority-nonce-0001"
        cls.context = "issue-204-test-authority"
        cls.private_key = TEST_ROOT / "authority-signing-key"
        subprocess.run(
            (
                capsule.SSH_KEYGEN,
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(cls.private_key),
            ),
            check=True,
            capture_output=True,
        )
        public_fields = cls.private_key.with_suffix(".pub").read_text(
            encoding="ascii"
        ).split()
        verifier_bytes = (
            f"{cls.signer} {public_fields[0]} {public_fields[1]}\n"
        ).encode("ascii")
        cls.verifier_sha256 = hashlib.sha256(verifier_bytes).hexdigest()
        cls.verifier_fd = sealed_raw_descriptor(
            "workflow-capsule-verifier",
            verifier_bytes,
        )
        cls.attacker_private_key = TEST_ROOT / "attacker-signing-key"
        subprocess.run(
            (
                capsule.SSH_KEYGEN,
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(cls.attacker_private_key),
            ),
            check=True,
            capture_output=True,
        )
        attacker_fields = cls.attacker_private_key.with_suffix(".pub").read_text(
            encoding="ascii"
        ).split()
        cls.attacker_verifier_fd = sealed_raw_descriptor(
            "workflow-capsule-attacker-verifier",
            f"{cls.signer} {attacker_fields[0]} {attacker_fields[1]}\n".encode(
                "ascii"
            ),
        )
        cls.contract = authenticated_contract(
            cls.repository,
            repository_name="example/capsule",
            context=cls.context,
            specs=cls.specs,
            relationships=(("base", "origin"), ("origin", "head")),
            stdlib_modules=cls.stdlib_modules,
            private_key=cls.private_key,
            signer=cls.signer,
            nonce=cls.nonce,
        )
        cls.requests = tuple(
            capsule.ArtifactRequest(
                authority=spec.authority,
                path=spec.path,
                role=spec.role,
                module_name=spec.module_name,
            )
            for spec in cls.specs
        )
        cls.bundle = capsule.build_artifact_bundle(
            cls.repository,
            cls.contract,
            cls.requests,
            verifier_fd=cls.verifier_fd,
            expected_signer=cls.signer,
            expected_verifier_sha256=cls.verifier_sha256,
            expected_context=cls.context,
            expected_nonce=cls.nonce,
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
        os.close(cls.verifier_fd)
        os.close(cls.attacker_verifier_fd)
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def execute(self, mode: str = "positive", context: str = "outer", **kwargs):
        return capsule.execute_capsule(
            self.repository,
            self.contract,
            self.bundle,
            verifier_fd=self.verifier_fd,
            expected_signer=self.signer,
            expected_verifier_sha256=self.verifier_sha256,
            expected_context=self.context,
            expected_nonce=self.nonce,
            program_artifact_id=self.program_ids["trusted_program.py"],
            request={"mode": mode, "context": context, **kwargs},
            timeout=0.8 if "timeout" in mode else 10,
        )

    def build(self, requests):
        return self.build_with_contract(self.contract, requests)

    def build_with_contract(
        self,
        contract,
        requests,
        *,
        verifier_fd=None,
        verifier_sha256=None,
        signer=None,
        context=None,
        nonce=None,
    ):
        return capsule.build_artifact_bundle(
            self.repository,
            contract,
            requests,
            verifier_fd=self.verifier_fd if verifier_fd is None else verifier_fd,
            expected_signer=self.signer if signer is None else signer,
            expected_verifier_sha256=(
                self.verifier_sha256
                if verifier_sha256 is None
                else verifier_sha256
            ),
            expected_context=self.context if context is None else context,
            expected_nonce=self.nonce if nonce is None else nonce,
        )

    def execute_bundle(self, bundle, *, program_artifact_id, request, **kwargs):
        return capsule.execute_capsule(
            self.repository,
            self.contract,
            bundle,
            verifier_fd=self.verifier_fd,
            expected_signer=self.signer,
            expected_verifier_sha256=self.verifier_sha256,
            expected_context=self.context,
            expected_nonce=self.nonce,
            program_artifact_id=program_artifact_id,
            request=request,
            **kwargs,
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
        rebuilt = self.build(self.requests)
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
        contract_payload = base64.b64decode(
            json.loads(self.contract)["payload_b64"]
        )
        self.assertEqual(
            first.receipt["authority_contract_sha256"],
            hashlib.sha256(contract_payload).hexdigest(),
        )
        self.assertEqual(first.receipt["authority_signer"], self.signer)
        self.assertEqual(first.receipt["authority_nonce"], self.nonce)
        self.assertEqual(
            first.receipt["authority_verifier_sha256"],
            hashlib.sha256(
                os.pread(
                    self.verifier_fd,
                    os.fstat(self.verifier_fd).st_size,
                    0,
                )
            ).hexdigest(),
        )
        self.assertEqual(
            first.receipt_sha256,
            hashlib.sha256(capsule.normalized_json(first.receipt)).hexdigest(),
        )
        self.assertEqual(
            first.receipt["sandbox_profile"],
            "linux-x86_64-hosted-landlock-seccomp-pdeath-v3",
        )
        self.assertRegex(first.receipt["sandbox_launcher_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            first.receipt["sandbox_launcher_source_sha256"],
            hashlib.sha256(capsule._SANDBOX_LAUNCHER_SOURCE.encode("ascii")).hexdigest(),
        )
        self.assertEqual(
            first.receipt["sandbox_compiler_argv_sha256"],
            hashlib.sha256(
                capsule.normalized_json(
                    [capsule.CC, *capsule.SANDBOX_COMPILER_FLAGS]
                )
            ).hexdigest(),
        )

    def test_public_build_and_execute_each_verify_external_signature(self):
        with mock.patch.object(
            capsule,
            "_verify_ssh_signature",
            wraps=capsule._verify_ssh_signature,
        ) as verify:
            bundle = self.build(self.requests)
            self.execute_bundle(
                bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "authority"},
            )
        self.assertEqual(verify.call_count, 2)

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
            result = self.execute_bundle(
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
                    result = self.execute_bundle(
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
            self.bundle.stdlib_modules,
        )

    def test_closed_bundle_rejects_membership_and_metadata_mutations(self):
        cases = {
            "schema-bool": lambda data: data.update(schema_version=True),
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
                    self.execute_bundle(
                        self.mutated_bundle(mutate),
                        program_artifact_id=self.program_ids["trusted_program.py"],
                        request={"mode": "positive", "context": name},
                    )

    def test_public_import_and_signed_authority_attacks_reject(self):
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
            self.stdlib_modules,
        )
        with self.assertRaises(capsule.CapsuleError):
            self.execute_bundle(
                forged_bundle,
                program_artifact_id=target["artifact_id"],
                request={"mode": "authority"},
            )

        for name in (
            "VerifiedAuthorityPolicy",
            "load_verified_authority_policy",
            "_mint_verified_authority",
            "_verified_authority_state",
            "_authority_capability_accessors",
        ):
            self.assertFalse(hasattr(capsule, name), name)
        for entry in (capsule.build_artifact_bundle, capsule.execute_capsule):
            parameters = inspect.signature(entry).parameters
            self.assertNotIn("capability", parameters)
            self.assertNotIn("private_key", parameters)
            self.assertIn("signed_contract", parameters)
            self.assertIn("verifier_fd", parameters)
        manual_state = capsule._SignedAuthorityState(
            repository_root=self.repository,
            repository="example/capsule",
            context=self.context,
            authorities=policies,
            relationships=(),
            stdlib_modules=self.stdlib_modules,
            contract_sha256="0" * 64,
            verifier_sha256="0" * 64,
            signer=self.signer,
            nonce=self.nonce,
        )
        for fake in (
            manual_state,
            copy.copy(manual_state),
            copy.deepcopy(manual_state),
            pickle.loads(pickle.dumps(manual_state)),
            {},
            policies,
        ):
            with self.subTest(fake=type(fake).__name__):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.execute_capsule(
                        self.repository,
                        fake,
                        self.bundle,
                        verifier_fd=self.verifier_fd,
                        expected_signer=self.signer,
                        expected_verifier_sha256=self.verifier_sha256,
                        expected_context=self.context,
                        expected_nonce=self.nonce,
                        program_artifact_id=self.program_ids["trusted_program.py"],
                        request={"mode": "authority"},
                    )

        candidate_contract = authenticated_contract(
            self.repository,
            repository_name="example/capsule",
            context=self.context,
            specs=(
                capsule._ArtifactSpec(
                    "base",
                    self.candidate_sha,
                    "candidate_program.py",
                    "program",
                ),
            ),
            relationships=(),
            stdlib_modules=("json",),
            private_key=self.attacker_private_key,
            signer=self.signer,
            nonce=self.nonce,
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "digest|signature"):
            self.build_with_contract(
                candidate_contract,
                [capsule.ArtifactRequest("base", "candidate_program.py", "program")],
            )
        with self.assertRaisesRegex(capsule.CapsuleError, "digest|signature"):
            self.build_with_contract(
                self.contract,
                self.requests,
                verifier_fd=self.attacker_verifier_fd,
            )
        with self.assertRaisesRegex(capsule.CapsuleError, "digest"):
            self.build_with_contract(
                self.contract,
                self.requests,
                verifier_sha256="0" * 64,
            )
        with self.assertRaises(capsule.CapsuleError):
            self.build_with_contract(
                self.contract,
                self.requests,
                signer="wrong-authority-signer",
            )
        tampered = json.loads(self.contract)
        signature = bytearray(base64.b64decode(tampered["signature_b64"]))
        signature[-2] ^= 1
        tampered["signature_b64"] = base64.b64encode(signature).decode("ascii")
        with self.assertRaisesRegex(capsule.CapsuleError, "signature verification"):
            self.build_with_contract(
                capsule.normalized_json(tampered),
                self.requests,
            )
        verifier_bytes = os.pread(
            self.verifier_fd,
            os.fstat(self.verifier_fd).st_size,
            0,
        )
        unsealed_verifier = os.memfd_create(
            "unsealed-verifier",
            flags=os.MFD_ALLOW_SEALING,
        )
        try:
            os.write(unsealed_verifier, verifier_bytes)
            with self.assertRaisesRegex(capsule.CapsuleError, "not fully sealed"):
                self.build_with_contract(
                    self.contract,
                    self.requests,
                    verifier_fd=unsealed_verifier,
                )
        finally:
            os.close(unsealed_verifier)
        path_verifier = os.open(
            self.private_key.with_suffix(".pub"),
            os.O_RDONLY | os.O_CLOEXEC,
        )
        try:
            with self.assertRaisesRegex(
                capsule.CapsuleError, "sealed verifier|verifier identity"
            ):
                self.build_with_contract(
                    self.contract,
                    self.requests,
                    verifier_fd=path_verifier,
                )
        finally:
            os.close(path_verifier)

    def test_mixed_revision_and_candidate_under_base_authority_reject(self):
        candidate_blob = run_git(
            self.repository,
            "rev-parse",
            f"{self.candidate_sha}:candidate_program.py",
        ).decode().strip()
        candidate_tree = run_git(
            self.repository, "rev-parse", f"{self.candidate_sha}^{{tree}}"
        ).decode().strip()

        def add_mixed_base(payload):
            payload["authorities"].insert(
                1,
                {
                    "authority": "base",
                    "revision": self.candidate_sha,
                    "tree": candidate_tree,
                    "artifacts": [
                        {
                            "path": "candidate_program.py",
                            "role": "program",
                            "module_name": None,
                            "mode": "100644",
                            "blob_oid": candidate_blob,
                        }
                    ],
                },
            )

        mixed = mutate_authenticated_contract(
            self.contract,
            self.private_key,
            add_mixed_base,
        )
        with self.assertRaises(capsule.CapsuleError):
            self.build_with_contract(
                mixed,
                self.requests,
            )

        mutations = {
            "repository": lambda payload: payload.update(
                repository="attacker/repository"
            ),
            "object-format": lambda payload: payload.update(object_format="sha256"),
            "context": lambda payload: payload.update(context="wrong-context"),
            "nonce": lambda payload: payload.update(nonce="wrong-nonce-value"),
            "expired": lambda payload: payload.update(
                not_before="2020-01-01T00:00:00Z",
                not_after="2020-01-01T00:01:00Z",
            ),
            "future": lambda payload: payload.update(
                not_before="2099-01-01T00:00:00Z",
                not_after="2099-01-01T00:01:00Z",
            ),
            "program-role": lambda payload: payload.update(program_role="forged"),
            "request-role": lambda payload: payload.update(request_role="forged"),
            "schema-bool": lambda payload: payload.update(schema_version=True),
            "revision": lambda payload: payload["authorities"][0].update(
                revision="0" * 40
            ),
            "tree": lambda payload: payload["authorities"][0].update(tree="0" * 40),
            "relationship": lambda payload: payload.update(
                relationships=[{"ancestor": "head", "descendant": "base"}]
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = mutate_authenticated_contract(
                    self.contract, self.private_key, mutate
                )
                with self.assertRaises(capsule.CapsuleError):
                    self.build_with_contract(
                        changed,
                        self.requests,
                    )

    def test_import_closure_and_stdlib_policy_are_closed(self):
        without_helper = tuple(
            request
            for request in self.requests
            if request.module_name != "trustedpkg.helper"
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "closed bundle"):
            capsule.build_artifact_bundle(
                self.repository,
                self.contract,
                without_helper,
                verifier_fd=self.verifier_fd,
                expected_signer=self.signer,
                expected_verifier_sha256=self.verifier_sha256,
                expected_context=self.context,
                expected_nonce=self.nonce,
            )
        unsafe = mutate_authenticated_contract(
            self.contract,
            self.private_key,
            lambda payload: payload.update(stdlib_modules=["ctypes"]),
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "unsafe"):
            self.build_with_contract(
                unsafe,
                self.requests,
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
        mutations = {
            "missing": lambda payload: payload["authorities"][0]["artifacts"][
                0
            ].update(path="missing.py"),
            "mode": lambda payload: payload["authorities"][0]["artifacts"][
                0
            ].update(mode="100755"),
            "blob": lambda payload: payload["authorities"][0]["artifacts"][
                0
            ].update(blob_oid="0" * 40),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                contract = mutate_authenticated_contract(
                    self.contract, self.private_key, mutate
                )
                with self.assertRaises(capsule.CapsuleError):
                    self.build_with_contract(contract, self.requests)

        (self.repository / "unsafe.py").symlink_to("trusted_program.py")
        run_git(self.repository, "add", "unsafe.py")
        run_git(self.repository, "commit", "-q", "-m", "symlink")
        symlink_sha = run_git(self.repository, "rev-parse", "HEAD").decode().strip()
        symlink_contract = authenticated_contract(
            self.repository,
            repository_name="example/capsule",
            context=self.context,
            specs=(
                capsule._ArtifactSpec("base", symlink_sha, "unsafe.py", "program"),
            ),
            relationships=(),
            stdlib_modules=(),
            private_key=self.private_key,
            signer=self.signer,
            nonce=self.nonce,
        )
        with self.assertRaises(capsule.CapsuleError):
            self.build_with_contract(
                symlink_contract,
                [capsule.ArtifactRequest("base", "unsafe.py", "program")],
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
            rebuilt = self.build(self.requests)
        self.assertEqual(rebuilt, self.bundle)

        alternates = self.repository / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(ROOT / ".git" / "objects") + "\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(capsule.CapsuleError, "alternate object"):
                self.build(self.requests)
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
        self.assertTrue(
            b"filesystem access is forbidden" in spec_error.exception.stderr
            or b"sealed import policy" in spec_error.exception.stderr
        )

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
            extra = -1
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
            pass_fds = [
                program_fd,
                request_fd,
                bundle_fd,
                launcher_fd,
                ready_write_fd,
            ]
            if extra >= 0:
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
                stdlib_modules=self.stdlib_modules,
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

    def test_hosted_supervisor_uses_no_namespaces_or_bubblewrap(self):
        namespaces = {}

        def inspect_namespaces(pid: int) -> None:
            namespaces["user"] = os.readlink(f"/proc/{pid}/ns/user")
            namespaces["mount"] = os.readlink(f"/proc/{pid}/ns/mnt")
            namespaces["network"] = os.readlink(f"/proc/{pid}/ns/net")
            namespaces["pid_children"] = os.readlink(
                f"/proc/{pid}/ns/pid_for_children"
            )

        with mock.patch.dict(os.environ, {"PATH": "/no-bubblewrap"}, clear=False):
            result = self.execute_bundle(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={"mode": "namespace"},
                _after_spawn=inspect_namespaces,
            )
        self.assertEqual(result.output["hostname"], os.uname().nodename)
        self.assertGreater(result.output["pid"], 1)
        self.assertEqual(namespaces["user"], os.readlink("/proc/self/ns/user"))
        self.assertEqual(namespaces["mount"], os.readlink("/proc/self/ns/mnt"))
        self.assertEqual(namespaces["network"], os.readlink("/proc/self/ns/net"))
        self.assertEqual(
            namespaces["pid_children"],
            os.readlink("/proc/self/ns/pid_for_children"),
        )
        self.assertFalse(hasattr(capsule, "BWRAP"))
        self.assertNotIn("CLONE_NEWUSER", capsule._SANDBOX_LAUNCHER_SOURCE)
        self.assertNotIn("unshare(", capsule._SANDBOX_LAUNCHER_SOURCE)

    def test_hosted_preflight_proves_supported_kernel_without_userns(self):
        self.assertEqual(
            capsule.hosted_security_preflight(),
            {
                "landlock": True,
                "memfd": True,
                "no_new_privs": True,
                "pidfd": True,
                "seccomp": True,
                "userns": False,
            },
        )

    def test_build_topology_declares_no_new_capsule_runtime_package(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
            encoding="utf-8"
        )
        install_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "apt-get install" in line
        ]
        self.assertTrue(any("build-essential" in line for line in install_lines))
        self.assertTrue(
            all(
                "bubblewrap" not in line and "bwrap" not in line
                and "openssh-client" not in line
                for line in install_lines
            )
        )

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
            "thread",
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

    def test_post_start_seccomp_denies_raw_kernel_escape_syscalls(self):
        unhooked_bootstrap = capsule._BOOTSTRAP_SOURCE.replace(
            "    install_guards(records, stdlib_modules)\n",
            "    pass\n",
        )
        self.assertNotEqual(unhooked_bootstrap, capsule._BOOTSTRAP_SOURCE)
        cases = {
            "raw-open": {},
            "raw-ioctl": {},
            "raw-mmap-exec": {},
            "raw-mprotect-exec": {},
            "ptrace": {"number": 101},
            "perf-event-open": {"number": 298},
            "bpf": {"number": 321},
            "keyctl": {"number": 250},
            "shmat": {"number": 30},
            "pkey-mprotect": {"number": 329},
            "getdents64": {"number": 217},
            "statx": {"number": 332},
            "openat2": {"number": 437},
        }
        with mock.patch.object(capsule, "_BOOTSTRAP_SOURCE", unhooked_bootstrap):
            for name, extra in cases.items():
                mode = name if name.startswith("raw-") else "raw-syscall"
                with self.subTest(name=name):
                    with self.assertRaises(capsule.CapsuleExecutionError) as caught:
                        self.execute(mode=mode, **extra)
                    self.assertIn(
                        b"[Errno 1]",
                        caught.exception.stderr,
                    )

    def test_bootstrap_installs_final_filter_before_reading_signed_bytes(self):
        syntax = ast.parse(capsule._BOOTSTRAP_SOURCE)
        main = next(
            node
            for node in syntax.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        calls = {}
        for node in ast.walk(main):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.setdefault(node.func.id, []).append(node.lineno)
        self.assertLess(
            min(calls["preload_stdlib"]),
            min(calls["install_final_seccomp"]),
        )
        self.assertLess(
            min(calls["install_final_seccomp"]),
            min(calls["decode_descriptor"]),
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
            self.execute_bundle(
                self.bundle,
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
                "sandbox supervisor did not terminate"
            ),
        ):
            with self.assertRaisesRegex(
                capsule.CapsuleExecutionError,
                "supervisor did not terminate",
            ):
                self.execute(mode="positive")

    def test_bounds_strict_json_and_fail_closed_platform(self):
        with self.assertRaises(capsule.CapsuleError):
            capsule.execute_capsule(
                self.repository,
                self.contract,
                self.bundle.payload,
                verifier_fd=self.verifier_fd,
                expected_signer=self.signer,
                expected_verifier_sha256=self.verifier_sha256,
                expected_context=self.context,
                expected_nonce=self.nonce,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request={},
            )
        with self.assertRaises(capsule.CapsuleError):
            self.execute_bundle(
                self.bundle,
                program_artifact_id=self.program_ids["trusted_program.py"],
                request=b'{"x":1,"x":2}',
            )
        with self.assertRaises(capsule.CapsuleError):
            self.build([self.requests[0]] * (capsule.MAX_ARTIFACTS + 1))
        with mock.patch.object(capsule.sys, "platform", "darwin"):
            with self.assertRaisesRegex(capsule.CapsuleError, "require Linux"):
                capsule._require_platform()
        with mock.patch.object(capsule, "SSH_KEYGEN", "/missing/ssh-keygen"):
            with self.assertRaisesRegex(capsule.CapsuleError, "ssh-keygen"):
                capsule._require_platform()
        self.assertFalse(hasattr(capsule, "BWRAP"))

    def test_bootstrap_modules_are_not_ambient_candidate_authority(self):
        self.assertEqual(
            self.execute(mode="module-scrub").output,
            {"imp": False, "posix": False},
        )

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
                "etc_blocked": True,
                "proc_blocked": True,
                "write_blocked": True,
            },
        )

    def test_program_runs_after_authority_descriptors_are_closed(self):
        result = self.execute(mode="fds")
        self.assertEqual(result.output, {"fds": []})

    def test_abrupt_parent_sigkill_terminates_supervisor(self):
        read_fd, write_fd = os.pipe()
        helper_pid = os.fork()
        if helper_pid == 0:
            os.close(read_fd)
            try:
                capsule.execute_capsule(
                    self.repository,
                    self.contract,
                    self.bundle,
                    verifier_fd=self.verifier_fd,
                    expected_signer=self.signer,
                    expected_verifier_sha256=self.verifier_sha256,
                    expected_context=self.context,
                    expected_nonce=self.nonce,
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
        run_git(
            repository,
            "remote",
            "add",
            "origin",
            "https://github.com/example/sha256-capsule.git",
        )
        (repository / "program.py").write_text(
            'print(\'{"object_format":"sha256"}\')\n', encoding="utf-8"
        )
        run_git(repository, "add", "program.py")
        run_git(repository, "commit", "-q", "-m", "sha256")
        revision = run_git(repository, "rev-parse", "HEAD").decode().strip()
        spec = capsule._ArtifactSpec("base", revision, "program.py", "program")
        contract = authenticated_contract(
            repository,
            repository_name="example/sha256-capsule",
            context="sha256-capsule",
            specs=(spec,),
            relationships=(),
            stdlib_modules=(),
            private_key=self.private_key,
            signer=self.signer,
            nonce=self.nonce,
        )
        bundle = capsule.build_artifact_bundle(
            repository,
            contract,
            [capsule.ArtifactRequest("base", "program.py", "program")],
            verifier_fd=self.verifier_fd,
            expected_signer=self.signer,
            expected_verifier_sha256=self.verifier_sha256,
            expected_context="sha256-capsule",
            expected_nonce=self.nonce,
        )
        parsed = capsule.validate_artifact_bundle(bundle.payload)
        record = parsed["artifacts"][0]
        self.assertEqual(record["object_format"], "sha256")
        self.assertEqual(len(record["blob_oid"]), 64)
        result = capsule.execute_capsule(
            repository,
            contract,
            bundle,
            verifier_fd=self.verifier_fd,
            expected_signer=self.signer,
            expected_verifier_sha256=self.verifier_sha256,
            expected_context="sha256-capsule",
            expected_nonce=self.nonce,
            program_artifact_id=record["artifact_id"],
            request={},
        )
        self.assertEqual(result.output, {"object_format": "sha256"})


if __name__ == "__main__":
    unittest.main()
