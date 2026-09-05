"""TC-WORKFLOW-SEALED-ASSERTION-CAPSULE-001: real descriptor/process controls."""

from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import json
import mmap
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import event_classifier, sealed_capsule as capsule


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "build" / "test-artifacts"
CHECKER = """from checks import helper
def capsule_main(request, context):
    nested = context.invoke('assertion', request)
    return {'checker': helper.value(), 'assertion': nested}
"""
ASSERTION = """import json
from checks import helper
def capsule_main(request, context):
    values = {slot: json.loads(context.read(slot, 'inputs/state.json'))['value']
              for slot in ('base', 'origin', 'head')}
    return {'module': helper.value(), 'values': values, 'request': request,
            'status': 'pass' if values['origin'] == 'broken' and values['head'] == 'fixed' else 'fail'}
"""


@unittest.skipUnless(sys.platform == "linux", "Linux sealed descriptor contract")
class SealedCapsuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="sealed-capsule-", dir=ARTIFACTS)
        cls.root = Path(cls.temporary.name)
        cls.runtime = (ROOT / capsule.RUNTIME_PATH).read_bytes()
        cls.write(capsule.RUNTIME_PATH, cls.runtime)
        cls.write("scripts/workflow_pilot/__init__.py", b"")
        cls.write("scripts/workflow_pilot/event_classifier.py",
                  (ROOT / "scripts/workflow_pilot/event_classifier.py").read_bytes())
        cls.write("scripts/workflow_pilot/isolated_launcher.py",
                  (ROOT / "scripts/workflow_pilot/isolated_launcher.py").read_bytes())
        cls.write("checks/checker.py", CHECKER.encode())
        cls.write("checks/assertion.py", ASSERTION.encode())
        cls.write("checks/helper.py", b"def value():\n    return 'trusted-module'\n")
        cls.write("inputs/state.json", capsule.canonical({"value": "base"}))
        cls.git("init", "-q", "-b", "master")
        cls.git("config", "user.name", "Capsule fixture")
        cls.git("config", "user.email", "capsule@example.invalid")
        cls.base = cls.commit()
        cls.write("inputs/state.json", capsule.canonical({"value": "broken"}))
        cls.origin = cls.commit()
        cls.write("inputs/state.json", capsule.canonical({"value": "fixed"}))
        cls.head = cls.commit()
        cls.spec = capsule.CapsuleSpec(
            trees={"base": cls.base, "origin": cls.origin, "head": cls.head},
            programs={"checker": "checks/checker.py", "assertion": "checks/assertion.py"},
            data={slot: ("inputs/state.json",) for slot in ("base", "origin", "head")},
        )
        cls.bundle = capsule._make_bundle(cls.root, cls.spec.record())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def write(cls, path, raw):
        target = cls.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    @classmethod
    def git(cls, *args):
        completed = subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(cls.root), *args],
            env=capsule.GIT_ENVIRONMENT, capture_output=True, check=True,
        )
        return completed.stdout.decode("ascii").strip()

    @classmethod
    def commit(cls):
        cls.git("add", ".")
        cls.git("commit", "-qm", "Ephemeral capsule test tree")
        return cls.git("rev-parse", "HEAD")

    def attack(self, source):
        self.write("checks/attack.py", source.encode("utf-8"))
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"attack": "checks/attack.py"})
        return capsule.prepare(self.root, spec)

    def descriptors(self):
        return {fd: capsule._descriptor_identity(fd) for fd in capsule._inherited_fds()}

    def test_exact_checker_assertion_module_and_three_tree_data_execute(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            result = prepared.execute("checker", {"round": 2})
        self.assertEqual(result.value["checker"], "trusted-module")
        assertion = result.value["assertion"]
        self.assertEqual(assertion["value"], {
            "module": "trusted-module", "values": {"base": "base", "origin": "broken", "head": "fixed"},
            "request": {"round": 2}, "status": "pass",
        })
        self.assertEqual(assertion["receipt"]["program"], "assertion")
        self.assertEqual(result.receipt["program"], "checker")
        self.assertEqual(assertion["receipt"]["artifact_sha256"], result.receipt["artifact_sha256"])

    def test_receipt_names_exact_executed_descriptor_bytes_and_argv(self):
        seen = {}
        real_popen = subprocess.Popen

        def capture(command, **kwargs):
            seen["argv"] = command
            runtime, program, request, artifacts, _ = kwargs["pass_fds"]
            seen["digests"] = {
                "runtime_sha256": capsule.digest(os.pread(runtime, capsule.MAX_PROGRAM_BYTES, 0)),
                "program_sha256": capsule.digest(os.pread(program, capsule.MAX_PROGRAM_BYTES, 0)),
                "request_sha256": capsule.digest(os.pread(request, capsule.MAX_REQUEST_BYTES, 0)),
                "artifact_sha256": capsule.digest(os.pread(artifacts, capsule.MAX_BUNDLE_BYTES, 0)),
            }
            return real_popen(command, **kwargs)

        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with mock.patch.object(subprocess, "Popen", side_effect=capture):
                result = prepared.execute("assertion", {"round": 3})
        receipt = result.receipt
        self.assertEqual(receipt["argv"], seen["argv"])
        self.assertEqual({key: receipt[key] for key in seen["digests"]}, seen["digests"])
        self.assertEqual(receipt["output_sha256"], capsule.digest(result.output_bytes))
        self.assertEqual(receipt["payload_sha256"], capsule.digest(capsule.canonical({"round": 3})))
        paths = {(entry["tree"], entry["path"]) for entry in receipt["loaded"]}
        for slot in ("base", "origin", "head"):
            self.assertIn((slot, "inputs/state.json"), paths)
        self.assertIn(("base", "checks/helper.py"), paths)

    def test_near_output_limit_loaded_receipts_remain_usable_without_transport_growth(self):
        self.write("checks/bounds.py", b"""def capsule_main(request, context):
    if request.get('nested'):
        return context.invoke('bounds', {'paths': request['paths']})
    for path in request['paths']:
        context.entry('base', path)
    return None
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"bounds": "checks/bounds.py"})
        bundle = capsule._Bundle(capsule._make_bundle(self.root, spec.record()))
        initial = [bundle.artifacts[("base", record["path"])] for record in bundle.modules.values()]
        binding = {
            "version": capsule.VERSION, "program": "bounds", "nonce": "0" * 64,
            **{field: "0" * 64 for field in ("program_sha256", "runtime_sha256",
               "artifact_sha256", "request_sha256", "payload_sha256")},
        }
        paths = [f"absent/{index:04d}" for index in range(1000)]
        metadata = [{"tree": "base", "path": path, "role": "data", "mode": None,
                     "blob": None, "sha256": None, "size": None} for path in paths]
        size = len(capsule.canonical({
            "binding": binding, "result": None, "loaded": initial + metadata,
            "diagnostics": {"stdout_sha256": capsule.digest(b""), "stderr_sha256": capsule.digest(b"")},
        }))
        key = b"test-only-capsule-signing-key-1234"
        collect = capsule._collect
        overhead = None
        for crossing in ("receipt", "signed-wrapper"):
            target = capsule.MAX_OUTPUT_BYTES - (128 if overhead is None else overhead + 40)
            padding, extra = divmod(target - size, len(paths))
            declared = tuple(path + "x" * (padding + (index < extra))
                             for index, path in enumerate(paths))
            self.assertLessEqual(max(len(path.encode("utf-8")) for path in declared), 1024)
            full = capsule.CapsuleSpec(trees=spec.trees, programs=spec.programs,
                                       data={"base": declared})
            observed = []

            def capture(process, timeout, limit, *args, **kwargs):
                status, stdout, stderr = collect(process, timeout, limit, *args, **kwargs)
                observed.append((len(stdout), limit))
                return status, stdout, stderr

            with self.subTest(crossing=crossing), capsule.prepare(self.root, full) as prepared:
                with mock.patch.object(capsule, "_collect", side_effect=capture):
                    result = prepared.execute("bounds", {"paths": list(declared)})
                self.assertEqual(observed, [(target, capsule.MAX_OUTPUT_BYTES + capsule.MAX_DIAGNOSTIC_BYTES)])
                overhead = len(result.receipt_bytes) - target
                self.assertIsNone(result.value)
                self.assertEqual(len(result.receipt["loaded"]), len(initial) + len(declared))
                self.assertEqual(
                    {entry["path"] for entry in result.receipt["loaded"] if entry["role"] == "data"},
                    set(declared),
                )
                if crossing == "receipt":
                    self.assertGreater(len(result.receipt_bytes), capsule.MAX_OUTPUT_BYTES)
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("bounds", {"paths": list(declared), "nested": True})
                else:
                    self.assertLessEqual(len(result.receipt_bytes), capsule.MAX_OUTPUT_BYTES)
                signed = capsule.sign_receipt(result, key)
                self.assertGreater(len(signed), capsule.MAX_OUTPUT_BYTES)
                self.assertLessEqual(len(result.receipt_bytes), capsule.MAX_RECEIPT_BYTES)
                self.assertLessEqual(len(signed), capsule.MAX_SIGNED_RECEIPT_BYTES)
                self.assertEqual(capsule.verify_receipt(signed, key, result), result.receipt)

    def test_receipt_and_signed_bounds_accept_exact_sizes_and_reject_one_byte_over(self):
        key = b"test-only-capsule-signing-key-1234"
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            result = prepared.execute("assertion", {})
            signed = capsule.sign_receipt(result, key)
            receipt_size, signed_size = len(result.receipt_bytes), len(signed)
            self.assertEqual(
                signed_size - receipt_size,
                capsule.MAX_SIGNED_RECEIPT_BYTES - capsule.MAX_RECEIPT_BYTES,
            )
            with mock.patch.multiple(capsule, MAX_RECEIPT_BYTES=receipt_size,
                                     MAX_SIGNED_RECEIPT_BYTES=signed_size):
                exact = prepared.execute("assertion", {})
                self.assertEqual(len(exact.receipt_bytes), receipt_size)
                self.assertEqual(capsule.verify_receipt(capsule.sign_receipt(exact, key), key, exact),
                                 exact.receipt)
                with mock.patch.object(capsule, "MAX_RECEIPT_BYTES", receipt_size - 1):
                    for operation in (
                        lambda: prepared.execute("assertion", {}),
                        lambda: capsule.ExecutionResult(result.receipt_bytes, result.output_bytes),
                        lambda: result.receipt,
                        lambda: capsule.sign_receipt(result, key),
                        lambda: capsule.verify_receipt(signed, key, result),
                    ):
                        with self.assertRaises(capsule.CapsuleError):
                            operation()
                with mock.patch.object(capsule, "MAX_SIGNED_RECEIPT_BYTES", signed_size - 1):
                    with self.assertRaises(capsule.CapsuleError):
                        capsule.sign_receipt(result, key)
                    with self.assertRaises(capsule.CapsuleError):
                        capsule.verify_receipt(signed, key, result)
        with self.assertRaises(capsule.CapsuleError):
            capsule.ExecutionResult(result.receipt_bytes, capsule.canonical("x" * capsule.MAX_OUTPUT_BYTES))

    def test_swap_restore_every_former_path_cannot_change_sealed_execution(self):
        paths = ("checks/checker.py", "checks/assertion.py", "checks/helper.py",
                 "inputs/state.json", capsule.RUNTIME_PATH)
        saved = {path: (self.root / path).read_bytes() for path in paths}
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            try:
                for path in paths:
                    self.write(path, b"raise RuntimeError('pathname substitution executed')\n")
                self.write("checks/__init__.py", b"raise RuntimeError('package hijack')\n")
                result = prepared.execute("checker", {"input": "original"})
            finally:
                for path, raw in saved.items():
                    self.write(path, raw)
                (self.root / "checks/__init__.py").unlink()
        self.assertEqual(result.value["assertion"]["value"]["status"], "pass")
        self.assertEqual(saved, {path: (self.root / path).read_bytes() for path in paths})

    def test_checkout_directory_move_and_git_disappearance_do_not_change_execution(self):
        moved = self.root.with_name(self.root.name + "-moved")
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            before = prepared.execute("assertion", {"path": "not-authority"})
            self.root.rename(moved)
            try:
                after = prepared.execute("assertion", {"path": "not-authority"})
            finally:
                moved.rename(self.root)
        self.assertEqual(before.value, after.value)
        for field in ("program_sha256", "runtime_sha256", "artifact_sha256", "payload_sha256"):
            self.assertEqual(before.receipt[field], after.receipt[field])

    def test_request_object_and_old_request_path_swap_after_sealing_are_inert(self):
        request = {"authority": "original"}
        old_path = self.root / "former-request.json"
        old_path.write_bytes(capsule.canonical(request))
        real_popen = subprocess.Popen

        def swap(command, **kwargs):
            request["authority"] = "forged"
            old_path.write_bytes(capsule.canonical(request))
            return real_popen(command, **kwargs)

        try:
            with capsule.Capsule(self.bundle, self.spec) as prepared:
                with mock.patch.object(subprocess, "Popen", side_effect=swap):
                    result = prepared.execute("assertion", request)
            self.assertEqual(result.value["request"], {"authority": "original"})
        finally:
            old_path.unlink()

    def test_pre_fix_validate_then_reopen_control_signs_forged_restored_program(self):
        # The abandoned #189 launch protocol: validate source, run its pathname
        # with --stdin, then inspect restored bytes before signing child output.
        path = self.root / "former-assertion.py"
        original = (b"import json,sys\nx=json.load(sys.stdin)\n"
                    b"status = 'pass' if x['head_state'] == 'fixed' else 'fail'\n"
                    b"print(json.dumps({'status':status,'binding':x}))\n")
        forged = original.replace(b"x['head_state'] == 'fixed'", b"True")
        request = {"origin": self.origin, "head": self.head, "head_state": "broken",
                   "program_sha256": capsule.digest(original)}
        path.write_bytes(original)
        checked = capsule.digest(path.read_bytes())
        try:
            for state, expected in (("broken", "fail"), ("fixed", "pass")):
                baseline = subprocess.run(
                    [capsule.PYTHON, "-I", path.name, "--stdin"], cwd=self.root,
                    input=capsule.canonical({**request, "head_state": state}),
                    capture_output=True, check=True,
                )
                self.assertEqual(json.loads(baseline.stdout)["status"], expected)
            path.write_bytes(forged)
            try:
                completed = subprocess.run(
                    [capsule.PYTHON, "-I", path.name, "--stdin"], cwd=self.root,
                    input=capsule.canonical(request), capture_output=True, check=True,
                )
            finally:
                path.write_bytes(original)
            self.assertEqual(capsule.digest(path.read_bytes()), checked)
            result = json.loads(completed.stdout)
            self.assertEqual(result, {"status": "pass", "binding": request})
            key = b"test-only-pre-fix-negative-key!!!"
            signature = hmac.new(key, completed.stdout, hashlib.sha256).digest()
            self.assertTrue(hmac.compare_digest(signature, hmac.new(key, completed.stdout, hashlib.sha256).digest()))
        finally:
            path.unlink()
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with capsule.SealedBytes(forged, "forged-program", capsule.MAX_PROGRAM_BYTES) as substituted:
                with mock.patch.object(capsule._Bundle, "program", return_value=substituted.read()):
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("assertion", request)

    def test_real_production_classifier_uses_unchanged_semantic_predicate(self):
        cases = json.loads((ROOT / "scripts/workflow_pilot/tests/fixtures/event_classification.json").read_bytes())
        spec = capsule.CapsuleSpec(trees={"base": self.base},
                                   programs={"classifier": "scripts/workflow_pilot/event_classifier.py"})
        with capsule.prepare(self.root, spec) as prepared:
            for case in cases["cases"]:
                with self.subTest(case=case["id"]):
                    request = {"event_name": case["event_name"], "payload": case["payload"], **case["runner"]}
                    actual = prepared.execute("classifier", request)
                    expected = json.loads(event_classifier.classify_event(**request).canonical_json())
                    self.assertEqual(actual.value, expected)

    def test_real_isolated_launcher_ignores_swapped_runtime_and_classifier_paths(self):
        from scripts.workflow_pilot.tests.test_event_classifier import _launcher_command

        case = json.loads((ROOT / "scripts/workflow_pilot/tests/fixtures/event_classification.json").read_bytes())["cases"][0]
        event_path, output_path = self.root / "event.json", self.root / "event.out"
        event_path.write_bytes(capsule.canonical(case["payload"]))
        paths = (capsule.RUNTIME_PATH, "scripts/workflow_pilot/event_classifier.py")
        saved = {path: (self.root / path).read_bytes() for path in paths}
        command = _launcher_command(case, event_path, output_path)
        command[2] = str(self.root / "scripts/workflow_pilot/isolated_launcher.py")
        try:
            for path in paths:
                self.write(path, b"raise RuntimeError('substituted source executed')\n")
            completed = subprocess.run(
                command, cwd=self.root, capture_output=True,
                env={**capsule.ENVIRONMENT, "PYTHONPATH": str(self.root), "GIT_DIR": "/invalid"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            expected = event_classifier.classify_event(
                case["event_name"], case["payload"], **case["runner"])
            self.assertEqual(completed.stdout.decode(), expected.canonical_json())
            self.assertIn("classification=" + expected.classification, output_path.read_text())
        finally:
            for path, raw in saved.items():
                self.write(path, raw)
            event_path.unlink()
            output_path.unlink(missing_ok=True)

    def test_event_snapshot_cannot_follow_a_path_swap_after_descriptor_open(self):
        path, backup = self.root / "event-snapshot.json", self.root / "event-snapshot.saved"
        original, forged = {"state": "trusted"}, {"state": "forged"}
        path.write_bytes(capsule.canonical(original))
        real_read = os.read
        swapped = False

        def read(fd, count):
            nonlocal swapped
            if not swapped:
                swapped = True
                path.rename(backup)
                path.write_bytes(capsule.canonical(forged))
            return real_read(fd, count)

        try:
            with mock.patch.object(os, "read", side_effect=read):
                try:
                    result = event_classifier.load_event(path)
                except event_classifier.EventClassificationError as error:
                    self.assertIn("changed while being read", str(error))
                else:
                    self.assertEqual(result, original)
            self.assertTrue(swapped)
        finally:
            path.unlink()
            backup.unlink(missing_ok=True)

    def test_immutable_seals_reject_writes_resize_mapping_and_seal_changes(self):
        with capsule.SealedBytes(b"immutable", "test", 100) as owned:
            self.assertEqual(capsule.fcntl.fcntl(owned.fd, capsule.fcntl.F_GET_SEALS) & capsule.SEALS,
                             capsule.SEALS)
            for operation in (
                lambda: os.write(owned.fd, b"forged"),
                lambda: os.ftruncate(owned.fd, 0),
                lambda: os.ftruncate(owned.fd, 1024),
                lambda: mmap.mmap(owned.fd, 0, access=mmap.ACCESS_WRITE),
                lambda: capsule.fcntl.fcntl(owned.fd, capsule.fcntl.F_ADD_SEALS, capsule.SEALS),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(OSError):
                        operation()
            duplicate = os.dup(owned.fd)
            try:
                with self.assertRaises(OSError):
                    os.pwrite(duplicate, b"x", 0)
            finally:
                os.close(duplicate)
            self.assertEqual(owned.read(), b"immutable")

    def test_mutable_and_regular_descriptors_are_not_accepted(self):
        fd = os.memfd_create("unsealed-test", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, b"same bytes")
            with self.assertRaisesRegex(capsule.CapsuleError, "fully sealed"):
                capsule._read_descriptor(fd, 100)
        finally:
            os.close(fd)
        with (self.root / "inputs/state.json").open("rb") as file:
            with self.assertRaises(capsule.CapsuleError):
                capsule._read_descriptor(file.fileno(), 100)

    def test_reused_descriptor_rejects_without_closing_unowned_replacement(self):
        owned = capsule.SealedBytes(b"original", "test", 100)
        replacement = capsule.SealedBytes(b"replaced", "test", 100)
        number = owned.fd
        try:
            os.dup2(replacement.fd, number)
            with self.assertRaisesRegex(capsule.CapsuleError, "reused"):
                owned.read()
            owned.close()
            self.assertEqual(os.pread(number, 100, 0), b"replaced")
        finally:
            os.close(number)
            replacement.close()

    def test_replaced_runtime_and_unexpected_inherited_fd_fail_before_worker(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            old = prepared.runtime_fd.fd
            os.dup2(prepared.bundle_fd.fd, old)
            try:
                with self.assertRaises(capsule.CapsuleError):
                    prepared.execute("assertion", {})
            finally:
                prepared.runtime_fd.close()
                os.close(old)
        real_popen = subprocess.Popen
        with capsule.SealedBytes(b"unrelated", "extra", 100) as extra:
            def inherit(command, **kwargs):
                kwargs["pass_fds"] = (*kwargs["pass_fds"], extra.fd)
                return real_popen(command, **kwargs)
            with capsule.Capsule(self.bundle, self.spec) as prepared:
                with mock.patch.object(subprocess, "Popen", side_effect=inherit):
                    with self.assertRaisesRegex(capsule.CapsuleError, "unexpected inherited"):
                        prepared.execute("assertion", {})

    def test_artifact_identity_and_complete_closure_mutations_fail(self):
        original = capsule.parse(self.bundle, capsule.MAX_BUNDLE_BYTES)
        changes = {
            "missing": lambda value: value["artifacts"].pop(),
            "extra": lambda value: value["artifacts"].append({**value["artifacts"][0], "path": "extra"}),
            "duplicate": lambda value: value["artifacts"].append(value["artifacts"][0]),
            "wrong-mode": lambda value: value["artifacts"][0].update(mode="100755"),
            "wrong-blob": lambda value: value["artifacts"][0].update(blob="a" * 40),
            "wrong-digest": lambda value: value["artifacts"][0].update(sha256="a" * 64),
            "wrong-role": lambda value: value["artifacts"][0].update(role="program"),
            "wrong-tree": lambda value: value["artifacts"][0].update(tree="other"),
            "wrong-version-type": lambda value: value.update(version=True),
            "wrong-package-type": lambda value: value["modules"]["checks"].update(package=1),
            "missing-module": lambda value: value["modules"].pop("checks.helper"),
            "extra-module": lambda value: value["modules"].update(ambient={"path": "ambient.py", "package": False}),
            "missing-proof": lambda value: value["objects"].pop(),
            "duplicate-proof": lambda value: value["objects"].append(value["objects"][0]),
            "wrong-proof": lambda value: value["objects"][0].update(bytes="Zm9yZ2Vk"),
            "forged-spec": lambda value: value["spec"]["trees"].update(base=self.head),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                value = copy.deepcopy(original)
                change(value)
                with self.assertRaises(capsule.CapsuleError):
                    with capsule.Capsule(capsule.canonical(value), self.spec):
                        self.fail("malformed capsule was accepted")

    def test_git_proof_collection_stops_before_reading_over_limit(self):
        paths = tuple("inputs/proof-" + str(index) + "/"
                      + "/".join(f"level-{level}" for level in range(24)) + "/state.json"
                      for index in range(3))
        for index, path in enumerate(paths):
            self.write(path, capsule.canonical({"value": index}))
        self.write("checks/proof.py",
                   b"import json\n"
                   b"def capsule_main(request, context):\n"
                   b"    return [json.loads(context.read('base', path))['value']\n"
                   b"            for path in request['paths']]\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(trees={"base": revision},
                                   programs={"proof": "checks/proof.py"}, data={"base": paths})
        raw = capsule._make_bundle(self.root, spec.record())
        with capsule.Capsule(raw, spec) as prepared:
            self.assertEqual(prepared.execute("proof", {"paths": list(paths)}).value, [0, 1, 2])
        source = capsule._GitSource(self.root)
        with mock.patch.object(capsule, "MAX_ENTRIES", 8):
            with self.assertRaisesRegex(capsule.CapsuleError, "bounded Git object closure"):
                capsule._Bundle(raw, spec.record())
            with (
                mock.patch.object(capsule, "_GitSource", return_value=source),
                mock.patch.object(capsule, "_git", wraps=capsule._git) as read,
                mock.patch.object(capsule, "canonical", wraps=capsule.canonical) as serialize,
            ):
                with self.assertRaisesRegex(capsule.CapsuleError, "bounded Git object closure"):
                    capsule._make_bundle(self.root, spec.record())
                self.assertEqual(len(source.objects), capsule.MAX_ENTRIES * 8)
                self.assertEqual(read.call_count, capsule.MAX_ENTRIES * 8)
                serialize.assert_not_called()
                oid, (kind, content) = next(iter(source.objects.items()))
                read.reset_mock()
                self.assertEqual(source.get(kind, oid), content)
                read.assert_not_called()

    def test_shared_prefix_tree_parsing_is_linear_in_unique_proof_bytes(self):
        paths = tuple(f"inputs/cached/shared/leaf/value-{index:03d}.json" for index in range(64))
        contents = {path: capsule.canonical({"value": index}) for index, path in enumerate(paths)}
        for path, content in contents.items():
            self.write(path, content)
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"assertion": "checks/assertion.py"},
            data={"base": paths},
        )
        consumed, tree_sizes = {}, {}

        class CountedTree(bytes):
            def find(self, sub, start=0, *args):
                if sub == b" " and start == 0:
                    oid = capsule._oid("tree", self)
                    consumed[oid] = consumed.get(oid, 0) + len(self)
                return super().find(sub, start, *args)

        git = capsule._git

        def read(root, *arguments):
            raw = git(root, *arguments)
            if arguments[:2] == ("cat-file", "tree"):
                tree_sizes[arguments[2]] = len(raw)
                return CountedTree(raw)
            return raw

        with mock.patch.object(capsule, "_git", side_effect=read):
            raw = capsule._make_bundle(self.root, spec.record())
        with self.subTest(stage="construction"):
            self.assertEqual(sum(consumed.values()), sum(tree_sizes.values()))
            self.assertEqual(consumed, tree_sizes)
        consumed.clear()
        decode = capsule.base64.b64decode

        def decode_proof(value, **kwargs):
            content = decode(value, **kwargs)
            return (CountedTree(content) if capsule._oid("tree", content) in tree_sizes
                    else content)

        with mock.patch.object(capsule.base64, "b64decode", side_effect=decode_proof):
            bundle = capsule._Bundle(raw, spec.record())
        with self.subTest(stage="independent-validation"):
            self.assertEqual(sum(consumed.values()), sum(tree_sizes.values()))
            self.assertEqual(consumed, tree_sizes)
        self.assertEqual({path: bundle.content("base", path) for path in paths}, contents)

    def test_tree_cache_revalidates_replaced_proofs_and_rejects_bad_suffixes(self):
        bundle = capsule._Bundle(self.bundle, self.spec.record())
        for source in (capsule._ObjectSource(dict(bundle.objects)), capsule._GitSource(self.root)):
            with self.subTest(source=type(source).__name__):
                oid = source.tree(self.base)
                expected = source.lookup(self.base, "checks/helper.py")
                kind, content = source.objects[oid]
                altered = content[:-1] + bytes([content[-1] ^ 1])
                source.objects[oid] = (kind, altered)
                with self.assertRaises(capsule.CapsuleError):
                    source.lookup(self.base, "checks/helper.py")
                source.objects[oid] = (kind, content)
                self.assertEqual(source.lookup(self.base, "checks/helper.py"), expected)
        value = capsule.parse(self.bundle, capsule.MAX_BUNDLE_BYTES)
        index = next(index for index, entry in enumerate(value["objects"]) if entry["kind"] == "tree")
        for change in ("oid", "bytes"):
            mutated = copy.deepcopy(value)
            mutated["objects"][index][change] = ("0" * 40 if change == "oid" else
                                                 capsule.base64.b64encode(altered).decode("ascii"))
            with self.subTest(change=change), self.assertRaises(capsule.CapsuleError):
                capsule._Bundle(capsule.canonical(mutated), self.spec.record())
        entry = b"100644 item\0" + bytes.fromhex("a" * 40)
        for suffix in (entry, b"100644 incomplete\0"):
            tree = entry + suffix
            tree_oid = capsule._oid("tree", tree)
            commit = f"tree {tree_oid}\n".encode("ascii")
            commit_oid = capsule._oid("commit", commit)
            source = capsule._ObjectSource({
                commit_oid: ("commit", commit), tree_oid: ("tree", tree),
            })
            for attempt in range(2):
                with self.subTest(suffix=suffix, attempt=attempt), self.assertRaises(capsule.CapsuleError):
                    source.lookup(commit_oid, "item")

    def test_undeclared_program_and_data_do_not_fall_back(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("not-declared", {})
        with self.attack("def capsule_main(request, context):\n    return context.read('base', 'unlisted.json')\n") as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("attack", {})

    def test_missing_static_trusted_import_is_rejected_during_preparation(self):
        with self.assertRaisesRegex(capsule.CapsuleError, "outside complete trusted closure"):
            self.attack("import nonexistent_capsule_module\n"
                        "def capsule_main(request, context):\n    return True\n")

    def test_transitive_relative_packages_and_explicit_dynamic_closure(self):
        self.write("checks/deep/__init__.py", b"from .leaf import answer\n")
        self.write("checks/deep/leaf.py", b"answer = 41\n")
        self.write("checks/second.py", b"from .deep import answer\ndef value():\n    return answer + 1\n")
        self.write("checks/dynamic.py", b"answer = 'declared-dynamic'\n")
        self.write("checks/transitive.py",
                   b"def capsule_main(request, context):\n"
                   b"    from checks import second\n"
                   b"    return [second.value(), context.load_module('checks.dynamic').answer]\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"transitive": "checks/transitive.py"},
            modules=("checks.dynamic",),
        )
        with capsule.prepare(self.root, spec) as prepared:
            result = prepared.execute("transitive", {})
        self.assertEqual(result.value, [42, "declared-dynamic"])
        paths = {entry["path"] for entry in result.receipt["loaded"]}
        self.assertTrue({"checks/deep/__init__.py", "checks/deep/leaf.py",
                         "checks/second.py", "checks/dynamic.py"}.issubset(paths))
        undeclared = capsule.CapsuleSpec(trees=spec.trees, programs=spec.programs)
        with capsule.prepare(self.root, undeclared) as prepared:
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("transitive", {})

    def test_static_stdlib_package_submodules_and_exported_attributes(self):
        self.write("checks/stdlib_helper.py",
               b"from xml.etree import ElementTree\n"
               b"def tag():\n    return ElementTree.fromstring('<sealed/>').tag\n")
        with self.attack(
            "from checks import stdlib_helper\n"
            "from collections import Counter, abc\n"
            "from concurrent.futures import Future\n"
            "from os.path import basename\n"
            "from urllib import parse\n"
            "def capsule_main(request, context):\n"
            "    return {'xml': stdlib_helper.tag(), 'counts': dict(Counter('aba')),\n"
            "            'mapping': isinstance({}, abc.Mapping), 'done': Future().done(),\n"
            "            'basename': basename('sealed/input'),\n"
            "            'query': parse.parse_qs('state=fixed')}\n"
        ) as prepared:
            bundle = capsule._Bundle(prepared.bundle_fd.read())
            self.assertEqual(bundle.spec["modules"], [])
            self.assertTrue({"xml.etree.ElementTree", "collections.abc", "urllib.parse"}
                        .issubset(bundle.stdlib))
            self.assertTrue({"collections.Counter", "concurrent.futures.Future"}
                            .isdisjoint(bundle.stdlib))
            result = prepared.execute("attack", {})
        self.assertEqual(result.value, {
            "xml": "sealed", "counts": {"a": 2, "b": 1}, "mapping": True, "done": False,
            "basename": "input", "query": {"state": ["fixed"]},
        })

    def test_static_stdlib_resolution_never_uses_candidate_importers(self):
        paths = ("xml/__init__.py", "xml/etree/__init__.py", "xml/etree/ElementTree.py")
        for path in paths:
            self.write(path, b"raise RuntimeError('candidate stdlib shadow executed')\n")
        shadow = types.ModuleType("xml")
        shadow.__path__ = [str(self.root / "xml")]

        class CandidateFinder:
            def find_spec(self, fullname, path=None, target=None):
                raise AssertionError(f"ambient importer consulted: {fullname}")

        try:
            with (
                mock.patch.object(sys, "path", [str(self.root), *sys.path]),
                mock.patch.object(sys, "meta_path", [CandidateFinder(), *sys.meta_path]),
                mock.patch.dict(sys.modules, {"xml": shadow}),
            ):
                with self.attack(
                    "from xml.etree import ElementTree\n"
                    "def capsule_main(request, context):\n"
                    "    return ElementTree.fromstring('<trusted/>').tag\n"
                ) as prepared:
                    result = prepared.execute("attack", {})
            self.assertEqual(result.value, "trusted")
        finally:
            for path in paths:
                (self.root / path).unlink()

    def test_undeclared_stdlib_submodules_and_missing_exports_still_reject(self):
        attempts = (
            "__import__('xml.etree.ElementTree', fromlist=['ElementTree'])",
            "from xml.etree import nonexistent_capsule_export",
            "__import__('importlib.util', fromlist=['util'])",
            "__import__('importlib', fromlist=['util']).util",
            "importlib.import_module('importlib.util')",
            "__import__('json', fromlist=['decoder']).decoder",
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.attack(
                    "import xml.etree, importlib, json\n"
                    "def capsule_main(request, context):\n"
                    "    try:\n        " + attempt + "\n"
                    "    except Exception:\n        pass\n"
                    "    return {'status': 'pass'}\n"
                ) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {})

    def test_program_visible_module_cache_excludes_runtime_preloads(self):
        with self.attack(
            "import sys\n"
            "def capsule_main(request, context):\n"
            "    return sorted(sys.modules)\n"
        ) as prepared:
            result = prepared.execute("attack", {})
        self.assertEqual(set(result.value), {"sys", "checks", "checks.attack"})

    def test_preloaded_undeclared_dynamic_imports_and_direct_cache_reads_reject(self):
        with self.attack("""import sys
cached_import = __import__
def capsule_main(request, context):
    name = request['module']
    if request['route'] == 'index':
        return sys.modules[name].__name__
    if request['route'] == 'get':
        return sys.modules.get(name) is None
    try:
        cached_import(name)
    except Exception:
        pass
    return {'status': 'pass'}
""") as prepared:
            for name in ("__capsule_runtime__", "subprocess", "ctypes"):
                for route in ("import", "index", "get"):
                    with self.subTest(module=name, route=route):
                        request = {"module": name, "route": route}
                        if route == "get":
                            self.assertTrue(prepared.execute("attack", request).value)
                        else:
                            with self.assertRaises(capsule.CapsuleError):
                                prepared.execute("attack", request)

    def test_cached_dynamic_imports_reject_undeclared_module_table_injection(self):
        with self.attack("""import builtins, importlib, sys
cached_import = __import__
cached_builtin = builtins.__import__
cached_portable_import = importlib.__import__
cached_import_module = importlib.import_module
def capsule_main(request, context):
    sys.modules[request['module']] = sys
    try:
        if request['route'] == 'builtin':
            cached_import(request['module'])
        elif request['route'] == 'builtins':
            cached_builtin(request['module'])
        elif request['route'] == 'portable':
            cached_portable_import(request['module'])
        else:
            cached_import_module(request['module'])
    except Exception:
        pass
    return {'status': 'pass'}
""") as prepared:
            for route in ("builtin", "builtins", "portable", "importlib"):
                with self.subTest(route=route), self.assertRaises(capsule.CapsuleError):
                    prepared.execute("attack", {"module": "subprocess", "route": route})

    def test_declared_cached_imports_preserve_static_dynamic_and_nested_execution(self):
        self.write("checks/cached_dynamic.py", b"value = 'declared-dynamic'\n")
        self.write("checks/cached_package/__init__.py", b"from .. import cached_dynamic as alias\n")
        self.write("checks/cached.py", b"""import importlib, sys
from checks import helper
from checks.cached_package import alias
cached_import = __import__
def capsule_main(request, context):
    json = cached_import('json')
    dynamic = importlib.import_module('checks.cached_dynamic')
    xml = importlib.import_module('xml.etree.ElementTree')
    value = {'static': helper.value(), 'dynamic': dynamic.value,
             'alias': alias is dynamic,
             'json': json.loads('{"sealed":true}'), 'xml': xml.fromstring('<sealed/>').tag,
             'cached': sys.modules['json'] is json and sys.modules[dynamic.__name__] is dynamic
                       and sys.modules['xml.etree.ElementTree'] is xml}
    if request.get('nested'):
        return value
    return {'value': value, 'nested': context.invoke('cached', {'nested': True})}
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision}, programs={"cached": "checks/cached.py"},
            modules=("json", "xml.etree.ElementTree", "checks.cached_dynamic"),
        )
        with capsule.prepare(self.root, spec) as prepared:
            result = prepared.execute("cached", {})
        expected = {"static": "trusted-module", "dynamic": "declared-dynamic",
                    "alias": True, "json": {"sealed": True}, "xml": "sealed", "cached": True}
        self.assertEqual(result.value["value"], expected)
        self.assertEqual(result.value["nested"]["value"], expected)
        for receipt in (result.receipt, result.value["nested"]["receipt"]):
            self.assertEqual(receipt["program"], "cached")
            self.assertTrue({"checks/helper.py", "checks/cached_dynamic.py"}.issubset(
                entry["path"] for entry in receipt["loaded"]))

    def test_artifact_existence_uses_sealed_entries_after_path_swap(self):
        self.write("checks/existence.py",
               b"def capsule_main(request, context):\n"
               b"    return {path: context.entry('head', path)['mode'] is not None\n"
               b"            for path in request['paths']}\n")
        revision = self.commit()
        paths = ("inputs/state.json", "inputs/not-present.json")
        spec = capsule.CapsuleSpec(
            trees={"base": revision, "head": revision},
            programs={"existence": "checks/existence.py"}, data={"head": paths},
        )
        saved = (self.root / paths[0]).read_bytes()
        try:
            with capsule.prepare(self.root, spec) as prepared:
                request = {"paths": list(paths)}
                before = prepared.execute("existence", request)
                (self.root / paths[0]).unlink()
                self.write(paths[1], b"ambient existence is not authority\n")
                after = prepared.execute("existence", request)
            self.assertEqual(before.value, {paths[0]: True, paths[1]: False})
            self.assertEqual(after.value, before.value)
            key = b"test-only-capsule-signing-key-1234"
            for result in (before, after):
                loaded = {(entry["tree"], entry["path"]) for entry in result.receipt["loaded"]}
                self.assertTrue({("head", path) for path in paths} <= loaded)
                self.assertEqual(
                    capsule.verify_receipt(capsule.sign_receipt(result, key), key, result),
                    result.receipt,
                )
        finally:
            self.write(paths[0], saved)
            (self.root / paths[1]).unlink(missing_ok=True)

    def test_live_path_existence_cannot_change_a_receipted_verdict(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack(
            "import os\n"
            "def capsule_main(request, context):\n"
            "    return {'status': 'pass' if os.access(request['path'], os.F_OK) else 'fail'}\n"
        ) as prepared:
            child = subprocess.Popen(
                [capsule.PYTHON, "-I", "-S", "-c", "import sys; sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                request = {"path": f"/proc/{child.pid}"}
                for alive in (True, False):
                    if not alive:
                        child.stdin.close()
                        child.wait(timeout=3)
                    self.assertEqual(os.access(request["path"], os.F_OK), alive)
                    with self.subTest(alive=alive):
                        with self.assertRaises(capsule.CapsuleError):
                            prepared.execute("attack", request)
            finally:
                child.stdin.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_live_chroot_and_chown_oracles_cannot_return_receipted_verdicts(self):
        source = """import errno, os
def capsule_main(request, context):
    try:
        if request['operation'] == 'chroot':
            os.chroot(request['path'])
        else:
            os.chown(request['path'], -1, -1)
    except OSError as error:
        return {'exists': error.errno != errno.ENOENT, 'errno': error.errno}
    return {'exists': True, 'errno': 0}
"""
        control = (source + "\nimport json, sys\n"
                   "print(json.dumps(capsule_main(json.loads(sys.argv[1]), None)))\n")
        path = self.root / "inputs/owned-metadata-oracle"
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        try:
            with self.attack(source) as prepared:
                for operation in ("chroot", "chown"):
                    for present in (False, True):
                        with self.subTest(operation=operation, present=present):
                            path.unlink(missing_ok=True)
                            if present:
                                path.write_bytes(b"ordinary owned file, never a chroot directory\n")
                            request = {"operation": operation, "path": str(path)}
                            observed = subprocess.run(
                                [capsule.PYTHON, "-I", "-S", "-c", control,
                                 capsule.canonical(request).decode("ascii")],
                                env=capsule.ENVIRONMENT, capture_output=True, check=True, timeout=3,
                            )
                            expected_errno = (errno.ENOTDIR if operation == "chroot" else 0) if present else errno.ENOENT
                            self.assertEqual(json.loads(observed.stdout),
                                             {"exists": present, "errno": expected_errno})
                            with self.assertRaises(capsule.CapsuleError):
                                prepared.execute("attack", request)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_os_audit_namespace_is_closed_and_denials_are_latched(self):
        bundle = capsule._Bundle(self.bundle, self.spec.record())
        for event in ("os.chroot", "os.chown", "os.chmod", "os.utime", "os.rename",
                      "os.getxattr", "os.setxattr", "os.removexattr", "os.listxattr",
                      "os.future_path_operation"):
            with self.subTest(event=event):
                guard = capsule._Guard(bundle, "", b"")
                with self.assertRaises(capsule.CapsuleError):
                    guard.audit(event, ())
                self.assertEqual(len(guard.denied), 1)
        guard = capsule._Guard(bundle, "", b"")
        guard.audit("mmap.__new__", (-1, 4096, 0, 0))
        self.assertEqual(guard.denied, [])

    def test_caught_pathname_metadata_probes_cannot_return_pass(self):
        attempts = {
            "access": "os.access(path, os.F_OK)",
            "access-effective-ids": "os.access(path, os.F_OK, effective_ids=True)",
            "access-no-follow": "os.access(path, os.F_OK, follow_symlinks=False)",
            "stat": "os.stat(path)",
            "lstat": "os.lstat(path)",
            "readlink": "os.readlink(path)",
            "statvfs": "os.statvfs(path)",
            "pathconf": "os.pathconf(path, 'PC_NAME_MAX')",
            "getcwd": "os.getcwd()",
            "chroot": "os.chroot(path)",
            "chown": "os.chown(path, -1, -1)",
            "chown-no-follow": "os.chown(path, -1, -1, follow_symlinks=False)",
            "lchown": "os.lchown(path, -1, -1)",
            "chmod": "os.chmod(path, 0o600)",
            "getxattr": "os.getxattr(path, 'user.capsule')",
            "getxattr-no-follow": "os.getxattr(path, 'user.capsule', follow_symlinks=False)",
            "listxattr": "os.listxattr(path)",
            "listxattr-no-follow": "os.listxattr(path, follow_symlinks=False)",
            "setxattr": "os.setxattr(path, 'user.capsule', b'probe')",
            "removexattr": "os.removexattr(path, 'user.capsule')",
            "utime": "os.utime(path, None)",
        }
        for label, attempt in attempts.items():
            with self.subTest(operation=label):
                with self.attack(
                    "import os\n"
                    "def capsule_main(request, context):\n"
                    "    path = request['path']\n"
                    "    try:\n        " + attempt + "\n"
                    "    except Exception:\n        pass\n"
                    "    return {'status': 'pass'}\n"
                ) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {"path": str(self.root / "inputs/state.json")})

    def test_metadata_syscall_filters_cover_both_supported_abis(self):
        policies = {
            "x86_64": (0xC000003E, {
                0, 1, 3, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20,
                22, 23, 24, 25, 28, 32, 33, 35, 39, 60, 72, 96, 97, 98,
                102, 104, 107, 108, 110, 131, 186, 201, 202, 213, 219, 228,
                229, 230, 231, 232, 233, 270, 271, 281, 291, 292, 293,
                295, 296, 302, 318, 319, 327, 328, 441,
            }, {
                "access": 21, "faccessat": 269, "faccessat2": 439, "getcwd": 79,
                "stat": 4, "lstat": 6, "newfstatat": 262, "statx": 332,
                "ustat": 136, "statfs": 137, "fstatfs": 138, "lookup_dcookie": 212,
                "readlink": 89, "readlinkat": 267,
                "getdents": 78, "getdents64": 217, "utime": 132, "utimes": 235,
                "futimesat": 261, "utimensat": 280,
                "chroot": 161, "chdir": 80, "fchdir": 81, "pivot_root": 155,
                "chown": 92, "fchown": 93, "lchown": 94, "fchownat": 260,
                "chmod": 90, "fchmod": 91, "fchmodat": 268, "fchmodat2": 452,
                "open": 2, "creat": 85, "openat": 257, "openat2": 437,
                "name_to_handle_at": 303, "open_by_handle_at": 304,
                "mkdir": 83, "rmdir": 84, "mknod": 133, "mkdirat": 258, "mknodat": 259,
                "link": 86, "symlink": 88, "linkat": 265, "symlinkat": 266,
                "unlink": 87, "unlinkat": 263, "rename": 82, "renameat": 264, "renameat2": 316,
                "truncate": 76, "ftruncate": 77, "mount": 165, "umount2": 166,
                "quotactl": 179, "quotactl_fd": 443, "acct": 163, "uselib": 134,
                "swapon": 167, "swapoff": 168, "execve": 59, "execveat": 322,
                "inotify_add_watch": 254, "fanotify_mark": 301,
                **{f"xattr-{number}": number for number in range(188, 200)},
            }),
            "aarch64": (0xC00000B7, {
                20, 21, 22, 23, 24, 25, 57, 59, 62, 63, 64, 65, 66, 67, 68,
                69, 70, 72, 73, 80, 93, 94, 98, 101, 113, 114, 115, 124,
                128, 132, 134, 135, 139, 163, 165, 169, 172, 173, 174, 175,
                176, 177, 178, 214, 215, 216, 222, 226, 233, 261, 278,
                279, 286, 287, 441,
            }, {
                "faccessat": 48, "faccessat2": 439, "getcwd": 17,
                "newfstatat": 79, "statx": 291, "statfs": 43, "fstatfs": 44,
                "lookup_dcookie": 18, "readlinkat": 78, "getdents64": 61, "utimensat": 88,
                "chroot": 51, "chdir": 49, "fchdir": 50, "pivot_root": 41,
                "fchownat": 54, "fchown": 55, "fchmod": 52, "fchmodat": 53, "fchmodat2": 452,
                "openat": 56, "openat2": 437, "name_to_handle_at": 264, "open_by_handle_at": 265,
                "mknodat": 33, "mkdirat": 34, "unlinkat": 35, "symlinkat": 36, "linkat": 37,
                "renameat": 38, "renameat2": 276, "truncate": 45, "ftruncate": 46,
                "mount": 40, "umount2": 39, "quotactl": 60, "quotactl_fd": 443,
                "acct": 89, "swapon": 224, "swapoff": 225, "execve": 221, "execveat": 281,
                "inotify_add_watch": 27, "fanotify_mark": 263,
                **{f"xattr-{number}": number for number in range(5, 17)},
            }),
        }

        def verdict(instructions, architecture, number, arguments=(0,) * 6):
            data = {0: number & 0xFFFFFFFF, 4: architecture}
            for index, argument in enumerate(arguments):
                data[16 + 8 * index] = argument & 0xFFFFFFFF
                data[20 + 8 * index] = (argument >> 32) & 0xFFFFFFFF
            pc, accumulator = 0, None
            while pc < len(instructions):
                code, yes, no, operand = instructions[pc]
                if code == 0x20:
                    accumulator = data[operand]
                elif code == 0x15:
                    pc += yes if accumulator == operand else no
                elif code == 0x35:
                    pc += yes if accumulator >= operand else no
                elif code == 0x06:
                    return operand
                else:
                    self.fail(f"unsupported seccomp instruction: {code}")
                pc += 1
            self.fail("seccomp filter has no verdict")

        for machine, (architecture, allowed, denied) in policies.items():
            with self.subTest(machine=machine):
                instructions = []

                def prctl(option, *args):
                    if option == 22:
                        program = args[1]._obj
                        instructions.extend((entry.code, entry.jt, entry.jf, entry.k)
                                            for entry in program.filters[:program.length])
                    return 0

                kernel = mock.Mock()
                kernel.prctl.side_effect = prctl
                with mock.patch.object(os, "uname", return_value=types.SimpleNamespace(machine=machine)):
                    with mock.patch.object(capsule.ctypes, "CDLL", return_value=kernel):
                        capsule._lock_worker_kernel()
                for name, number in denied.items():
                    with self.subTest(syscall=name):
                        self.assertEqual(verdict(instructions, architecture, number), 0x80000000)
                for number in allowed:
                    self.assertEqual(verdict(instructions, architecture, number), 0x7FFF0000)
                    self.assertEqual(verdict(instructions, architecture ^ 1, number), 0x80000000)
                    self.assertEqual(verdict(instructions, architecture, number | 0x40000000), 0x80000000)
                for number in {*range(512), 4096, -1} - allowed:
                    self.assertEqual(verdict(instructions, architecture, number), 0x80000000,
                                     f"{machine}: unexpected syscall capability {number}")
                fcntl_number, prlimit_number = (72, 302) if machine == "x86_64" else (25, 261)
                for command in (0, 1, 2, 3, 1030, 1032, 1033, 1034):
                    self.assertEqual(verdict(instructions, architecture, fcntl_number,
                                             (3, command, 0, 0, 0, 0)), 0x7FFF0000)
                for command in (4, 5, 6, 7, 8, 9, 10, 1024, 1025, 1031, 4096, 1 << 32):
                    self.assertEqual(verdict(instructions, architecture, fcntl_number,
                                             (3, command, 0, 0, 0, 0)), 0x80000000)
                for pid, new_limit in ((1, 0), (0, 1), (1 << 32, 0), (0, 1 << 32)):
                    self.assertEqual(verdict(instructions, architecture, prlimit_number,
                                             (pid, 7, new_limit, 0, 0, 0)), 0x80000000)
        with mock.patch.object(os, "uname", return_value=types.SimpleNamespace(machine="riscv64")):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule._lock_worker_kernel()

    def test_absence_and_symlink_data_are_exact_inert_artifacts(self):
        link = self.root / "inputs/link.json"
        link.symlink_to("never-follow.json")
        self.write("checks/inert.py",
                   b"def capsule_main(request, context):\n"
                   b"    return {'missing': context.read('head', 'inputs/not-present.json'),\n"
                   b"            'link': context.read('head', 'inputs/link.json').decode(),\n"
                   b"            'mode': context.entry('head', 'inputs/link.json')['mode']}\n")
        try:
            revision = self.commit()
            spec = capsule.CapsuleSpec(
                trees={"base": revision, "head": revision},
                programs={"inert": "checks/inert.py"},
                data={"head": ("inputs/not-present.json", "inputs/link.json")},
            )
            with capsule.prepare(self.root, spec) as prepared:
                result = prepared.execute("inert", {})
            self.assertEqual(result.value, {"missing": None, "link": "never-follow.json", "mode": "120000"})
        finally:
            link.unlink()

    def test_caught_path_import_process_and_network_fallbacks_cannot_return_pass(self):
        attempts = {
            "open": "open('/etc/hostname').read()",
            "path": "__import__('pathlib').Path('/etc/hostname').read_bytes()",
            "import": "__import__('unlisted_module')",
            "file-loader": "__import__('importlib.util', fromlist=['util']).spec_from_file_location('x', '/etc/hostname').loader.get_data('/etc/hostname')",
            "fork": "__import__('os').fork()",
            "signal": "__import__('os').kill(__import__('os').getpid(), 0)",
            "process-group": "__import__('os').setpgid(0, __import__('os').getppid())",
            "session": "__import__('os').setsid()",
            "process": "__import__('subprocess').run(['/bin/true'])",
            "ctypes": "__import__('ctypes').CDLL(None)",
            "network": "__import__('socket').socket()",
            "data": "context.read('head', 'unlisted.json')",
        }
        for label, attempt in attempts.items():
            with self.subTest(label=label):
                source = ("import pathlib, importlib.util, os, subprocess, ctypes, socket\n"
                          "def capsule_main(request, context):\n"
                          "    try:\n        " + attempt + "\n"
                          "    except Exception:\n        pass\n"
                          "    return {'status': 'pass'}\n")
                with self.attack(source) as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {})

    def test_worker_cannot_escape_group_then_stall_cleanup(self):
        before = self.descriptors()
        with self.attack(
            "import os,time\n"
            "def capsule_main(request, context):\n"
            "    os.setpgid(0, os.getppid())\n"
            "    time.sleep(20)\n"
        ) as prepared:
            started = time.monotonic()
            with self.assertRaises(capsule.CapsuleError):
                prepared.execute("attack", {}, timeout=0.3)
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(before, self.descriptors())

    def test_worker_descriptor_limits_preserve_nested_invocation_and_cleanup(self):
        self.write("checks/descriptors.py", b"""import os, resource
def capsule_main(request, context):
    descriptors = []
    failure = None
    try:
        for _ in range(request['attempts']):
            try:
                created = ([os.memfd_create('bounded-worker', os.MFD_CLOEXEC)]
                           if request['kind'] == 'memfd' else os.pipe2(os.O_CLOEXEC))
            except OSError as error:
                failure = error.errno
                break
            descriptors.extend(created)
        nested = context.invoke('assertion', {'state': 'descriptor-bound'})
        result = {'allocated': len(descriptors), 'errno': failure,
                  'limit': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
                  'nested_status': nested['value']['status'],
                  'nested_program': nested['receipt']['program']}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    descriptor = os.memfd_create('recovered-worker', os.MFD_CLOEXEC)
    os.close(descriptor)
    result['recovered'] = True
    return result
""")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={**self.spec.trees, "base": revision},
            programs={"descriptors": "checks/descriptors.py", "assertion": "checks/assertion.py"},
            data=self.spec.data,
        )
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with capsule.prepare(self.root, spec) as prepared:
            for kind in ("memfd", "pipe"):
                with self.subTest(kind=kind):
                    result = prepared.execute("descriptors", {"kind": kind, "attempts": 65}).value
                    self.assertEqual(result["errno"], errno.EMFILE)
                    self.assertEqual(result["limit"], [64, 64])
                    self.assertGreater(result["allocated"], 0)
                    self.assertLessEqual(result["allocated"], 64)
                    self.assertEqual(result["nested_status"], "pass")
                    self.assertEqual(result["nested_program"], "assertion")
                    self.assertTrue(result["recovered"])
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_worker_memfd_growth_stops_at_file_size_limit(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack("""import os, resource
def capsule_main(request, context):
    descriptor = os.memfd_create('bounded-growth', os.MFD_CLOEXEC)
    try:
        initial = os.write(descriptor, b'x' * (request['limit'] - 1))
        crossing = os.write(descriptor, b'yz')
        errors = {}
        for name in ('write', 'pwrite'):
            try:
                if name == 'write':
                    os.write(descriptor, b'x')
                else:
                    os.pwrite(descriptor, b'x', request['limit'])
            except OSError as error:
                errors[name] = error.errno
        return {'initial': initial, 'crossing': crossing, 'errors': errors,
                'size': os.fstat(descriptor).st_size,
                'limit': list(resource.getrlimit(resource.RLIMIT_FSIZE))}
    finally:
        os.close(descriptor)
""") as prepared:
            limit = 1024 * 1024
            result = prepared.execute("attack", {"limit": limit}).value
        self.assertEqual(result["initial"], limit - 1)
        self.assertEqual(result["crossing"], 1)
        self.assertEqual(result["errors"], {"write": errno.EFBIG, "pwrite": errno.EFBIG})
        self.assertEqual(result["size"], limit)
        self.assertEqual(result["limit"], [limit, limit])
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_closed_kernel_policy_preserves_private_memory_ipc_and_nested_execution(self):
        before = {fd: identity[:2] for fd, identity in self.descriptors().items()}
        with self.attack("""import fcntl, mmap, os, selectors
def capsule_main(request, context):
    if request.get('nested'):
        return 'nested-sealed'
    descriptors = []
    try:
        descriptor = os.memfd_create('private-buffer', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        descriptors.append(descriptor)
        with mmap.mmap(-1, 4096) as memory:
            memory[:6] = b'sealed'
            os.write(descriptor, memory[:6])
        os.pwrite(descriptor, b'IPC', 0)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, 15)
        duplicate = os.dup(descriptor)
        descriptors.append(duplicate)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        descriptors.extend((read_fd, write_fd))
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            os.write(write_fd, b'ready')
            ready = bool(selector.select(1))
            message = os.read(read_fd, 5)
        return {'memory': os.pread(duplicate, 6, 0).decode(), 'size': os.fstat(duplicate).st_size,
                'seals': fcntl.fcntl(duplicate, fcntl.F_GET_SEALS),
                'ready': ready, 'message': message.decode(),
                'nested': context.invoke('attack', {'nested': True})['value']}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
""") as prepared:
            result = prepared.execute("attack", {})
        self.assertEqual(result.value, {
            "memory": "IPCled", "size": 6, "seals": 15, "ready": True,
            "message": "ready", "nested": "nested-sealed",
        })
        self.assertEqual(before, {fd: identity[:2] for fd, identity in self.descriptors().items()})

    def test_mutating_sys_path_cannot_load_an_ambient_module(self):
        ambient = self.root / "unlisted_module.py"
        ambient.write_bytes(b"status = 'pass'\n")
        try:
            with self.attack(
                "import sys\n"
                "def capsule_main(request, context):\n"
                f"    sys.path.insert(0, {str(self.root)!r})\n"
                "    return __import__('unlisted_module').status\n"
            ) as prepared:
                with self.assertRaises(capsule.CapsuleError):
                    prepared.execute("attack", {})
        finally:
            ambient.unlink()

    def test_parent_credentials_are_not_in_child_environment(self):
        with self.attack("import os\ndef capsule_main(request, context):\n    return dict(os.environ)\n") as prepared:
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-secret", "WORKFLOW_HMAC_KEY": "test-key"}):
                result = prepared.execute("attack", {})
        self.assertNotIn("GITHUB_TOKEN", result.value)
        self.assertNotIn("WORKFLOW_HMAC_KEY", result.value)
        self.assertNotIn("PYTHONPATH", result.value)

    def test_signing_verification_rejects_forgery_transplant_and_replay(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            first = prepared.execute("assertion", {"round": 1})
            second = prepared.execute("assertion", {"round": 1})
        key = b"test-only-capsule-signing-key-1234"
        signed = capsule.sign_receipt(first, key)
        self.assertEqual(capsule.verify_receipt(signed, key, first), first.receipt)
        with self.assertRaises(capsule.CapsuleError):
            capsule.verify_receipt(signed, key, second)
        value = capsule.parse(signed)
        value["receipt"]["program_sha256"] = "f" * 64
        with self.assertRaises(capsule.CapsuleError):
            capsule.verify_receipt(capsule.canonical(value), key, first)
        with self.assertRaises(capsule.CapsuleError):
            capsule.sign_receipt(capsule.ExecutionResult(first.receipt_bytes, first.output_bytes), key)
        with self.assertRaises(capsule.CapsuleError):
            capsule.sign_receipt(first, b"short")

    def test_success_failure_and_timeout_leave_no_owned_descriptor(self):
        before = self.descriptors()
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            prepared.execute("assertion", {})
        self.assertEqual(before, self.descriptors())
        programs = {
            "exception": "raise RuntimeError('intentional crash')",
            "crash": "__import__('os')._exit(3)",
            "empty": "__import__('os')._exit(0)",
            "partial": "__import__('os').write(1, b'{')\n    return {'status': 'pass'}",
            "stdout": "print('forged pass')\n    return {'status': 'pass'}",
            "oversized": f"return 'x' * {capsule.MAX_OUTPUT_BYTES + 1}",
            "timeout": "while True:\n        pass",
        }
        for name, body in programs.items():
            with self.subTest(name=name):
                with self.attack("def capsule_main(request, context):\n    " + body + "\n") as prepared:
                    with self.assertRaises(capsule.CapsuleError):
                        prepared.execute("attack", {}, timeout=0.3 if name == "timeout" else 3)
                self.assertEqual(before, self.descriptors())

    def test_interruption_closes_liveness_and_reaps_guardian(self):
        with capsule.Capsule(self.bundle, self.spec) as prepared:
            before = self.descriptors()
            real_selector = selectors.DefaultSelector

            class InterruptingSelector:
                def __enter__(self):
                    self.delegate = real_selector()
                    return self
                def __exit__(self, *exc):
                    self.delegate.close()
                def register(self, *args):
                    return self.delegate.register(*args)
                def get_map(self):
                    return self.delegate.get_map()
                def select(self, *args):
                    raise KeyboardInterrupt()

            with mock.patch.object(selectors, "DefaultSelector", InterruptingSelector):
                with self.assertRaises(KeyboardInterrupt):
                    prepared.execute("checker", {})
            self.assertEqual(before, self.descriptors())

    def test_missing_platform_fails_closed_without_process_creation(self):
        with mock.patch.object(capsule.sys, "platform", "unsupported"):
            with mock.patch.object(subprocess, "Popen") as launch:
                with self.assertRaises(capsule.CapsuleUnavailable) as error:
                    capsule.prepare(self.root, self.spec)
                self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                launch.assert_not_called()
        with mock.patch.object(os, "memfd_create", side_effect=OSError(errno.ENOSYS, "unavailable")):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule.SealedBytes(b"must-not-fall-back", "test", 100)

    def test_missing_proc_descriptors_are_explicitly_unavailable_before_launch(self):
        capsule._platform()
        for number in (errno.ENOENT, errno.EACCES, errno.ENOTDIR):
            with self.subTest(errno=number):
                with (
                    mock.patch.object(os, "listdir", side_effect=OSError(number, "proc unavailable")),
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError(
                        "process created before platform admission")) as launch,
                    mock.patch.object(os, "memfd_create") as create,
                ):
                    with self.assertRaises(capsule.CapsuleUnavailable) as error:
                        capsule.prepare(self.root, self.spec)
                    self.assertEqual(error.exception.disposition, "sealed-capsule-unavailable")
                    launch.assert_not_called()
                    create.assert_not_called()
                    with (
                        mock.patch.object(os, "write") as diagnostic,
                        mock.patch.object(os, "waitpid", side_effect=ChildProcessError),
                        mock.patch.object(os, "fork") as fork,
                    ):
                        with self.assertRaises(SystemExit) as exit:
                            capsule._supervise(["3", "4", "5", "6", "7", "", "", ""])
                        self.assertEqual(exit.exception.code, 125)
                        self.assertEqual(diagnostic.call_args.args[0], 2)
                        self.assertTrue(diagnostic.call_args.args[1].startswith(b"CapsuleUnavailable:"))
                        fork.assert_not_called()

    def test_parent_death_reaps_outer_and_nested_execution_groups(self):
        capsule._prctl(36, 1)
        self.write("checks/hang.py", b"import time\ndef capsule_main(request, context):\n    time.sleep(20)\n")
        self.write("checks/hang_checker.py",
                   b"def capsule_main(request, context):\n    return context.invoke('hang', request)\n")
        revision = self.commit()
        spec = capsule.CapsuleSpec(
            trees={"base": revision},
            programs={"hang": "checks/hang.py", "hang-checker": "checks/hang_checker.py"},
        )
        bundle_path = self.root / "parent-death-bundle.json"
        bundle_path.write_bytes(capsule._make_bundle(self.root, spec.record()))
        helper = """
import pathlib,sys
sys.path.insert(0,sys.argv[1])
from scripts.workflow_pilot import sealed_capsule as c
raw=pathlib.Path(sys.argv[2]).read_bytes()
spec=c.CapsuleSpec(**c.parse(raw,c.MAX_BUNDLE_BYTES)['spec'])
launch=c.subprocess.Popen
def traced(*args,**kwargs):
    child=launch(*args,**kwargs)
    print(child.pid,flush=True)
    return child
c.subprocess.Popen=traced
with c.Capsule(raw,spec) as prepared:
    prepared.execute('hang-checker',{},timeout=10)
"""
        process = subprocess.Popen(
            [capsule.PYTHON, "-I", "-S", "-c", helper, str(ROOT), str(bundle_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        observed, handles = set(), {}
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), "parent helper did not launch")
                line = process.stdout.readline()
                self.assertTrue(line.strip().isdigit(), f"helper failed: {line!r}")
                guardian = int(line)
            observed.add(guardian)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for pid in tuple(observed):
                    path = Path(f"/proc/{pid}/task/{pid}/children")
                    try:
                        observed.update(map(int, path.read_text().split()))
                    except FileNotFoundError:
                        pass
                if len(observed) >= 4:
                    break
                time.sleep(0.02)
            self.assertGreaterEqual(len(observed), 4, "nested guardian and worker were not live")
            for pid in observed:
                handles[pid] = os.pidfd_open(pid)
            process.kill()
            process.wait(timeout=3)
            deadline = time.monotonic() + 3
            pending = set(handles)
            while pending and time.monotonic() < deadline:
                for pid in tuple(pending):
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                    if not Path(f"/proc/{pid}").exists():
                        pending.remove(pid)
                if pending:
                    time.sleep(0.02)
            self.assertFalse(pending, f"parent death left live processes: {pending}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for pid, handle in handles.items():
                try:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(handle)
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            process.stdout.close()
            process.stderr.close()
            bundle_path.unlink()

    def test_noncanonical_and_duplicate_wire_json_rejected(self):
        for raw in (b'{"x":1,"x":1}\n', b'{"x":NaN}\n', b'{"x":1e999}\n',
                    b'{"x": 1}\n', b'{"x":1}', b'{}\n{}\n'):
            with self.subTest(raw=raw):
                with self.assertRaises(capsule.CapsuleError):
                    capsule.parse(raw)


if __name__ == "__main__":
    unittest.main()
