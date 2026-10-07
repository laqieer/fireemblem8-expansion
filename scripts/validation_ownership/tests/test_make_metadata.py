import copy
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import unittest
from unittest import mock
import os

from scripts.validation_ownership import reporter
from scripts.validation_ownership import budget as budget_module
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, git_tree_entries
from scripts.validation_ownership.budget import ProbeBudget, MakeProbeError


ROOT = Path(__file__).resolve().parents[3]


class MakeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/make-metadata" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.budget = ProbeBudget()
        self.write("scripts/producer.py", "def value():\n    return 1\n")
        self.write("source.json", '{"value":1}\n')
        self.data = {
            "schema_version": 1,
            "contracts": [
                {"id": name, "expression": "$(shell " + name + ")", "tool": "scripts/producer.py",
                 "input_files": ["source.json"], "input_variables": [], "automatic_inputs": [],
                 "resolved_value": None, "owning_evidence_ids": ["owner"]}
                for name in ("first", "second", "third")
            ], "seal": "",
        }
        self.write_registry()
        self.git("init", "--quiet")

    def tearDown(self):
        self.budget.close()
        shutil.rmtree(self.directory)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_registry(self):
        self.data["seal"] = reporter._sha256(
            reporter.MAKE_DYNAMIC_SEAL_DOMAIN, reporter.canonical_make_dynamic_payload(self.data),
        )
        self.write(reporter.MAKE_DYNAMIC_PATH, json.dumps(self.data))

    def git(self, *args):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), *args],
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        ).stdout

    def loader(self):
        self.git("add", "-A")
        revision = self.git("write-tree").decode().strip()
        return AuthorityLoader(
            self.root, git_tree_entries(self.root, revision, budget=self.budget),
            revision, budget=self.budget,
        )

    def test_validated_metadata_reuses_exact_inputs_and_preserves_all_contracts(self):
        loader = self.loader()
        metadata = reporter._make_metadata(loader, required=True)
        self.assertEqual(len(metadata.contracts), 3)
        self.assertEqual(set(metadata.blobs), {"scripts/producer.py", "source.json"})
        before = self.budget.runs
        self.assertEqual(reporter.load_make_ambient_contracts(loader, required=True, _metadata=metadata), {})
        self.assertEqual(reporter.load_make_prerequisite_domains(loader, required=True, _metadata=metadata), {})
        self.assertEqual(reporter.load_make_typed_variable_contracts(loader, required=True, _metadata=metadata), ({}, {}, {}))
        self.assertEqual(reporter.load_make_generated_prerequisite_paths(loader, required=True, _metadata=metadata), {})
        self.assertEqual(reporter.load_make_symbolic_recipe_names(loader, required=True, _metadata=metadata), set())
        self.assertEqual(self.budget.runs, before)
        self.assertEqual(metadata.contracts, reporter.load_make_dynamic_contracts(loader, required=True))
        self.assertGreater(self.budget.bytes["cache"], 0)

    def test_metadata_cannot_cross_selected_views(self):
        current = self.loader()
        metadata = reporter._make_metadata(current, required=True)
        self.write("source.json", '{"value":2}\n')
        other = self.loader()
        with self.assertRaisesRegex(MakeProbeError, "another selected"):
            reporter.load_make_ambient_contracts(other, required=True, _metadata=metadata)
        changed = reporter._make_metadata(other, required=True)
        self.assertNotEqual(changed.contracts, metadata.contracts)

    def test_invalid_seal_and_missing_declared_input_still_fail_closed(self):
        valid = self.loader()
        metadata = reporter._make_metadata(valid, required=True)
        self.data["contracts"][0]["input_files"].append("missing.json")
        self.write_registry()
        with self.assertRaises(MakeProbeError):
            reporter._make_metadata(self.loader(), required=True)
        self.data = copy.deepcopy(metadata.data)
        self.data["seal"] = "0" * 64
        self.write(reporter.MAKE_DYNAMIC_PATH, json.dumps(self.data))
        with self.assertRaisesRegex(MakeProbeError, "seal does not match"):
            reporter._make_metadata(self.loader(), required=True)

    def test_candidate_command_pattern_compilation_uses_bounded_worker(self):
        self.data = json.loads((ROOT / reporter.MAKE_DYNAMIC_PATH).read_bytes())
        for contract in self.data["contracts"]:
            contract["tool"] = "scripts/producer.py"
            contract["input_files"] = ["source.json"]
        self.data["contracts"][0]["command_regex"] = "^[$"
        self.write_registry()
        loader = self.loader()
        read, chunks = os.read, []
        def capture(*args):
            result = read(*args)
            chunks.append(result)
            return result
        with mock.patch.object(budget_module.os, "read", side_effect=capture), \
             self.assertRaisesRegex(MakeProbeError, "compile validation failed"):
            reporter._make_metadata(loader, required=True)
        self.assertIn(b'"phase":"compile-start"', b"".join(chunks))
        self.assertFalse(self.budget.children)


if __name__ == "__main__":
    unittest.main()
