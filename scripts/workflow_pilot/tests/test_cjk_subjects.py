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
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        for variant in ("same-count-selections", "writer-bypasses-selection"):
            with self.subTest(variant=variant):
                tree = ast.parse(original)
                name = (
                    "_source_selection" if variant == "same-count-selections"
                    else "write_fehrr_priority_assets"
                )
                function = next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == name)
                if variant == "same-count-selections":
                    function.body = ast.parse(
                        "return {prefix: tuple(range(len(asset['glyphs']))) "
                        "for prefix, asset in lock['assets'].items()}"
                    ).body
                else:
                    class BypassSelection(ast.NodeTransformer):
                        def visit_Call(self, node):
                            self.generic_visit(node)
                            if (isinstance(node.func, ast.Name)
                                    and node.func.id == "build_fehrr_priority_assets"):
                                node.func.id = "build_runtime_split_assets"
                            return node

                    function.body = [BypassSelection().visit(node) for node in function.body]
                before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                after = self.repo.commit({path: original}, parent=before)
                members = tuple(
                    item for item in self.tools.members(self.scope(after), (before,))
                    if item.family == "generated"
                )
                observed = self.tools.run_obligations(members, before)
                self.assertEqual(
                    [(item.verdict, item.checks) for item in observed
                     if item.obligation.role == "outputs"],
                    [("contract-violation", 1)],
                )

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
        wire = tuple(item for item in members if item.family == "wire")
        self.assert_satisfied(self.tools.run_obligations(wire, self.repo.base))
        self.assert_satisfied(self.tools.run_obligations(wire, head))
        self.assertTrue(all(path not in item.source_inputs for item in members))

    def test_fixture_presence_follows_each_observed_revision(self):
        path = "src/review_unused_fixture.c"
        added = self.repo.commit({path: "/* Owned unused source fixture. */\n"})
        added_members = self.tools.members(self.scope(added), (self.repo.base,))
        wire = tuple(item for item in added_members if item.family == "wire")
        self.assert_satisfied(self.tools.run_obligations(wire, self.repo.base))
        self.assert_satisfied(self.tools.run_obligations(wire, added))
        removed = self.repo.commit({path: None}, parent=added)
        removed_members = self.tools.members(self.scope(removed), (added,))
        wire = tuple(item for item in removed_members if item.family == "wire")
        self.assert_satisfied(self.tools.run_obligations(wire, added))
        self.assert_satisfied(self.tools.run_obligations(wire, removed))
        self.assertTrue(all(path not in item.source_inputs for item in removed_members))

    def test_valid_encode_and_writer_failures_are_contract_violations(self):
        path = "scripts/fonttools/cjk/resource_index.py"
        original = (self.repo.root / path).read_text()
        for name, roles in (
            ("encode_index", {("generated", "outputs"), ("wire", "producers"),
                              ("resource", "enabled"), ("resource", "disabled")}),
            ("encode_like_index", {("generated", "drift-checks"), ("wire", "replay")}),
        ):
            with self.subTest(operation=name):
                tree = ast.parse(original)
                function = next(
                    node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name
                )
                function.body[:0] = ast.parse(
                    'raise ResourceIndexError("controlled valid-input failure")'
                ).body
                before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                after = self.repo.commit({path: original}, parent=before)
                members = tuple(
                    item for item in self.tools.members(self.scope(after), (before,))
                    if item.family in {family for family, _ in roles}
                )
                broken = self.tools.run_obligations(members, before)
                self.assertEqual(
                    {(item.obligation.family, item.obligation.role):
                     (item.verdict, item.checks) for item in broken
                     if (item.obligation.family, item.obligation.role) in roles},
                    {role: ("contract-violation", 1) for role in roles},
                )

    def test_untyped_payload_parser_failures_are_contract_violations(self):
        path = "scripts/fonttools/cjk/resource_index.py"
        original = (self.repo.root / path).read_text()
        for failure in (
            "malformed_base64.b64decode(None, validate=True)",
            'raise RuntimeError("controlled untyped parser failure")',
            'raise AttributeError("controlled untyped parser failure")',
            'raise IndexError("controlled untyped parser failure")',
            'raise ArithmeticError("controlled untyped parser failure")',
        ):
            with self.subTest(failure=failure):
                tree = ast.parse(original)
                function = next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == "decode_index")
                function.body[:0] = ast.parse(
                    "import base64 as malformed_base64\n"
                    "import json as malformed_json\n"
                    f"if malformed_json.loads({function.args.args[0].arg}).get('payload', False) is None:\n"
                    f"    {failure}\n"
                ).body
                before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                after = self.repo.commit({path: original}, parent=before)
                members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                                if item.family == "wire")
                self.assertEqual(
                    [(item.verdict, item.checks)
                     for item in self.tools.run_obligations(members, before)
                     if item.obligation.role == "validators"],
                    [("contract-violation", 1)],
                )
                self.assert_satisfied(self.tools.run_obligations(members, after))

    def test_untyped_valid_encoder_and_writer_failures_are_contract_violations(self):
        path = "scripts/fonttools/cjk/resource_index.py"
        original = (self.repo.root / path).read_text()
        for error in (
            "TypeError", "KeyError", "RuntimeError",
            "AttributeError", "IndexError", "ArithmeticError",
        ):
            for name in ("encode_index", "encode_like_index"):
                with self.subTest(error=error, operation=name):
                    tree = ast.parse(original)
                    function = next(
                        node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == name
                    )
                    function.body[:0] = ast.parse(
                        f'raise {error}("controlled valid-input failure")'
                    ).body
                    before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                    after = self.repo.commit({path: original}, parent=before)
                    members = tuple(
                        item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == "wire"
                    )
                    role = "producers" if name == "encode_index" else "replay"
                    self.assertEqual(
                        [(item.verdict, item.checks)
                         for item in self.tools.run_obligations(members, before)
                         if item.obligation.role == role],
                        [("contract-violation", 1)],
                    )

    def test_root_validator_changes_have_real_generated_source_evidence(self):
        paths = {
            "scripts/localization/game_catalog/build.py",
            "scripts/localization/game_catalog/control_streams.py",
        }
        for path in sorted(paths):
            with self.subTest(path=path):
                original = (self.repo.root / path).read_text()
                tree = ast.parse(original)
                if path.endswith("/build.py"):
                    function = next(
                        node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_load_mapping"
                    )
                    call = next(
                        node for node in ast.walk(function)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "validate_mapping_document"
                    )
                    call.keywords = [item for item in call.keywords if item.arg != "repo_root"]
                else:
                    function = next(
                        node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "load_portrait_operand_map"
                    )
                    assignment = next(
                        node for node in ast.walk(function)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "fe8u_source"
                                for target in node.targets)
                    )
                    assignment.value = ast.Name(id="fe8u_relative", ctx=ast.Load())
                before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                after = self.repo.commit({path: original}, parent=before)
                members = tuple(
                    item for item in self.tools.members(self.scope(after), (before,))
                    if item.family == "generated"
                )
                self.assertTrue(all(paths <= set(item.inputs) for item in members))
                self.assertEqual(
                    [(item.verdict, item.checks)
                     for item in self.tools.run_obligations(members, before)
                     if item.obligation.role == "drift-checks"],
                    [("contract-violation", 1)],
                )
    def test_untyped_xz_parser_failure_is_a_contract_violation(self):
        path = "scripts/fonttools/cjk/resource_index.py"
        original = (self.repo.root / path).read_text()
        tree = ast.parse(original)
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "decode_index")
        function.body[:0] = ast.parse(
            "import lzma as malformed_xz\n"
            "raise malformed_xz.LZMAError('controlled malformed-stream failure')\n"
        ).body
        before = self.repo.commit({path: ast.unparse(tree) + "\n"})
        after = self.repo.commit({path: original}, parent=before)
        members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == "wire")
        self.assertEqual(
            [(item.verdict, item.checks) for item in self.tools.run_obligations(members, before)
             if item.obligation.role == "validators"],
            [("contract-violation", 1)],
        )
        self.assert_satisfied(self.tools.run_obligations(members, after))

    def test_actual_generator_format_regressions_do_not_get_codec_output_credit(self):
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        for name in ("build_runtime_split_assets", "build_fehrr_priority_assets"):
            with self.subTest(generator=name):
                tree = ast.parse(original)
                function = next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == name)
                class PlainResult(ast.NodeTransformer):
                    def visit_FunctionDef(self, node):
                        return node

                    def visit_Return(self, node):
                        if node.value is not None:
                            node.value = ast.Call(
                                func=ast.Name(id="source_mutant_plain_result", ctx=ast.Load()),
                                args=[node.value], keywords=[],
                            )
                        return node

                function.body = [PlainResult().visit(node) for node in function.body]
                tree.body.extend(ast.parse(
                    "def source_mutant_plain_result(outputs):\n"
                    "    import hashlib\n"
                    "    import json\n"
                    "    from .resource_index import decode_index, json_bytes\n"
                    "    index = 'fonts/cjk/fehrr-sources.json'\n"
                    "    manifest = 'graphics/fonts/cjk/manifest.json'\n"
                    "    outputs[index] = json_bytes(decode_index(outputs[index]))\n"
                    "    metadata = json.loads(outputs[manifest])\n"
                    "    metadata['sources']['fehrr_priority']['sha256'] = hashlib.sha256(outputs[index]).hexdigest()\n"
                    "    outputs[manifest] = json_bytes(metadata)\n"
                    "    return outputs\n"
                ).body)
                before = self.repo.commit({path: ast.unparse(tree) + "\n"})
                after = self.repo.commit({path: original}, parent=before)
                members = tuple(
                    item for item in self.tools.members(self.scope(after), (before,))
                    if item.family == "generated"
                )
                observed = self.tools.run_obligations(members, before)
                self.assertEqual(
                    [(item.verdict, item.checks) for item in observed
                     if item.obligation.role == "outputs"],
                    [("contract-violation", 1)],
                )

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
        ] + [
            b'{"value":1,"value":2}', b'{"value":1e999}', b'{"value":NaN}',
            b'{"value":"\\ud800"}', b'{"\\udfff":"value"}', b'{"value":["\\ud800"]}',
        ]
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

    def test_real_surrogate_decoder_mutation_is_not_false_credit(self):
        original = (self.repo.root / "scripts/fonttools/cjk/resource_index.py").read_text()
        broken = original.replace(
            '        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")\n',
            "", 1,
        )
        self.remediation(broken, "wire", "consumers")

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

    def test_unbound_legacy_maintainer_failure_is_not_false_credit(self):
        path = "scripts/fonttools/cjk/package.py"
        original = (self.repo.root / path).read_text()
        guarded = (
            '        priority = manifest["sources"].get("fehrr_priority")\n'
            '        if not isinstance(priority, dict):\n'
            '            raise CjkFontError("cannot repack FEHRR index without a priority provenance binding")\n'
            '        priority["sha256"] = sha256_bytes(data)\n'
        )
        broken = original.replace(
            guarded, '        manifest["sources"]["fehrr_priority"]["sha256"] = sha256_bytes(data)\n', 1,
        )
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
