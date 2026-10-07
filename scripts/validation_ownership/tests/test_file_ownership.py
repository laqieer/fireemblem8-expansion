"""Real native file-cleanup ownership, terminal retention and removal races."""

import json
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

from scripts.validation_ownership import file_ownership, source_directories
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.budget import Limits, ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession
from scripts.validation_ownership.producer_channel import ChannelError, ProducerChannel
from scripts.validation_ownership.tests import test_source_phases as source_phase_tests


class FileOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.case = source_phase_tests.SourcePhaseTests("runTest")
        self.case.setUp()
        self.fixture = self.case.fixture
        self.fixture.add("build/keep.h", "/* existing fixed parent */\n")
        self.sessions = []
        self.outer_pins = []

    def tearDown(self):
        try:
            for session in self.sessions:
                self.assertFalse(session.budget.children)
                self.assertFalse(session.budget.producer_waiters)
                self.assertFalse(session.parked_capsules)
                session.release_retained_file_handles()
            for descriptor in self.outer_pins:
                os.close(descriptor)
        finally:
            self.case.tearDown()

    def session(self):
        result = self.case.session()
        self.sessions.append(result)
        return result

    @contextmanager
    def scope(self, *, retained=False):
        session = self.session()
        expected = self.assertRaisesRegex(MakeProbeError, "report tree retained") if retained else nullcontext()
        with expected:
            with session:
                yield session
        if retained:
            self.assertIsNotNone(session.base)
            self.assertTrue(any(owner.retained for owner in session._file_owners.values()))

    def options(self, mode):
        return {} if mode is None else {"observe_source_journal": True, "source_journal_mode": mode}

    def pin(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
        self.outer_pins.append(descriptor)
        return descriptor

    def assert_retained(self, session, path):
        self.assertTrue(path.is_file())
        self.assertTrue(session.budget.failed)
        self.assertTrue(session.budget.closed)
        self.assertTrue(any(owner.retained for owner in session._file_owners.values()))
        self.assertFalse(session.budget.children)

    def native_failure(self, *, before=False, displaced=False, mode="fixed-directories", name="src/new.c",
                       unlink_owned=False, forge_identity=False):
        changed = []
        receive, send = ProducerChannel.receive, ProducerChannel.send
        with self.scope(retained=before or displaced or unlink_owned) as session:
            def inject():
                path = session.tree / name
                if before:
                    self.assertIn(name, session.generated_paths)
                    self.assertFalse(path.exists())
                else:
                    owner = session._file_owners[session.tree]
                    pin = next(pin for pin in owner.pins if pin.approved and name in pin.paths)
                    changed.append(("owned-pin", pin))
                    old = self.fixture.directory / ("displaced-" + name.replace("/", "-"))
                    if unlink_owned:
                        path.unlink()
                        self.assertEqual(pin.info().st_nlink, 0)
                    elif displaced:
                        path.rename(old)
                        changed.append(("displaced", old))
                if before or displaced or unlink_owned:
                    path.write_bytes(b"foreign must survive\n")
                    changed.append(("foreign", path))
                    foreign = self.pin(path)
                    changed.append(("foreign-fd", foreign))
                    if not before:
                        self.assertNotEqual(os.fstat(foreign).st_ino, pin.info().st_ino)
                    if forge_identity:
                        pin.identity = file_ownership.object_identity(os.fstat(foreign))
                changed.append(("injected", True))
                session.budget.reject("injected terminal file ownership failure")

            def receiving(channel):
                raw = receive(channel)
                if raw is None:
                    return None
                value = json.loads(raw)
                publication = value.get("publication")
                ready = (
                    value.get("kind") == "journal-barrier" and value["stage"] == "begin"
                    if before else isinstance(publication, dict)
                    and any(item["path"] == name for item in publication.get("outputs", ()))
                )
                if not changed and ready and (not before or mode is not None):
                    inject()
                return raw

            def sending(channel, raw):
                value = json.loads(raw)
                if before and mode is None and not changed and value.get("kind") == "result" and name in value.get("outputs", ()):
                    inject()
                return send(channel, raw)

            with patch.object(ProducerChannel, "receive", receiving), patch.object(ProducerChannel, "send", sending):
                with self.assertRaisesRegex(MakeProbeError, "injected terminal file ownership failure") as failure:
                    session.make("all", commands=self.case.commands(session), **self.options(mode))
            self.assertTrue(changed)
            actual = dict(changed)
            self.assertTrue(actual["injected"])
            if "foreign" in actual:
                self.assertEqual(actual["foreign"].read_bytes(), b"foreign must survive\n")
                self.assertEqual(os.fstat(actual["foreign-fd"]).st_nlink, 1)
                self.assert_retained(session, actual["foreign"])
                self.assertTrue(getattr(failure.exception, "cleanup_errors", ()))
            else:
                self.assertFalse((session.tree / name).exists())
                self.assertFalse(session._file_owners)
            if "displaced" in actual:
                self.assertEqual(actual["displaced"].read_bytes(), b"/* genuine source creation */\n")
                self.assertEqual(actual["displaced"].stat().st_ino, actual["owned-pin"].info().st_ino)
        if "foreign" in actual:
            self.assertTrue(actual["foreign"].is_file(), "broader session cleanup erased retained foreign data")
        else:
            self.fixture.assert_clean(session)
        return actual

    def test_prepublication_foreign_files_survive_all_native_journal_modes(self):
        for mode in (None, "fixed-directories", source_directories.MODE):
            with self.subTest(mode=mode):
                self.native_failure(before=True, mode=mode)

    def test_postinstall_preack_replacement_retains_foreign_and_displaced_owned_files(self):
        self.native_failure(displaced=True)

    def test_unlinked_owned_pin_prevents_inode_reuse_and_foreign_cleanup(self):
        self.native_failure(unlink_owned=True)

    def test_numeric_identity_substitution_cannot_replace_the_actual_descriptor(self):
        self.native_failure(unlink_owned=True, forge_identity=True)

    def test_failed_reports_still_remove_actually_owned_files(self):
        for mode in (None, "fixed-directories", source_directories.MODE):
            with self.subTest(mode=mode):
                self.native_failure(mode=mode)

    def test_healthy_cleanup_removes_actual_outputs_and_closes_ownership(self):
        with self.session() as session:
            result = session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(result.source_journal["closed"])
            self.assertEqual({item.path for item in result.generated}, {"src/new.c", "build/remade.mk"})
            self.assertFalse((session.tree / "src/new.c").exists())
            self.assertFalse((session.tree / "build/remade.mk").exists())
            self.assertFalse(session._file_owners)
        self.fixture.assert_clean(session)

    def removal_race(self, *, obstruct_restore=False, unsafe=False):
        changed = []
        rename = file_ownership.rename_noreplace
        remove = file_ownership.FileOwnership.remove
        with self.scope(retained=True) as session:
            def replace_public():
                path = session.tree / "src/new.c"
                displaced = self.fixture.directory / "displaced-at-removal"
                path.rename(displaced)
                path.write_bytes(b"first foreign\n")
                changed.extend((("public", path), ("displaced", displaced), ("first", self.pin(path))))

            def racing(source, name, destination, target):
                if name == "new.c" and not changed:
                    replace_public()
                elif obstruct_restore and changed and target == "new.c" and name.endswith(".claim"):
                    path = dict(changed)["public"]
                    path.write_bytes(b"second foreign\n")
                    changed.append(("second", self.pin(path)))
                return rename(source, name, destination, target)

            def pathname_remove(owner, name, pin):
                if name != "src/new.c":
                    return remove(owner, name, pin)
                path = owner.tree / name
                self.assertEqual(file_ownership.object_identity(path.stat()), pin.identity)
                if name == "src/new.c" and not changed:
                    replace_public()
                path.unlink()
                return pin

            with patch.object(file_ownership, "rename_noreplace", racing):
                if unsafe:
                    with patch.object(file_ownership.FileOwnership, "remove", pathname_remove):
                        with self.assertRaises(MakeProbeError):
                            session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "retained uncertain ownership"):
                        session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(changed)
            actual = dict(changed)
            self.assertEqual(actual["displaced"].read_bytes(), b"/* genuine source creation */\n")
            if unsafe:
                self.assertFalse(actual["public"].exists())
                self.assertEqual(os.fstat(actual["first"]).st_nlink, 0)
            elif obstruct_restore:
                self.assertEqual(actual["public"].read_bytes(), b"second foreign\n")
                self.assertEqual(os.fstat(actual["first"]).st_nlink, 1)
                claims = [path for owner in session._file_owners.values()
                          for path in owner.root.glob("*.claim")]
                self.assertEqual([path.read_bytes() for path in claims], [b"first foreign\n"])
            else:
                self.assertEqual(actual["public"].read_bytes(), b"first foreign\n")
                self.assertEqual(os.fstat(actual["first"]).st_nlink, 1)

    def test_atomic_claim_rejects_replacement_at_the_removal_boundary(self):
        self.removal_race()

    def test_failed_claim_restore_never_overwrites_another_public_occupant(self):
        self.removal_race(obstruct_restore=True)

    def test_stat_then_unlink_mutation_recovers_the_actual_foreign_deletion(self):
        self.removal_race(unsafe=True)

    def test_reservation_only_cleanup_mutation_recovers_the_prepublication_deletion(self):
        def old_cleanup(owner, journal=None):
            for name in owner.session.generated_paths:
                (owner.tree / name).unlink(missing_ok=True)
            owner.release_handles()
            if owner.root is not None:
                owner.root.rmdir()
        with patch.object(file_ownership.FileOwnership, "cleanup", old_cleanup):
            with self.assertRaises(FileNotFoundError):
                self.native_failure(before=True)

    def test_lost_descriptor_ack_retains_actual_unapproved_pin_and_objects(self):
        changed = []
        receive = ProducerChannel.receive
        with self.scope(retained=True) as session:
            def receiving(channel):
                raw = receive(channel)
                if raw is None:
                    return None
                value = json.loads(raw)
                if value.get("kind") == "file-opened" and value["path"] == "src/new.c" and not changed:
                    owner = session._file_owners[session.tree]
                    pin = next(pin for pin in owner.pins if not pin.approved)
                    path = session.tree / "src/new.c"
                    displaced = self.fixture.directory / "unacknowledged-owned"
                    path.rename(displaced)
                    path.write_bytes(b"foreign after descriptor receipt\n")
                    changed.append((pin, path, displaced))
                    session.budget.reject("injected failure before descriptor acknowledgement")
                return raw
            with patch.object(ProducerChannel, "receive", receiving), self.assertRaises(MakeProbeError):
                session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(changed)
            pin, path, displaced = changed[0]
            self.assertFalse(pin.approved)
            self.assertFalse(pin.handle.closed)
            self.assertEqual(displaced.stat().st_ino, pin.info().st_ino)
            self.assertEqual(path.read_bytes(), b"foreign after descriptor receipt\n")
            self.assert_retained(session, path)
        self.assertTrue(path.is_file())

    def test_native_descriptor_bindings_reject_corruption_without_deleting_objects(self):
        receive = ProducerChannel.receive
        for defect in ("scope", "owner", "path", "identity", "parent"):
            with self.subTest(defect=defect), self.scope(retained=True) as session:
                changed = []
                def receiving(channel):
                    raw = receive(channel)
                    if raw is None:
                        return None
                    value = json.loads(raw)
                    if value.get("kind") == "file-opened" and value["path"] == "src/new.c" and not changed:
                        changed.append(True)
                        if defect == "scope":
                            value["scope"] += "-foreign"
                        elif defect == "owner":
                            value["owner"] = "0" * 64
                        elif defect == "path":
                            value["path"] = "src/unissued.c"
                        elif defect == "identity":
                            value["identity"][1] += 1
                        else:
                            value["parent"][1] += 1
                        return json.dumps(value).encode()
                    return raw
                with patch.object(ProducerChannel, "receive", receiving), self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                self.assertTrue(changed)
                self.assertTrue((session.tree / "src/new.c").is_file())
                self.assertTrue(any(not pin.approved and not pin.handle.closed
                                    for owner in session._file_owners.values() for pin in owner.pins))

    def test_selected_view_retention_survives_restoration_and_outer_exit(self):
        budget = ProbeBudget(Limits(seconds=30))
        base = self.fixture.capture_view(budget)
        self.fixture.add("current-only.txt", "current")
        current = self.fixture.capture_view(budget)
        session = ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget,
                               runtime_files=("/usr/include/build",))
        self.sessions.append(session)
        changed = []
        receive = ProducerChannel.receive
        with self.assertRaisesRegex(MakeProbeError, "report tree retained"):
            with session:
                with self.assertRaisesRegex(MakeProbeError, "report tree retained"):
                    with session.select_view(base):
                        def receiving(channel):
                            raw = receive(channel)
                            if raw is None:
                                return None
                            value = json.loads(raw)
                            if value.get("kind") == "journal-barrier" and value["stage"] == "begin" and not changed:
                                path = session.tree / "src/new.c"
                                path.write_bytes(b"foreign selected-view object")
                                changed.append(path)
                                session.budget.reject("injected selected-view terminal failure")
                            return raw
                        with patch.object(ProducerChannel, "receive", receiving):
                            with self.assertRaisesRegex(MakeProbeError, "injected selected-view"):
                                session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                self.assertIs(session.loader, current)
                self.assertTrue(changed)
                self.assertEqual(changed[0].read_bytes(), b"foreign selected-view object")
        self.assertEqual(changed[0].read_bytes(), b"foreign selected-view object")

    def test_lost_retire_and_transfer_acknowledgements_keep_actual_file_ownership(self):
        from scripts.validation_ownership.tests import test_header_effects

        receive = ProducerChannel.receive
        for operation in ("retire", "transfer"):
            with self.subTest(operation=operation):
                case = test_header_effects.HeaderEffectTests("runTest")
                case.setUp()
                session = case.fixture.session()
                changed = []
                try:
                    with self.assertRaisesRegex(MakeProbeError, "report tree retained"):
                        with session:
                            def receiving(channel):
                                raw = receive(channel)
                                if raw is None:
                                    return None
                                value = json.loads(raw)
                                publication = value.get("publication")
                                if (
                                    isinstance(publication, dict) and publication.get("kind") == "filesystem"
                                    and publication["operation"] == operation and not changed
                                ):
                                    path = session.tree / publication["path"]
                                    if operation == "transfer":
                                        displaced = case.fixture.directory / "displaced-transferred-file"
                                        path.rename(displaced)
                                        changed.append(("displaced", displaced))
                                    else:
                                        self.assertFalse(path.exists())
                                    path.write_bytes(b"foreign header occupant")
                                    changed.append(("foreign", path))
                                    session.budget.reject("injected lost " + operation + " acknowledgement")
                                return raw
                            with patch.object(ProducerChannel, "receive", receiving):
                                with self.assertRaisesRegex(MakeProbeError, "injected lost"):
                                    session.make("all", commands=case.commands(session))
                            self.assertTrue(changed)
                            actual = dict(changed)
                            self.assertEqual(actual["foreign"].read_bytes(), b"foreign header occupant")
                            if operation == "transfer":
                                self.assertEqual(actual["displaced"].read_bytes(), case.data)
                    self.assertTrue(actual["foreign"].is_file())
                    self.assertFalse(session.budget.children)
                    self.assertFalse(session.budget.producer_waiters)
                finally:
                    session.release_retained_file_handles()
                    case.tearDown()

    def test_parent_remap_during_claim_restores_into_the_pinned_original_parent(self):
        rename = file_ownership.rename_noreplace
        changed = []
        with self.scope(retained=True) as session:
            def racing(source, name, destination, target):
                if name == "new.c" and not changed:
                    original = session.tree / "src"
                    displaced = self.fixture.directory / "displaced-source-parent"
                    original.rename(displaced)
                    original.mkdir()
                    (original / "new.c").write_bytes(b"foreign parent occupant")
                    changed.append((original, displaced))
                return rename(source, name, destination, target)
            with patch.object(file_ownership, "rename_noreplace", racing), self.assertRaises(MakeProbeError):
                session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(changed)
            original, displaced = changed[0]
            self.assertEqual((original / "new.c").read_bytes(), b"foreign parent occupant")
            self.assertEqual((displaced / "new.c").read_bytes(), b"/* genuine source creation */\n")
        self.assertTrue((displaced / "new.c").is_file())

    def test_received_descriptor_remains_owned_if_its_first_byte_charge_fails(self):
        changed = []
        with self.scope(retained=True) as session:
            charge = session.budget.charge
            def fail_after_fd(category, size):
                pending = [pin for owner in session._file_owners.values() for pin in owner.pins
                           if not pin.approved and not pin.handle.closed]
                if category == "control" and pending and not changed:
                    changed.append(pending[0])
                    session.budget.reject("injected byte-accounting failure after actual fd receipt")
                return charge(category, size)
            with patch.object(session.budget, "charge", fail_after_fd):
                with self.assertRaisesRegex(MakeProbeError, "injected byte-accounting"):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(changed)
            self.assertFalse(changed[0].handle.closed)
            self.assertFalse(changed[0].approved)
            self.assertEqual(changed[0].info().st_nlink, 1)

    def test_lost_reply_after_pin_approval_still_cleans_the_known_owned_partial_file(self):
        send = ProducerChannel.send
        changed = []
        with self.scope() as session:
            def lose_reply(channel, raw):
                value = json.loads(raw)
                if value.get("kind") == "file-pinned" and not changed:
                    owner = session._file_owners[session.tree]
                    pin = next(pin for pin in owner.pins if pin.approved and value["path"] in pin.paths)
                    changed.append((value["path"], pin))
                    session.budget.reject("injected loss of approved file-pin acknowledgement")
                return send(channel, raw)
            with patch.object(ProducerChannel, "send", lose_reply):
                with self.assertRaisesRegex(MakeProbeError, "injected loss of approved"):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertTrue(changed)
            name, pin = changed[0]
            self.assertFalse((session.tree / name).exists())
            self.assertTrue(pin.handle.closed)
            self.assertTrue(pin.removed)
            self.assertFalse(session._file_owners)
        self.fixture.assert_clean(session)

    def test_file_registration_configuration_is_closed_and_has_no_boolean_version(self):
        config = {"mode": "make", "producer_scope": "owned/scope"}
        value = {"version": 1, "scope": "owned/scope"}
        file_ownership.validate_config(value, config)
        for wrong in (
            {"version": True, "scope": "owned/scope"},
            {"version": 1, "scope": None},
            {"version": 1, "scope": "foreign"},
            {**value, "extra": 0},
        ):
            with self.subTest(value=wrong), self.assertRaises(ChannelError):
                file_ownership.validate_config(wrong, config)
        with self.assertRaises(ChannelError):
            file_ownership.validate_config(value, {**config, "mode": "command"})

    def test_exact_runtime_native_owner_and_existing_case_are_wired(self):
        from scripts import check_docs
        from scripts.validation_ownership import ci_verifier, reporter

        root = source_phase_tests.foundation.ROOT
        graph = reporter.load_json(root / reporter.GRAPH_PATH)
        admissions = {key: set() for key in (
            "initial-graph-cohort", "generated-source-registry", "verifier-runtime-registry",
        )}
        for path, rule_id in (
            ("scripts/validation_ownership/file_ownership.py", "paths.ownership"),
            ("scripts/validation_ownership/tests/test_file_ownership.py", "paths.ownership-native"),
        ):
            rule, = [row for row in graph["path_rules"] if reporter._path_rule_matches(row, path, set())]
            self.assertEqual(rule["id"], rule_id)
            self.assertEqual(reporter._path_admission(path, rule, admissions), "exact-ownership-rule")
            removed = {**rule, "include": [item for item in rule["include"]
                                           if item != {"kind": "exact", "path": path}]}
            with self.assertRaises(reporter.OwnershipError):
                reporter._path_admission(path, removed, admissions)
        self.assertIn("scripts/validation_ownership/file_ownership.py", ci_verifier.TRUSTED_RUNTIME_PATHS)
        recipe = (root / "scripts/validation_ownership/foundation.mk").read_text().split("ownership-probe-test:\n", 1)[1]
        self.assertEqual(shlex.split(recipe).count(__name__), 1)
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_file_ownership.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


if __name__ == "__main__":
    unittest.main()
