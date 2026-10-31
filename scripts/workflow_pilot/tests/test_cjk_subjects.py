"""Source-backed codec obligations, not whole-suite pass-label adapters."""

import shutil
import subprocess
import unittest

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
                    or name.startswith(("fonts/cjk/", "graphics/fonts/cjk/"))):
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

    def remediation(self, broken, family, role, path="scripts/fonttools/cjk/resource_index.py"):
        original = (self.repo.root / path).read_text()
        self.assertNotEqual(original, broken)
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: original}, parent=before)
        members = self.tools.members(self.scope(after), (before,))
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
        self.remediation(broken, "generated", "outputs", path)
