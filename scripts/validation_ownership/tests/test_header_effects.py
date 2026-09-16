import copy
import hashlib
import json
from pathlib import Path
import shlex
import stat
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.validation_ownership import header_effects
from scripts.validation_ownership.authority import encoded
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.producer_channel import (
    ChannelError, ProducerChannel, publication_identity, validate_publication_confirmation,
)
from scripts.validation_ownership.tests import test_foundation as foundation


class HeaderEffectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.install_fixture()

    def install_fixture(self, target="build/one.headers.d"):
        self.target = target
        self.data = b"# primitive data fixture; no ARM claim\n"
        parent = Path(target).parent.as_posix()
        self.fixture.add("scan.py", (
            "import os\n"
            f"os.makedirs('/work/{parent}',exist_ok=True)\n"
            f"fd=os.open('/work/{target}.tmp',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
            f"with os.fdopen(fd,'wb') as out: out.write({self.data!r})\n"
        ))
        self.fixture.add("filter.py", (
            "import os\nfrom pathlib import Path\n"
            f"data=Path('/repo/{target}.tmp').read_bytes()\n"
            f"Path('/work/{parent}').mkdir(parents=True,exist_ok=True)\n"
            f"fd=os.open('/work/{target}.tmp2',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)\n"
            "with os.fdopen(fd,'wb') as out: out.write(data)\n"
        ))
        self.makefile = (
            ".DEFAULT_GOAL := all\n"
            f"{target}:\n"
            f"\t@mkdir -p {parent}\n"
            "\t@python3 scan.py\n"
            "\t@python3 filter.py\n"
            f"\t@rm -f {target}.tmp\n"
            f"\t@mv -f {target}.tmp2 {target}\n"
            f"include {target}\nall: ;\n"
        )
        self.fixture.add("Makefile", self.makefile)

    def tearDown(self):
        self.fixture.tearDown()

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertFalse(session._header_pipelines)
        self.assertFalse(session._header_commands)
        self.assertFalse(session._issued_header_steps)
        self.assertFalse(session.generated_paths)
        self.assertFalse(session.generated_directories)

    def commands(self, session):
        target = self.target
        class Commands:
            def __contains__(inner, command):
                return True
            def __getitem__(inner, command):
                args = shlex.split(command)
                if args[:2] == ["mkdir", "-p"]:
                    return session._header_step_command(Command(tuple(args)), 1)
                if args == ["python3", "scan.py"]:
                    result = Command(
                        ("/usr/bin/python3", "/repo/scan.py"), code=("scan.py",),
                        outputs=(target + ".tmp",),
                    )
                    return session._header_step_command(result, 2)
                if args == ["python3", "filter.py"]:
                    result = Command(
                        ("/usr/bin/python3", "/repo/filter.py"), code=("filter.py",),
                        sources=(target + ".tmp",), outputs=(target + ".tmp2",),
                    )
                    return session._header_step_command(result, 3)
                if args[:2] == ["rm", "-f"]:
                    return session._header_step_command(Command(tuple(args)), 4)
                if args[:2] == ["mv", "-f"]:
                    return session._header_step_command(Command(tuple(args)), 5)
                raise AssertionError(command)
        return Commands()

    def test_actual_owned_directory_retirement_and_transfer_are_visible(self):
        # The two regular producers isolate the filesystem primitive. They are
        # not an ARM pre-scan/sed implementation or a full pipeline acceptance.
        with self.fixture.session() as session:
            confirmations, physical = [], []
            confirm = session._confirm_header_effect
            def capture(pending, outcome, *args):
                confirmations.append(copy.deepcopy(outcome))
                physical.append({
                    suffix: publication_identity((session.tree / (self.target + suffix)).stat())
                    for suffix in ("", ".tmp", ".tmp2")
                    if (session.tree / (self.target + suffix)).exists()
                })
                return confirm(pending, outcome, *args)
            with patch.object(session, "_confirm_header_effect", capture):
                result = session.make("all", commands=self.commands(session))
            self.assertEqual(len(result.events), 5)
            self.assertEqual([(item.path, item.data, item.mode) for item in result.generated], [
                (self.target, self.data, 0o640),
            ])
            operations = {
                item["filesystem"]["operation"] for item in result.semantics["dynamic_commands"]
                if "filesystem" in item
            }
            self.assertEqual(operations, {"directory", "retire", "transfer"})
            self.assertEqual([item["job"]["command_line"] for item in result.semantics["native_dispatches"]], [1, 2, 3, 4, 5])
            self.assertTrue(all(item["rebuilding_makefiles"] for item in result.semantics["native_dispatches"]))
            directory, retired, transferred = confirmations
            self.assertEqual([row[0] for row in directory["directories"]], ["build"])
            self.assertEqual([set(item) for item in physical], [set(), {".tmp2"}, {""}])
            self.assertEqual(retired["before"][0:5], [
                self.target + ".tmp", retired["before"][1], 0o600,
                len(self.data), hashlib.sha256(self.data).hexdigest(),
            ])
            self.assertEqual(transferred["before"][5], list(physical[1][".tmp2"]))
            self.assertEqual(transferred["after"][5], list(physical[2][""]))
            self.assertEqual(transferred["before"][5][:5], transferred["after"][5][:5])
            self.assertEqual(transferred["before"][5][6], transferred["after"][5][6])
            self.assertFalse((session.tree / "build").exists())
        self.assert_clean(session)

    def test_renamed_nested_family_preserves_existing_parent_and_sources(self):
        self.install_fixture("build/existing/nested/renamed.headers.d")
        self.fixture.add("build/existing/keep.txt", "immutable parent witness\n")
        with self.fixture.session() as session:
            before = (session.tree / "build/existing/keep.txt").stat()
            result = session.make("all", commands=self.commands(session))
            directory, = [
                value["filesystem"] for value in result.semantics["dynamic_commands"]
                if value.get("filesystem", {}).get("operation") == "directory"
            ]
            self.assertEqual(directory["directories"], ["build/existing/nested"])
            self.assertEqual(result.generated[0].path, self.target)
            self.assertEqual((session.tree / "build/existing/keep.txt").stat(), before)
            self.assertTrue((session.tree / "build/existing").is_dir())
            self.assertFalse((session.tree / "build/existing/nested").exists())
        self.assert_clean(session)

    def test_partial_out_of_order_and_wrong_operand_pipelines_reject(self):
        for defect in ("skip-scan", "skip-retire", "wrong-directory", "extra-operand", "partial"):
            source = self.makefile
            if defect == "skip-scan":
                source = source.replace("\t@python3 scan.py\n", "")
            elif defect == "skip-retire":
                source = source.replace(f"\t@rm -f {self.target}.tmp\n", "")
            elif defect == "wrong-directory":
                source = source.replace("mkdir -p build", "mkdir -p build/foreign")
            elif defect == "extra-operand":
                source = source.replace(f"rm -f {self.target}.tmp", f"rm -f {self.target}.tmp Makefile")
            elif defect == "partial":
                source = source.replace(f"\t@mv -f {self.target}.tmp2 {self.target}\n", "")
                source = source.replace(f"include {self.target}", f"-include {self.target}")
            self.fixture.add("Makefile", source)
            with self.subTest(defect=defect), self.fixture.session() as session:
                with self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.commands(session), observe_recipe_dispatch=True)
            self.assert_clean(session)

    def test_nonremake_observation_does_not_issue_or_execute_header_effects(self):
        self.fixture.add("Makefile", self.makefile.replace(
            f"include {self.target}\nall: ;", f"all: {self.target}",
        ))
        with self.fixture.session() as session:
            with patch.object(session, "_header_step_command", wraps=session._header_step_command) as issue:
                result = session.make(
                    "all", commands=self.commands(session), observe_recipe_dispatch=True,
                )
            issue.assert_not_called()
            self.assertEqual(result.generated, ())
            self.assertEqual(len(result.semantics["native_dispatches"]), 5)
            self.assertTrue(all(
                not record["rebuilding_makefiles"]
                for record in result.semantics["native_dispatches"]
            ))
            self.assertFalse(session._header_pipelines)
        self.assert_clean(session)

    def test_equal_copied_changed_and_unissued_commands_do_not_gain_effects(self):
        for defect in ("equal-command", "copied-record", "changed-command", "no-issuance"):
            with self.subTest(defect=defect), self.fixture.session() as session:
                issue = session._header_step_command
                reached = []
                def corrupt(command, step):
                    reached.append(step)
                    if defect == "no-issuance":
                        return command
                    issued = issue(command, step)
                    if defect == "equal-command":
                        return replace(issued)
                    if defect == "copied-record":
                        session._header_commands[id(issued)] = replace(session._header_commands[id(issued)])
                    else:
                        object.__setattr__(issued, "argv", ("mkdir", "-p", "build/foreign"))
                    return issued
                with patch.object(session, "_header_step_command", corrupt):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands=self.commands(session))
                self.assertEqual(reached, [1])
            self.assert_clean(session)
        with self.fixture.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "live"):
                session._header_step_command(Command(("mkdir", "-p", "build")), 1)
        self.assert_clean(session)

    def test_completed_command_cannot_replay_in_another_invocation_or_session(self):
        held = []
        with self.fixture.session() as session:
            issue = session._header_step_command
            def remember(command, step):
                result = issue(command, step)
                if step == 1:
                    held.append(result)
                return result
            with patch.object(session, "_header_step_command", remember):
                session.make("all", commands=self.commands(session))
            self.assertEqual(len(held), 1)
            with patch.object(session, "_header_step_command", return_value=held[0]):
                with self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.commands(session))
        self.assert_clean(session)
        with self.fixture.session() as foreign:
            with patch.object(foreign, "_header_step_command", return_value=held[0]):
                with self.assertRaises(MakeProbeError):
                    foreign.make("all", commands=self.commands(foreign))
        self.assert_clean(foreign)

    def test_native_effect_requests_reject_foreign_stale_and_untyped_operands(self):
        for defect in ("path", "source", "owner", "identity", "digest", "record-type", "operation", "extra"):
            with self.subTest(defect=defect), self.fixture.session() as session:
                send, changed = ProducerChannel.send, []
                def corrupt(channel, payload):
                    value = json.loads(payload)
                    if value.get("kind") == "effect-request" and value["operation"] == "transfer":
                        changed.append(defect)
                        if defect == "path":
                            value["path"] = "Makefile"
                        elif defect == "source":
                            value["source"] = "Makefile"
                        elif defect == "owner":
                            value["expected"][1] = "0" * 64
                        elif defect == "identity":
                            value["expected"][5][1] += 1
                        elif defect == "digest":
                            value["expected"][4] = "0" * 64
                        elif defect == "record-type":
                            value["expected"] = 1
                        elif defect == "operation":
                            value["operation"] = []
                        else:
                            value["extra"] = True
                    return send(channel, encoded(value))
                with patch.object(ProducerChannel, "send", corrupt):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands=self.commands(session))
                self.assertEqual(changed, [defect])
            self.assert_clean(session)

    def test_actual_confirmations_cannot_omit_or_forge_physical_outcomes(self):
        for defect in ("missing", "owner", "identity", "created-parent", "source", "operation"):
            with self.subTest(defect=defect), self.fixture.session() as session:
                receive, changed = ProducerChannel.receive, []
                def corrupt(channel):
                    payload = receive(channel)
                    if payload is None:
                        return None
                    value = json.loads(payload)
                    result = value.get("publication")
                    if result and result.get("kind") == "filesystem" and not changed:
                        changed.append(defect)
                        if defect == "missing":
                            value["publication"] = None
                        elif defect == "owner":
                            result["owner"] = "0" * 64
                        elif defect == "identity":
                            result["directories"][0][2] += 1
                        elif defect == "created-parent":
                            result["directories"] = []
                        elif defect == "source":
                            result["source"] = "Makefile"
                        else:
                            result["operation"] = "transfer"
                    return encoded(value)
                with patch.object(ProducerChannel, "receive", corrupt):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands=self.commands(session))
                self.assertEqual(changed, [defect])
            self.assert_clean(session)

    def test_tracked_target_and_temporary_are_never_owned_by_pipeline(self):
        for suffix in ("", ".tmp", ".tmp2"):
            path = self.target + suffix
            self.fixture.add(path, "# immutable header-family input\n")
            source = self.makefile
            if suffix == "":
                source = source.replace(self.target + ":\n", self.target + ": FORCE\n")
                source += ".PHONY: FORCE\nFORCE:\n"
            self.fixture.add("Makefile", source)
            with self.subTest(path=path), self.fixture.session() as session:
                before = (session.tree / path).stat()
                with self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.commands(session))
                self.assertEqual((session.tree / path).stat(), before)
                self.assertEqual((session.tree / path).read_text(), "# immutable header-family input\n")
            self.assert_clean(session)
            del self.fixture.entries[path]
            (self.fixture.root / path).unlink()

    def test_closed_effect_schema_rejects_type_path_identity_and_operation_mutations(self):
        identity = [1, 2, stat.S_IFREG | 0o640, 3, 4, 5, 1]
        before = [self.target + ".tmp2", "1" * 64, 0o640, 3, "2" * 64, identity]
        effect = header_effects.Effect("transfer", self.target, before[0], tuple(before), "3" * 64)
        self.assertIs(header_effects.validate_effect(effect, self.target), effect)
        confirmation = {
            "kind": "filesystem", "slot": 4, "owner": effect.owner,
            "operation": "transfer", "path": self.target, "source": before[0],
            "before": before, "after": [self.target, effect.owner, *before[2:]],
            "directories": [],
        }
        self.assertIs(validate_publication_confirmation(confirmation, count_limit=5, file_limit=64), confirmation)
        for name, value in (
            ("owner", None), ("operation", []), ("path", True), ("path", "../outside"),
            ("path", "build//alias.headers.d"), ("source", self.target + ".tmp"),
            ("expected", 1), ("expected", None),
        ):
            with self.subTest(effect_field=name, value=value), self.assertRaises(ChannelError):
                header_effects.validate_effect(replace(effect, **{name: value}), self.target)
        for name, value in (
            ("slot", True), ("operation", []), ("path", True), ("owner", []),
            ("before", 1), ("after", {}), ("directories", [None]), ("extra", True),
            ("source", "Makefile"), ("directories", [["build", 1, 2, stat.S_IFREG | 0o755]]),
        ):
            with self.subTest(confirmation_field=name, value=value), self.assertRaises(ChannelError):
                validate_publication_confirmation(
                    {**confirmation, name: value}, count_limit=5, file_limit=64,
                )
        for index, value in ((2, stat.S_IFDIR | 0o640), (3, 4), (6, 2), (1, True)):
            changed = copy.deepcopy(confirmation)
            changed["before"][5][index] = value
            with self.subTest(identity_index=index), self.assertRaises(ChannelError):
                validate_publication_confirmation(changed, count_limit=5, file_limit=64)

    def test_effect_runtime_and_native_module_have_exact_admission_without_neighbor_waiver(self):
        from scripts.validation_ownership import ci_verifier, reporter
        from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
        from scripts.validation_ownership.budget import ProbeBudget

        graph = reporter.load_json(foundation.ROOT / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(foundation.ROOT, budget=budget)
            loader = AuthorityLoader(foundation.ROOT, entries, "HEAD", budget=budget)
            sources = reporter._path_admission_sources(loader, set())
            module = "scripts/validation_ownership/header_effects.py"
            test = "scripts/validation_ownership/tests/test_header_effects.py"
            self.assertIn(module, ci_verifier.TRUSTED_RUNTIME_PATHS)
            for path, rule_id in ((module, "paths.ownership"), (test, "paths.ownership-native")):
                rule, = [
                    rule for rule in graph["path_rules"]
                    if reporter._path_rule_matches(rule, path, set())
                ]
                self.assertEqual(rule["id"], rule_id)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if path == test:
                    without = {**rule, "include": [
                        item for item in rule["include"] if item != {"kind": "exact", "path": path}
                    ]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, without, sources)
            neighbor = "scripts/validation_ownership/tests/test_unregistered_header_effects.py"
            rule, = [
                rule for rule in graph["path_rules"]
                if reporter._path_rule_matches(rule, neighbor, set())
            ]
            with self.assertRaises(reporter.OwnershipError):
                reporter._path_admission(neighbor, rule, sources)
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)

    def test_frozen_case_keeps_human_procedure_and_selected_module_automation(self):
        from scripts import check_docs

        registry, errors = check_docs.parse_test_case_registry(str(foundation.ROOT))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        command = ["python3", "-m", "unittest", __name__, "-v"]
        matches = [item for item in case["automation"] if shlex.split(item["command"]) == command]
        self.assertEqual([item["evidence"] for item in matches], [
            "scripts/validation_ownership/tests/test_header_effects.py",
        ])
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(foundation.ROOT)), [])


if __name__ == "__main__":
    unittest.main()
