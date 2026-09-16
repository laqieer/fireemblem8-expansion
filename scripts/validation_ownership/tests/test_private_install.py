import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.validation_ownership import private_install
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession
from scripts.validation_ownership.python_commands import python_command
from scripts.validation_ownership.tests import test_foundation as foundation


ROOT = Path(__file__).resolve().parents[3]


class PrivateInstallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        self.assertEqual((ROOT / witness).read_bytes(), b"")
        self.fixture.add(witness, b"")

    def tearDown(self):
        self.fixture.tearDown()

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertFalse(session._private_install_commands)
        self.assertFalse(session._private_install_launches)
        self.assertFalse(session._private_install_issued)
        self.assertFalse(session._private_install_launch_issued)

    def command(self, session, body, *, destinations=("out/result",), outputs=("out/result",), issued=True):
        command = python_command(session, body, outputs=outputs)
        return session._private_install_command(command, destinations) if issued else command

    def test_real_text_installs_have_exact_native_outcomes_and_default_denial(self):
        for name in ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"):
            self.fixture.add(name, (ROOT / name).read_bytes())
        self.fixture.add("text.txt", "#0001\n[X][Y][X]\n#0002\n[Y][X][Y]\n")
        self.fixture.add("defs.txt", "[X] = 0\n[Y] = 1\n")
        body = (
            "sys.path.insert(0,'/repo/scripts/texttools');import textprocess;"
            "sys.argv[0]='scripts/texttools/textprocess.py';"
            "textprocess.main(['/repo/text.txt','/repo/defs.txt','/work/out/data.c','/work/out/header.h','utf8'])"
        )
        outputs = ("out/data.c", "out/header.h")
        with self.fixture.session() as session:
            command = python_command(
                session, body, sources=("defs.txt", "text.txt"), outputs=outputs,
                code=("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"),
            )
            session._private_install_command(command, outputs)
            recorded = []
            sandbox = session._sandbox_run

            def capture(root, **options):
                result, observed = sandbox(root, **options)
                recorded.extend(observed["private_install_records"])
                return result, observed

            with patch.object(session, "_sandbox_run", capture):
                result = session.command(command)
            generated = {item.path: item for item in result.generated}
            self.assertEqual(set(generated), set(outputs))
            self.assertTrue(generated["out/data.c"].data.startswith(b'#include "global.h"'))
            self.assertIn(b"#define MSG_001 0x0001", generated["out/header.h"].data)
            self.assertEqual(set(result.consumed), {"defs.txt", "text.txt"})
            self.assertEqual(set(result.code_consumed),
                             {"scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"})
            self.assertEqual([item.sequence for item in recorded], [1, 2])
            self.assertEqual([item.destination for item in recorded], ["/work/out/header.h", "/work/out/data.c"])
            self.assertTrue(all(item.result == 0 and item.identity[6] == 1 for item in recorded))
            self.assertFalse(session._private_install_launches)
        self.assert_clean(session)
        with self.fixture.session() as denied:
            command = python_command(
                denied, "import os;" + "os.mkdir('/work/out');" + body,
                sources=("defs.txt", "text.txt"), outputs=outputs,
                code=("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"),
            )
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                denied.command(command)
        self.assert_clean(denied)

    def test_private_install_uses_actual_absolute_relative_and_dirfd_lookups(self):
        prefix = "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
        for action in (
            "os.replace('/work/out/temp','/work/out/result')\n",
            "os.chdir('/work/out');os.replace('temp','result')\n",
            "directory=os.open('/work/out',os.O_RDONLY|os.O_DIRECTORY)\n"
            "try: os.replace('temp','result',src_dir_fd=directory,dst_dir_fd=directory)\n"
            "finally: os.close(directory)\n",
        ):
            with self.subTest(action=action):
                with self.fixture.session() as session:
                    result = session.command(self.command(session, prefix + action))
                    self.assertEqual([(item.path, item.data) for item in result.generated], [("out/result", b"actual")])
                self.assert_clean(session)

    def test_private_install_rejects_path_type_alias_actor_and_mapping_changes(self):
        prefix = "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
        attempts = (
            ("existing", prefix + "open('/work/out/result','wb').close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("directory", "import os\nos.mkdir('/work/out/temp')\nos.replace('/work/out/temp','/work/out/result')"),
            ("hardlink", prefix + "os.link('/work/out/temp','/work/out/alias')\nos.replace('/work/out/temp','/work/out/result')"),
            ("open-fd", prefix + "descriptor=os.open('/work/out/temp',os.O_RDONLY)\nos.replace('/work/out/temp','/work/out/result')"),
            ("duplicated-fd", prefix + "descriptor=os.open('/work/out/temp',os.O_RDONLY)\n"
             "alias=os.dup(descriptor);os.close(descriptor)\nos.replace('/work/out/temp','/work/out/result')"),
            ("cross-parent", prefix + "os.replace('/work/out/temp','/work/result')"),
            ("unlisted", prefix + "os.replace('/work/out/temp','/work/out/other')"),
            ("outside-work", prefix + "os.replace('/work/out/temp','/repo/result')"),
            ("parent-spelling", prefix + "os.replace('/work/out/../out/temp','/work/out/result')"),
            ("symlink", prefix + "os.symlink('temp','/work/out/alias')\nos.replace('/work/out/alias','/work/out/result')"),
            ("changed-parent", "import os\nos.rmdir('/work/out');os.mkdir('/work/out')\n"
             "open('/work/out/temp','wb').close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("fork", prefix + "os.fork()\nos.replace('/work/out/temp','/work/out/result')"),
            ("repeated", prefix + "os.replace('/work/out/temp','/work/out/result')\n"
             "os.unlink('/work/out/result');open('/work/out/temp','wb').close()\n"
             "os.replace('/work/out/temp','/work/out/result')"),
            ("mapped", prefix + "import mmap\nsource=open('/work/out/temp','rb')\n"
             "mapping=mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ)\n"
             "source.close()\nos.replace('/work/out/temp','/work/out/result')"),
            ("flags", prefix + "import ctypes\nlibc=ctypes.CDLL(None)\n"
             "libc.renameat2(-100,b'/work/out/temp',-100,b'/work/out/result',1)\n"),
        )
        for name, program in attempts:
            with self.subTest(name=name):
                with self.fixture.session() as session:
                    with self.assertRaises(MakeProbeError):
                        session.command(self.command(session, program))
                self.assert_clean(session)

    def test_equal_cloned_foreign_mutated_and_expired_commands_do_not_inherit_grants(self):
        body = (
            "import os\nos.makedirs('/work/out',exist_ok=True)\n"
            "with open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        with self.fixture.session() as session:
            issued = self.command(session, body)
            clone = replace(issued)
            self.assertEqual(clone, issued)
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                session.command(clone)
        self.assert_clean(session)
        with self.fixture.session() as foreign:
            with self.assertRaisesRegex(MakeProbeError, "directory-entry relocation"):
                foreign.command(issued)
        self.assert_clean(foreign)
        with self.fixture.session() as modified:
            command = self.command(modified, body)
            object.__setattr__(command, "argv", (*command.argv[:-1], command.argv[-1] + "\n# changed"))
            with self.assertRaisesRegex(MakeProbeError, "changed|issued"):
                modified.command(command)
        self.assert_clean(modified)
        with self.fixture.session() as forged:
            command = self.command(forged, body)
            record = forged._private_install_commands[id(command)]
            forged._private_install_commands[id(command)] = replace(record)
            with self.assertRaisesRegex(MakeProbeError, "issued"):
                forged.command(command)
        self.assert_clean(forged)
        with self.fixture.session() as expired:
            command = self.command(expired, body)
        self.assert_clean(expired)
        with self.assertRaisesRegex(MakeProbeError, "not active"):
            expired.command(command)

    def test_private_install_cannot_cross_actual_views_or_replay_a_launch(self):
        body = (
            "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        budget = ProbeBudget()
        base = self.fixture.capture_view(budget)
        self.fixture.add("other.txt", "different selected view\n")
        current = self.fixture.capture_view(budget)
        with ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget) as session:
            first = self.command(session, body)
            with session.select_view(base):
                with self.assertRaisesRegex(MakeProbeError, "issued view"):
                    session._require_private_install(first)
            with self.assertRaisesRegex(MakeProbeError, "issued view"):
                session._require_private_install(first)
            fresh = self.command(session, body)
            sandbox = session._sandbox_run
            replays = []

            def attempted_replay(root, **options):
                result = sandbox(root, **options)
                before = budget.runs
                with self.assertRaisesRegex(MakeProbeError, "forged|consumed"):
                    sandbox(root, **options)
                self.assertEqual(budget.runs, before)
                replays.append(True)
                return result

            with patch.object(session, "_sandbox_run", attempted_replay):
                result = session.command(fresh)
            self.assertEqual(result.generated[0].data, b"actual")
            self.assertEqual(replays, [True])
        self.assert_clean(session)

    def test_forged_launch_and_missing_native_install_outcomes_reject(self):
        body = (
            "import os\nwith open('/work/out/temp','wb') as target: target.write(b'actual')\n"
            "os.replace('/work/out/temp','/work/out/result')\n"
        )
        with self.fixture.session() as session:
            command = self.command(session, body)
            with patch.object(session, "_private_install_launch", return_value={"private_install": True}):
                with self.assertRaisesRegex(MakeProbeError, "forged|expired"):
                    session.command(command)
        self.assert_clean(session)
        with self.fixture.session() as missing:
            command = self.command(missing, body)
            read = missing.budget.read_bytes

            def omit(path, category):
                data = read(path, category)
                if category == "control" and Path(path).name.startswith("report-"):
                    report = json.loads(data)
                    report["accessed"] = [item for item in report["accessed"] if not item.startswith(private_install.PREFIX)]
                    return json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
                return data

            with patch.object(missing.budget, "read_bytes", omit):
                with self.assertRaisesRegex(MakeProbeError, "incomplete|outcomes"):
                    missing.command(command)
        self.assert_clean(missing)
        with self.fixture.session() as absent:
            command = self.command(absent, "open('/work/out/result','wb').write(b'not installed')")
            with self.assertRaisesRegex(MakeProbeError, "omitted|incomplete"):
                absent.command(command)
        self.assert_clean(absent)

    def test_private_install_protocol_rejects_unbound_and_malformed_data(self):
        config = {
            "mode": "command", "root": "/owned/session/command-root-1",
            "argv": ["/usr/bin/python3", "-c", "pass"], "code": [], "sources": [],
            "enumerations": [], "environment": {}, "observation_count": 100, "creation_limit": 100,
        }
        value = {
            "version": 1, "scope": private_install.launch_scope(config["root"]),
            "binding": private_install.launch_binding(config),
            "parents": [["/work", 1, 2, 0o40700]], "destinations": ["/work/result"],
        }
        spec = private_install.validate_config(value, config)
        record = {
            "version": 1, "scope": spec.scope, "sequence": 1,
            "source": "/work/temp", "destination": "/work/result", "result": 0,
            "identity": [1, 3, 0o100644, 4, 5, 6, 1],
        }
        encoded = private_install.PREFIX + json.dumps(record)
        self.assertEqual(private_install.validate_records([encoded], spec)[0].identity, tuple(record["identity"]))
        for key, wrong in (
            ("version", True), ("scope", "other/root"), ("binding", "0" * 64),
            ("parents", [["/work", 1, 2, 0o100644]]),
            ("destinations", ["/repo/result"]), ("destinations", ["/work/result", "/work/result"]),
        ):
            with self.subTest(key=key, wrong=wrong):
                changed = {**value, key: wrong}
                with self.assertRaises(private_install.InstallError):
                    private_install.validate_config(changed, config)
        for changed in (
            {**record, "scope": "other/root"}, {**record, "sequence": True},
            {**record, "sequence": 2}, {**record, "result": -2},
            {**record, "destination": "/work/other"}, {**record, "extra": True},
            {**record, "source": "/work/result"},
            {**record, "identity": [1, 3, 0o40700, 4, 5, 6, 1]},
            {**record, "identity": [1, 3, 0o100644, 4, 5, 6, 2]},
        ):
            with self.subTest(record=changed):
                with self.assertRaises(private_install.InstallError):
                    private_install.validate_records([private_install.PREFIX + json.dumps(changed)], spec)
        for records in ([], [encoded, encoded]):
            with self.assertRaises(private_install.InstallError):
                private_install.validate_records(records, spec)
        with self.assertRaises(private_install.InstallError):
            private_install.validate_records([encoded], None)
        with self.assertRaises(private_install.InstallError):
            private_install.validate_records([encoded[:-1] + ',"result":0}'], spec)


if __name__ == "__main__":
    unittest.main()
