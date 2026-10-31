"""Source-backed codec obligations, not whole-suite pass-label adapters."""

import ast
import base64
import json
import lzma
import shutil
import struct
import subprocess
import unittest
import zlib

from scripts.workflow_pilot.tests.review_support import ENV, ROOT, git, request, snapshot
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools


class CjkSubjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = snapshot()
        cls.repo = cls.workspace.__enter__()
        names = subprocess.check_output(
            ["/usr/bin/git", "-C", str(ROOT), "ls-files", "-z"], env=ENV,
        ).decode().split("\0")
        for name in filter(None, names):
            if not (name.startswith("scripts/") and name.endswith(".py")
                    and "/tests/" not in name
                    or name.startswith((
                        "fonts/cjk/", "graphics/fonts/cjk/", "src/", "include/",
                        "texts/", "assets/", "reports/",
                    ))):
                continue
            target = cls.repo.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        git(cls.repo.root, "add", ".")
        git(cls.repo.root, "commit", "-qm", "captured resource codec inputs")
        cls.repo.base = git(cls.repo.root, "rev-parse", "HEAD")
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.__exit__(None, None, None)

    def scope(self, head=None):
        return request("TC-CJK-RESOURCE-STORAGE-001", "cjk-resource-index",
                       self.repo.base, head or self.repo.base)

    def assert_satisfied(self, observed):
        self.assertTrue(observed)
        self.assertEqual([(item.obligation.member, item.verdict, item.detail)
                          for item in observed if item.verdict != "satisfied"], [])
        self.assertTrue(all(item.kind == "host" and item.checks > 0 for item in observed))

    def test_complete_codec_wire_generated_and_resource_obligations(self):
        members = self.tools.members(self.scope())
        self.assertEqual({(item.family, item.role) for item in members}, {
            ("generated", "owners"), ("generated", "outputs"),
            ("generated", "consumers"), ("generated", "drift-checks"),
            ("wire", "producers"), ("wire", "consumers"), ("wire", "validators"),
            ("wire", "replay"), ("wire", "stale-bindings"),
            ("resource", "enabled"), ("resource", "disabled"),
        })
        self.assert_satisfied(self.tools.run_obligations(members, self.repo.base))

    def test_invalid_reference_bounds_are_unavailable_not_codec_regression_credit(self):
        limit = 4 * 1024 * 1024
        plain = json.dumps({"value": "x" * limit}).encode()
        small = b'{"value":1}'
        dictionary = bytearray(lzma.compress(small))
        end = 12 + (dictionary[12] + 1) * 4
        self.assertEqual(bytes(dictionary[14:16]), b"\x21\x01")
        dictionary[16] = 28
        dictionary[end - 4:end] = struct.pack("<I", zlib.crc32(dictionary[12:end - 4]))
        self.assertEqual(lzma.decompress(dictionary, memlimit=80 * 1024 * 1024), small)

        def packed(data, stream, size=None):
            return json.dumps({
                "kind": "cjk-resource-index", "version": 1, "encoding": "xz-base64",
                "decoded_bytes": len(data) if size is None else size,
                "payload": base64.b64encode(stream).decode(),
            }).encode()

        stream = lzma.compress(small)
        for data in (
            plain, packed(plain, lzma.compress(plain), limit), packed(small, dictionary),
            packed(small, stream + b"x"), packed(small, stream * 2), packed(small, stream[:-4]),
        ):
            with self.subTest(stored_bytes=len(data)):
                head = self.repo.commit({"fonts/cjk/fehrr-sources.json": data})
                members = tuple(item for item in self.tools.members(self.scope(head))
                                if item.family == "generated")
                observed = self.tools.run_obligations(members, head)
                self.assertEqual([(item.kind, item.verdict, item.checks) for item in observed
                                  if item.obligation.role == "owners"],
                                 [("host", "unavailable", 0)])

    def test_execution_only_closure_uses_the_stable_candidate_universe(self):
        path = "scripts/fonttools/cjk/unused_review_fixture.py"
        head = self.repo.commit({path: '"""Unused captured production module."""\n'})
        members = self.tools.members(self.scope(head), (self.repo.base,))
        self.assertTrue(all(path in item.source_inputs and path not in item.inputs
                            for item in members))
        wire = tuple(item for item in members if item.family == "wire")
        self.assert_satisfied(self.tools.run_obligations(wire, head))

    def test_unsupported_reference_metadata_is_setup_unavailable(self):
        seed = b'{"value":1}'
        envelope = {
            "kind": "cjk-resource-index", "version": 1, "encoding": "xz-base64",
            "decoded_bytes": len(seed), "payload": base64.b64encode(lzma.compress(seed)).decode(),
        }
        nested = json.dumps(envelope).encode()
        cases = [
            json.dumps(value).encode() for value in (
                {**envelope, "version": 2}, {**envelope, "version": True},
                {**envelope, "encoding": "unknown"}, {**envelope, "extra": 0},
                {**envelope, "decoded_bytes": len(nested),
                 "payload": base64.b64encode(lzma.compress(nested)).decode()},
            )
        ] + [b'{"value":1,"value":2}', b'{"value":1e999}', b'{"value":NaN}']
        for data in cases:
            with self.subTest(reference=data[:80]):
                head = self.repo.commit({"fonts/cjk/fehrr-sources.json": data})
                members = tuple(item for item in self.tools.members(self.scope(head))
                                if item.family == "wire")
                self.assertTrue(all(
                    item.kind == "host" and item.verdict == "unavailable" and item.checks == 0
                    for item in self.tools.run_obligations(members, head)))

    def remediation(self, broken, family, role, path="scripts/fonttools/cjk/resource_index.py"):
        original = (self.repo.root / path).read_text()
        self.assertNotEqual(original, broken)
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: original}, parent=before)
        members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == family)
        observed = self.tools.run_obligations(members, before)
        self.assertEqual([(item.verdict, item.checks) for item in observed
                          if (item.obligation.family, item.obligation.role) == (family, role)],
                         [("contract-violation", 1)])
        self.assert_satisfied(self.tools.run_obligations(members, after))

    def test_real_nonfinite_parser_mutation_is_not_false_credit(self):
        original = (self.repo.root / "scripts/fonttools/cjk/resource_index.py").read_text()
        self.remediation(original.replace("if not math.isfinite(number):", "if False:", 1),
                         "wire", "consumers")

    def test_real_recompression_mutation_is_not_false_credit(self):
        original = (self.repo.root / "scripts/fonttools/cjk/resource_index.py").read_text()
        self.remediation(original.replace("        return previous\n",
                                         "        return encode_index(value, packed=True)\n", 1),
                         "generated", "drift-checks")

    def test_semantics_preserving_local_rename_stays_green(self):
        path = "scripts/fonttools/cjk/resource_index.py"
        original = (self.repo.root / path).read_text()
        renamed = self.repo.commit({path: original.replace("previous", "old_data")})
        members = self.tools.members(self.scope(renamed))
        self.assert_satisfied(self.tools.run_obligations(members, renamed))

    def test_schema2_cli_cause_mutation_does_not_get_legacy_reader_credit(self):
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        broken = original.replace(
            'f"runtime split provenance JSON is invalid: {error}"',
            '"runtime split provenance JSON is invalid"', 1)
        self.remediation(broken, "generated", "consumers", path)

    def test_maintainer_format_mutation_is_not_hidden_by_codec_output(self):
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        broken = original.replace(
            "decode_index(read_index_bytes(root / relative)), root / relative, packed=packed,",
            "decode_index(read_index_bytes(root / relative)), root / relative, packed=True,", 1)
        self.remediation(broken, "generated", "consumers", path)

    def test_schema2_packed_reader_and_inventory_drift_use_their_real_predicates(self):
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        function = next(node for node in ast.parse(original).body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_check_runtime_split_assets")
        assignment = next(node for node in ast.walk(function)
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "lock"
                                  for target in node.targets))
        lines = original.splitlines(True)
        lines.insert(assignment.end_lineno,
                     '        if json.loads(lock_data).get("kind") == "cjk-resource-index":\n'
                     '            raise CjkFontError("controlled packed-only reader regression")\n')
        cases = [(path, "".join(lines), "consumers")]
        path = "scripts/fonttools/cjk/inventory.py"
        original = (self.repo.root / path).read_text()
        cases.append((path, original.replace(
            "root / RUNTIME_USAGE_PATH,\n",
            "root / RUNTIME_USAGE_PATH, packed=False,\n", 1), "drift-checks"))
        for path, broken, role in cases:
            with self.subTest(role=role):
                self.remediation(broken, "generated", role, path)
