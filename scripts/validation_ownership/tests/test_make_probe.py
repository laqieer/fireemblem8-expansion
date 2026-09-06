from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.validation_ownership import make_probe, reporter, sandbox_exec


ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = ROOT / "build" / "test-artifacts" / "validation-ownership"
PROBE_INPUTS = (
    "scripts/validation_ownership/generated_registry_probe.py",
    "scripts/validation_ownership/sandbox_exec.py",
    "scripts/validation_ownership/shell_interceptor.c",
)


class AuthoritativeMakeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = reporter.prepare_validation_scratch(ROOT)

    @classmethod
    def tearDownClass(cls):
        reporter.cleanup_validation_scratch(cls.scratch)

    def test_candidate_generated_registry_is_confined_typed_authority(self):
        registry = (
            "import os\n"
            "from pathlib import Path\n"
            "assert 'GITHUB_TOKEN' not in os.environ\n"
            "try:\n"
            "    Path('/repo/forged').write_text('forged')\n"
            "except OSError:\n"
            "    pass\n"
            "else:\n"
            "    raise RuntimeError('candidate tree was writable')\n"
            "class Schema:\n"
            "    version = 1\n"
            "    default_source = 'src/data/new_table.json'\n"
            "    default_hand_source = None\n"
            "    default_output_name = 'data_new_table.c'\n"
            "    default_inventory_path = None\n"
            "    def dependencies(self): return ()\n"
            "    def dependency_tables(self): return ()\n"
            "class Registry:\n"
            "    def all_names(self): return ('new-table',)\n"
            "    def resolve(self, name):\n"
            "        assert name == 'new-table'\n"
            "        return Schema()\n"
            "REGISTRY = Registry()\n"
        )
        directory, root, entries = self.fixture(
            "all:\n\t@true\n",
            {
                "scripts/__init__.py": "",
                "scripts/generated_data/__init__.py": "",
                "scripts/generated_data/registry.py": registry,
                "src/data/new_table.json": "{}\n",
            },
        )
        with directory:
            records, paths = reporter._generated_registry_records(
                reporter.AuthorityLoader(root, entries)
            )
            self.assertEqual(records[0]["name"], "new-table")
            self.assertEqual(
                records[0]["default_source"],
                "src/data/new_table.json",
            )
            self.assertEqual(paths, {"src/data/new_table.json"})
            self.assertFalse((root / "forged").exists())

    def fixture(self, makefile: str, files: dict[str, str] | None = None):
        directory = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        root = Path(directory.name)
        (root / "Makefile").write_text(makefile, encoding="ascii")
        entries = {
            "Makefile": reporter.GitTreeEntry(
                "Makefile",
                "100644",
                "blob",
                "0" * 40,
            )
        }
        for path in PROBE_INPUTS:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
            entries[path] = reporter.GitTreeEntry(
                path,
                "100644",
                "blob",
                "0" * 40,
            )
        for path, content in ({} if files is None else files).items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="ascii")
            entries[path] = reporter.GitTreeEntry(
                path,
                "100644",
                "blob",
                "0" * 40,
            )
        return directory, root, entries

    def test_live_probe_rejects_every_scratch_symlink_before_writes(self):
        parts = ("build", "test-artifacts", "validation-ownership")
        for symlink_component in parts:
            with self.subTest(component=symlink_component):
                with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
                    base = Path(directory)
                    root = base / "repo"
                    outside = base / "outside"
                    root.mkdir()
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sentinel.write_text("preserve\n", encoding="ascii")
                    (root / "Makefile").write_text(
                        "all:\n\t@true\n",
                        encoding="ascii",
                    )
                    entries = {
                        "Makefile": reporter.GitTreeEntry(
                            "Makefile",
                            "100644",
                            "blob",
                            "0" * 40,
                        )
                    }
                    current = root
                    for part in parts:
                        target = current / part
                        if part == symlink_component:
                            target.symlink_to(outside, target_is_directory=True)
                            if part == "build":
                                entries["build"] = reporter.GitTreeEntry(
                                    "build",
                                    "120000",
                                    "blob",
                                    "0" * 40,
                                )
                            break
                        target.mkdir()
                        current = target

                    with self.assertRaisesRegex(
                        make_probe.MakeProbeError,
                        "tracked Git object|non-symlink directory",
                    ):
                        make_probe.run_probe(
                            reporter.AuthorityLoader(root, entries),
                            {"all"},
                            {},
                            {},
                            scratch_root=(
                                root
                                / "build"
                                / "test-artifacts"
                                / "validation-ownership"
                            ),
                        )
                    self.assertEqual(
                        sentinel.read_text(encoding="ascii"),
                        "preserve\n",
                    )
                    self.assertEqual(
                        {item.name for item in outside.iterdir()},
                        {"sentinel"},
                    )

    def probe(
        self,
        root: Path,
        entries: dict[str, reporter.GitTreeEntry],
        *,
        domains: dict[str, dict] | None = None,
        dynamic: dict[str, dict] | None = None,
        environment_names: set[str] | None = None,
        generated_path_names: set[str] | None = None,
    ):
        return make_probe.run_probe(
            reporter.AuthorityLoader(root, entries),
            {"all"},
            {} if domains is None else domains,
            {} if dynamic is None else dynamic,
            declared_external_names=set(
                {} if domains is None else domains
            ),
            environment_names=environment_names,
            generated_path_names=generated_path_names,
            scratch_root=root / "artifacts",
        )["all"]

    @staticmethod
    def actual_make(root: Path, *arguments: str):
        environment = {
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        }
        return subprocess.run(
            [
                "/usr/bin/make",
                "--no-print-directory",
                "-n",
                "-B",
                "--trace",
                *arguments,
                "all",
            ],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_conditionals_and_finite_origins_use_actual_make(self):
        directory, root, entries = self.fixture(
            "MODE ?= one\n"
            "ifeq ($(MODE),two)\n"
            "all: two\n"
            "else\n"
            "all: one\n"
            "endif\n"
            "one:\n\t@printf 'one\\n'\n"
            "two:\n\t@printf 'two\\n'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "MODE": {
                        "kind": "explicit",
                        "values": ["one", "two"],
                    }
                },
                environment_names={"MODE"},
            )
            self.assertEqual(
                set(authority["transitive"]),
                {"one", "two"},
            )
            records = authority["record"]["variants"]
            self.assertEqual(
                {
                    (item["origin"], item["value"])
                    for item in records
                },
                {
                    ("fallback", None),
                    ("command-line", "one"),
                    ("command-line", "two"),
                    ("environment", "one"),
                    ("environment", "two"),
                },
            )
            self.assertIn("printf 'two", self.actual_make(root, "MODE=two").stdout)
            self.assertIn(
                authority["record"]["probe_tools"]["namespace_launcher"]["mode"],
                {"user-namespace", "sudo-drop"},
            )

    def test_graph_only_database_drift_reuses_fallback_recipe_semantics(self):
        directory, root, entries = self.fixture(
            "MODE ?= one\n"
            "ifeq ($(MODE),two)\n"
            "child: UNUSED = changed\n"
            "endif\n"
            "all: child\n"
            "child:\n\t@printf 'child\\n'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "MODE": {
                        "kind": "explicit",
                        "values": ["one", "two"],
                    }
                },
                environment_names={"MODE"},
            )
            fallback = next(
                item
                for item in authority["record"]["variants"]
                if item["origin"] == "fallback"
            )
            command_line = next(
                item
                for item in authority["record"]["variants"]
                if item["origin"] == "command-line"
                and item["value"] == "two"
            )
            environment = next(
                item
                for item in authority["record"]["variants"]
                if item["origin"] == "environment"
                and item["value"] == "two"
            )
        self.assertNotEqual(
            command_line["database_sha256"],
            fallback["database_sha256"],
        )
        self.assertEqual(
            command_line["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertNotIn("semantics", command_line)
        self.assertNotEqual(
            environment["database_sha256"],
            fallback["database_sha256"],
        )
        self.assertEqual(
            environment["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertNotIn("semantics", environment)

    def test_multi_target_goal_sensitivity_skips_redundant_combined_database_probe(self):
        directory, root, entries = self.fixture(
            "one:\n\t@printf 'one\\n'\n"
            "two:\n\t@printf 'two\\n'\n"
        )
        combined_database_argvs = []
        original = make_probe._sandbox_run

        def wrapped(*args, **kwargs):
            argv = kwargs["argv"]
            if (
                "--print-data-base" in argv
                and "one" in argv
                and "two" in argv
            ):
                combined_database_argvs.append(tuple(argv))
            return original(*args, **kwargs)

        with directory, mock.patch(
            "scripts.validation_ownership.make_probe._sandbox_run",
            side_effect=wrapped,
        ):
            authority = make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"one", "two"},
                {},
                {},
                scratch_root=root / "artifacts",
            )
        self.assertEqual(set(authority), {"one", "two"})
        self.assertEqual(combined_database_argvs, [])

    def test_external_defaults_and_undefined_names_require_sealed_domains(self):
        directory, root, entries = self.fixture(
            "MODE ?= one\n"
            "NAME ?= MODE\n"
            "ITEMS ?= child\n"
            "pick = $(1)\n"
            "define RULE\n"
            "all: $(call pick,$($(NAME))) "
            "$(foreach t,$(ITEMS),$(t))\n"
            "endef\n"
            "$(eval $(RULE))\n"
            "one two child other:\n\t@printf '$@\\n'\n"
        )
        with directory:
            with self.assertRaisesRegex(
                make_probe.MakeProbeError,
                "external defaults lack sealed ambient authority.*MODE",
            ):
                make_probe.run_probe(
                    reporter.AuthorityLoader(root, entries),
                    {"all"},
                    {
                        "ITEMS": {
                            "kind": "explicit",
                            "values": ["child", "other"],
                        },
                        "NAME": {
                            "kind": "explicit",
                            "values": ["MODE"],
                        },
                    },
                    {},
                    declared_external_names={"ITEMS", "NAME"},
                    scratch_root=root / "artifacts",
                )
            authority = self.probe(
                root,
                entries,
                domains={
                    "ITEMS": {
                        "kind": "explicit",
                        "values": ["child", "other"],
                    },
                    "MODE": {
                        "kind": "explicit",
                        "values": ["one", "two"],
                    },
                    "NAME": {
                        "kind": "explicit",
                        "values": ["MODE"],
                    },
                },
                environment_names={"ITEMS", "MODE", "NAME"},
            )
            self.assertEqual(
                set(authority["transitive"]),
                {"child", "one", "other", "two"},
            )
            self.assertEqual(
                authority["variable_census"]["external_defaults"],
                ["ITEMS", "MODE", "NAME"],
            )

        directory, root, entries = self.fixture(
            "all: $(if $(UNDECLARED),$(UNDECLARED),empty).out\n"
            "empty.out fallback.out sealed.out:\n\t@printf '$@\\n'\n"
        )
        with directory:
            with self.assertRaisesRegex(
                make_probe.MakeProbeError,
                "evaluated undefined variables without sealed authority.*UNDECLARED",
            ):
                make_probe.run_probe(
                    reporter.AuthorityLoader(root, entries),
                    {"all"},
                    {},
                    {},
                    scratch_root=root / "artifacts",
                )
            authority = self.probe(
                root,
                entries,
                domains={
                    "UNDECLARED": {
                        "kind": "explicit",
                        "values": ["fallback", "sealed"],
                    }
                },
            )
            self.assertEqual(
                set(authority["transitive"]),
                {"empty.out", "fallback.out", "sealed.out"},
            )

    def test_dynamic_external_default_name_rejects(self):
        directory, root, entries = self.fixture(
            "NAME := MODE\n"
            "$($(NAME)) ?= one\n"
            "all:\n\t@true\n"
        )
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "external-default declaration has a dynamic name",
        ):
            make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"all"},
                {},
                {},
                scratch_root=root / "artifacts",
            )

    def test_each_target_uses_its_standalone_makecmdgoals(self):
        directory, root, entries = self.fixture(
            "GOALS := $(MAKECMDGOALS)\n"
            "a: $(if $(filter b,$(GOALS)),two,one)\n"
            "b: two\n"
            "one:\n\t@printf 'one\\n'\n"
            "two:\n\t@printf 'two\\n'\n"
        )
        with directory:
            environment = {
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "TZ": "UTC",
            }
            combined = subprocess.run(
                ["/usr/bin/make", "-n", "-B", "--trace", "a", "b"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            solo = subprocess.run(
                ["/usr/bin/make", "-n", "-B", "--trace", "a"],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("printf 'two", combined.stdout)
            self.assertNotIn("printf 'one", combined.stdout)
            self.assertIn("printf 'one", solo.stdout)
            authority = make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"b", "a"},
                {},
                {},
                scratch_root=root / "artifacts",
            )
            self.assertEqual(set(authority["a"]["transitive"]), {"one"})
            self.assertEqual(set(authority["b"]["transitive"]), {"two"})
            reversed_authority = make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"a", "b"},
                {},
                {},
                scratch_root=root / "artifacts",
            )
            self.assertEqual(authority, reversed_authority)

    def test_namespace_launcher_falls_back_to_exact_passwordless_sudo(self):
        calls = []

        def run(command, **kwargs):
            del kwargs
            calls.append(command)
            if command == make_probe._namespace_probe_command(sudo=False):
                return subprocess.CompletedProcess(command, 1, "", "blocked")
            if command == make_probe._namespace_probe_command(sudo=True):
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "tool 1.0\n", "")

        original = make_probe._NAMESPACE_LAUNCHER
        try:
            make_probe._NAMESPACE_LAUNCHER = None
            with mock.patch.object(
                make_probe,
                "SUDO",
                Path("/usr/bin/true"),
            ), mock.patch.object(make_probe.subprocess, "run", side_effect=run):
                selected = make_probe._select_namespace_launcher(refresh=True)
            self.assertEqual(selected["mode"], "sudo-drop")
            self.assertEqual(
                selected["argv_prefix"][:3],
                ["/usr/bin/true", "-n", "/usr/bin/unshare"],
            )
            self.assertIn(
                make_probe._namespace_probe_command(sudo=False),
                calls,
            )
        finally:
            make_probe._NAMESPACE_LAUNCHER = original

    def test_sudo_sandbox_drops_groups_ids_and_capabilities(self):
        libc = mock.Mock()
        libc.prctl.return_value = 0
        libc.capset.return_value = 0
        with mock.patch.object(
            sandbox_exec.ctypes,
            "CDLL",
            return_value=libc,
        ), mock.patch.object(
            sandbox_exec.os,
            "setgroups",
        ) as setgroups, mock.patch.object(
            sandbox_exec.os,
            "setgid",
        ) as setgid, mock.patch.object(
            sandbox_exec.os,
            "setuid",
        ) as setuid, mock.patch.object(
            sandbox_exec.os,
            "getuid",
            return_value=1001,
        ), mock.patch.object(
            sandbox_exec.os,
            "getgid",
            return_value=1002,
        ), mock.patch.object(
            sandbox_exec.os,
            "getgroups",
            return_value=[],
        ):
            sandbox_exec._drop_sudo_privileges(1001, 1002)
        setgroups.assert_called_once_with([])
        setgid.assert_called_once_with(1002)
        setuid.assert_called_once_with(1001)
        self.assertEqual(libc.prctl.call_count, 65)
        libc.capset.assert_called_once()

    def test_namespace_launcher_rejects_when_both_supported_routes_fail(self):
        original = make_probe._NAMESPACE_LAUNCHER
        calls = []

        def unavailable(command, **kwargs):
            del kwargs
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, "", "unavailable")

        try:
            make_probe._NAMESPACE_LAUNCHER = None
            with mock.patch.object(
                make_probe, "SUDO", Path("/usr/bin/true"),
            ), mock.patch.object(
                make_probe.subprocess, "run", side_effect=unavailable,
            ):
                with self.assertRaises(make_probe.MakeProbeError):
                    make_probe._select_namespace_launcher(refresh=True)
                self.assertEqual(calls, [
                    make_probe._namespace_probe_command(sudo=False),
                    make_probe._namespace_probe_command(sudo=True),
                ])
                self.assertIsNone(make_probe._NAMESPACE_LAUNCHER)
        finally:
            make_probe._NAMESPACE_LAUNCHER = original

    def test_eval_patterns_automatic_and_variable_spellings(self):
        makefile = (
            "C ?= child\n"
            "NAME ?= child\n"
            "define RULE\n"
            "%.out: %.in\n"
            "\t@printf 'pattern %s %s\\n' '$$@' '$$<'\n"
            "endef\n"
            "$(eval $(RULE))\n"
            ".SECONDEXPANSION:\n"
            "all: $C ${NAME} sample.out scoped\n"
            "scoped: VALUE = child\n"
            "scoped: $$(VALUE)\n"
            "child:\n\t@printf 'child\\n'\n"
        )
        directory, root, entries = self.fixture(
            makefile,
            {"sample.in": "input\n"},
        )
        domains = {
            name: {"kind": "tracked-fallback"}
            for name in ("C", "NAME")
        }
        with directory:
            one = self.probe(root, entries, domains=domains)
            self.assertTrue(
                {"child", "sample.in", "sample.out", "scoped"}
                <= set(one["transitive"])
            )
            recipes = one["record"]["variants"][0]["semantics"]["recipes"]
            pattern_command = " ".join(
                recipes["sample.out"]["commands"]
            )
            self.assertIn("sample.out", pattern_command)
            self.assertIn("sample.in", pattern_command)
            (root / "Makefile").write_text(
                makefile.replace("pattern %s", "changed %s"),
                encoding="ascii",
            )
            two = self.probe(root, entries, domains=domains)
            self.assertNotEqual(
                one["record"]["variants"][0]["semantic_sha256"],
                two["record"]["variants"][0]["semantic_sha256"],
            )

    def test_literal_missing_prerequisite_fails(self):
        directory, root, entries = self.fixture("all: missing.file\n")
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "No rule to make target 'missing.file'",
        ):
            self.probe(root, entries)

    def test_active_error_fails_and_comments_are_semantically_stable(self):
        directory, root, entries = self.fixture(
            "all: child\nchild:\n\t@printf 'child\\n'\n"
        )
        with directory:
            one = self.probe(root, entries)
            (root / "Makefile").write_text(
                "# unrelated comment\n"
                "all: child\nchild:\n\t@printf 'child\\n'\n",
                encoding="ascii",
            )
            two = self.probe(root, entries)
            self.assertEqual(
                one["record"]["variants"][0]["semantic_sha256"],
                two["record"]["variants"][0]["semantic_sha256"],
            )
            (root / "Makefile").write_text(
                "$(error active failure)\nall:\n\t@true\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                make_probe.MakeProbeError,
                "active failure",
            ):
                self.probe(root, entries)

    def test_unknown_shell_and_eager_assignment_never_execute(self):
        cases = (
            "all:\n\t@printf '%s\\n' '$(shell printf direct)'\n",
            "UNUSED != printf eager\nall:\n\t@true\n",
            "VALUE != printf eager\n"
            "all:\n\t@printf eager\n",
            "VALUE != printf 'eager\\n'\n"
            "all:\n\t@printf 'eager\\n'\n",
            "VALUE != printf '%s\\n' 'eager'\n"
            "all:\n"
            "\t@printf '%s\\n' \\\n"
            "\t\t'eager'\n",
        )
        for index, makefile in enumerate(cases):
            with self.subTest(index=index):
                directory, root, entries = self.fixture(makefile)
                with directory, self.assertRaisesRegex(
                    make_probe.MakeProbeError,
                    "without exactly one sealed contract",
                ):
                    self.probe(root, entries)

    def test_registered_eager_assignment_is_exact_authority(self):
        directory, root, entries = self.fixture(
            "UNUSED != printf eager\nall:\n\t@true\n"
        )
        contract = {
            "id": "fixture-eager",
            "command_regex": "^printf eager$",
            "resolved_value": "sealed",
        }
        with directory:
            authority = self.probe(
                root,
                entries,
                dynamic={"$(shell fixture)": contract},
            )
            command = authority["record"]["dynamic_commands"][0]
            self.assertEqual(command["authority_id"], "fixture-eager")
            self.assertEqual(command["command"], "printf eager")

    def test_normal_dry_run_recipe_produces_no_execution_event(self):
        directory, root, entries = self.fixture(
            "all:\n\t@printf ordinary\n"
        )
        with directory:
            authority = self.probe(root, entries)
            self.assertEqual(
                authority["record"]["dynamic_commands"],
                [],
            )
            environment = authority["record"]["sanitized_environment"]
            self.assertNotIn("VO_EVENT_PATH", environment)
            self.assertNotIn("VO_MAP_DIR", environment)

    def test_stale_scratch_is_ignored_and_repeated_probe_is_identical(self):
        directory, root, entries = self.fixture(
            "all:\n\t@printf stable\n"
        )
        with directory:
            stale = root / "artifacts" / "gnu-make-probe-stale"
            stale.mkdir(parents=True)
            sentinel = stale / "sentinel"
            sentinel.write_text("untrusted stale state\n", encoding="ascii")
            first = self.probe(root, entries)
            second = self.probe(root, entries)
            self.assertEqual(first, second)
            self.assertEqual(
                sentinel.read_text(encoding="ascii"),
                "untrusted stale state\n",
            )

    def test_database_semantics_ignore_only_copy_timestamps(self):
        first = (
            "GNU Make data base\n"
            "# Files\n"
            "all: input\n"
            "\t@printf stable\n"
            "#  Last modified 2026-09-01 10:00:00.000000000\n"
            "# Finished Make data base\n"
        )
        second = first.replace(
            "2026-09-01 10:00:00.000000000",
            "2026-09-01 10:01:02.123456789",
        )
        self.assertEqual(
            make_probe._database_semantics(first),
            make_probe._database_semantics(second),
        )
        self.assertNotEqual(
            make_probe._database_semantics(first),
            make_probe._database_semantics(
                first.replace("all: input", "all: changed-input")
            ),
        )
        self.assertNotEqual(
            make_probe._database_semantics(first),
            make_probe._database_semantics(
                first.replace("printf stable", "printf changed")
            ),
        )

    def test_candidate_make_cannot_forge_supervisor_control_files(self):
        directory, root, entries = self.fixture(
            "$(file >/work/events.bin,candidate-forgery)\n"
            "$(file >/work/map,candidate-forgery)\n"
            "CANDIDATE_READ := $(file </work/events.bin)\n"
            "all:\n\t@true\n"
        )
        with directory:
            authority = self.probe(root, entries)
            self.assertEqual(
                authority["record"]["dynamic_commands"],
                [],
            )

    def test_registered_command_has_no_supervisor_control_descriptors(self):
        script = (
            "import os\n"
            "from pathlib import Path\n"
            "Path('/work/events.bin').write_text('candidate', encoding='ascii')\n"
            "Path('/work/map').write_text('candidate', encoding='ascii')\n"
            "for descriptor in (3, 4):\n"
            "    try:\n"
            "        os.write(descriptor, b'candidate')\n"
            "    except OSError:\n"
            "        continue\n"
            "    raise RuntimeError('supervisor descriptor leaked')\n"
            "print('sealed')\n"
        )
        directory, root, entries = self.fixture(
            "VALUE != python3 forge.py\nall:\n\t@true\n",
            {"forge.py": script},
        )
        contract = {
            "id": "fixture-control-forgery",
            "command_regex": "^python3 forge\\.py$",
            "resolved_value": None,
        }
        with directory:
            authority = self.probe(
                root,
                entries,
                dynamic={"$(shell fixture-forgery)": contract},
            )
            commands = authority["record"]["dynamic_commands"]
            self.assertEqual(
                [item["authority_id"] for item in commands],
                ["fixture-control-forgery"],
            )

    def test_interceptor_event_count_and_format_are_supervisor_bound(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            path = Path(directory) / "events.bin"
            path.write_bytes(
                struct.pack(
                    "<IIIII",
                    0xFFFFFFFF,
                    9,
                    0,
                    0,
                    0,
                )
            )
            with self.assertRaisesRegex(
                make_probe.MakeProbeError,
                "mapping count differs",
            ):
                make_probe._read_events(
                    path,
                    expected_mapping_count=0,
                )
            path.write_bytes(b"\0")
            with self.assertRaisesRegex(
                make_probe.MakeProbeError,
                "truncated event",
            ):
                make_probe._read_events(
                    path,
                    expected_mapping_count=0,
                )

    def test_mapping_materialization_appends_only_new_commands(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            mapping_dir = Path(directory) / "mapping"
            first_command = "printf first"
            second_command = "printf second"
            first_hash = make_probe._command_hash(first_command)
            second_hash = make_probe._command_hash(second_command)
            writes = []
            original = Path.write_bytes

            def spy(path, data):
                writes.append((Path(path).name, bytes(data)))
                return original(path, data)

            with mock.patch.object(
                Path,
                "write_bytes",
                autospec=True,
                side_effect=spy,
            ):
                materialized = make_probe._write_mapping(
                    mapping_dir,
                    [
                        {
                            "command": first_command,
                            "output": b"first\n",
                        }
                    ],
                    materialized_names=set(),
                )
                first_pass = [name for name, _ in writes]
                materialized = make_probe._write_mapping(
                    mapping_dir,
                    [
                        {
                            "command": first_command,
                            "output": b"first\n",
                        },
                        {
                            "command": second_command,
                            "output": b"second\n",
                        },
                    ],
                    materialized_names=materialized,
                )
            second_pass = [name for name, _ in writes[len(first_pass):]]
            self.assertEqual(
                set(first_pass),
                {f"{first_hash}.cmd", f"{first_hash}.out"},
            )
            self.assertEqual(
                set(second_pass),
                {f"{second_hash}.cmd", f"{second_hash}.out"},
            )
            self.assertEqual(
                materialized,
                {first_hash, second_hash},
            )
            self.assertEqual(
                {item.name for item in mapping_dir.iterdir()},
                {
                    f"{first_hash}.cmd",
                    f"{first_hash}.out",
                    f"{second_hash}.cmd",
                    f"{second_hash}.out",
                },
            )

    def test_absolute_and_untracked_includes_reject(self):
        cases = (
            (
                "-include /dev/stdin\nall:\n\t@true\n",
                "absolute or dynamic include",
            ),
            (
                "include missing-untracked.mk\nall:\n\t@true\n",
                "untracked include",
            ),
        )
        for makefile, message in cases:
            with self.subTest(makefile=makefile):
                directory, root, entries = self.fixture(makefile)
                with directory, self.assertRaisesRegex(
                    make_probe.MakeProbeError,
                    message,
                ):
                    self.probe(root, entries)

    def test_pure_registered_command_uses_process_cache(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            base = Path(directory)
            tree = base / "tree"
            build_output = base / "build-output"
            command_work = base / "command-work"
            tree.mkdir()
            build_output.mkdir()
            command_work.mkdir()
            contract = {
                "id": "fixture-pure",
                "resolved_value": None,
            }
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="sealed\n",
                stderr="",
            )
            make_probe._REGISTERED_COMMAND_CACHE.clear()
            with mock.patch.object(
                make_probe,
                "_sandbox_run",
                return_value=completed,
            ) as sandbox:
                first = make_probe._execute_registered_command(
                    "python3 -c \"print('sealed')\"",
                    contract,
                    base=base,
                    build_output=build_output,
                    cache_namespace=("fixture",),
                    command_work=command_work,
                    direct_arguments=["python3", "-c", "print('sealed')"],
                    tree=tree,
                    environment={},
                )
                second = make_probe._execute_registered_command(
                    "python3 -c \"print('sealed')\"",
                    contract,
                    base=base,
                    build_output=build_output,
                    cache_namespace=("fixture",),
                    command_work=command_work,
                    direct_arguments=["python3", "-c", "print('sealed')"],
                    tree=tree,
                    environment={},
                )
            self.assertEqual(first, b"sealed\n")
            self.assertEqual(second, b"sealed\n")
            self.assertEqual(sandbox.call_count, 1)

    def test_build_touching_registered_command_skips_process_cache(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            base = Path(directory)
            tree = base / "tree"
            build_output = base / "build-output"
            command_work = base / "command-work"
            tree.mkdir()
            build_output.mkdir()
            command_work.mkdir()
            contract = {
                "id": "fixture-build-touching",
                "resolved_value": None,
            }
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            )
            make_probe._REGISTERED_COMMAND_CACHE.clear()
            with mock.patch.object(
                make_probe,
                "_sandbox_run",
                return_value=completed,
            ) as sandbox:
                for _ in range(2):
                    make_probe._execute_registered_command(
                        "/usr/bin/make -j4 build/generated/data/data_classes.o",
                        contract,
                        base=base,
                        build_output=build_output,
                        cache_namespace=("fixture",),
                        command_work=command_work,
                        direct_arguments=None,
                        tree=tree,
                        environment={},
                    )
            self.assertEqual(sandbox.call_count, 2)

    def test_build_include_traversal_aliases_and_symlinks_reject(self):
        directory, root, entries = self.fixture(
            "$(file >/work/evil.mk,evil: ;)\n"
            "include build/../../work/evil.mk\n"
            "all: evil\n"
        )
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "noncanonical include",
        ):
            self.probe(root, entries)

        with tempfile.TemporaryDirectory(dir=SCRATCH_ROOT) as directory:
            base = Path(directory)
            source = base / "source"
            build = base / "build"
            outside = base / "outside"
            source.mkdir()
            build.mkdir()
            outside.mkdir()
            (source / "Makefile").write_text("all:\n", encoding="ascii")
            entries = {
                "Makefile": reporter.GitTreeEntry(
                    "Makefile",
                    "100644",
                    "blob",
                    "0" * 40,
                )
            }
            loader = reporter.AuthorityLoader(source, entries)
            (build / "good.mk").write_text("good:\n", encoding="ascii")
            make_probe._validate_includes(
                [
                    {"optional": False, "path": "build/good.mk"},
                    {"optional": False, "path": "/repo/build/good.mk"},
                    {"optional": True, "path": "build/optional-missing.mk"},
                ],
                loader,
                build,
            )
            for include in (
                "build/../good.mk",
                "build/../../work/evil.mk",
                "build//good.mk",
                "build/%2e%2e/good.mk",
                "/work/evil.mk",
            ):
                with self.subTest(include=include), self.assertRaises(
                    make_probe.MakeProbeError,
                ):
                    make_probe._validate_includes(
                        [{"optional": False, "path": include}],
                        loader,
                        build,
                    )
            (build / "linked.mk").symlink_to(outside / "evil.mk")
            for include in ("build/linked.mk",):
                with self.subTest(include=include), self.assertRaises(
                    make_probe.MakeProbeError,
                ):
                    make_probe._validate_includes(
                        [{"optional": False, "path": include}],
                        loader,
                        build,
                    )
        directory, root, entries = self.fixture(
            "include build/missing.mk\n"
            "all:\n\t@true\n"
        )
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "GNU Make authority probe failed",
        ):
            self.probe(root, entries)

    def test_every_live_sized_domain_runs_a_cli_variant(self):
        names = [f"V{index:02d}" for index in range(80)]
        directory, root, entries = self.fixture(
            "\n".join(f"{name} ?= child" for name in names)
            + "\nall: "
            + " ".join(f"$({name})" for name in names)
            + "\nchild:\n\t@printf 'child\\n'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    name: {"kind": "tracked-fallback"}
                    for name in names
                },
            )
            command_line = [
                item
                for item in authority["record"]["variants"]
                if item["origin"] == "command-line"
            ]
            self.assertEqual(len(command_line), 80)
            self.assertEqual(
                {item["variable"] for item in command_line},
                set(names),
            )

    def test_unbounded_external_selector_rejects_but_recipe_text_is_symbolic(self):
        directory, root, entries = self.fixture(
            "DEP ?=\nall: $(DEP)\n"
        )
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "lack finite domains",
        ):
            make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"all"},
                {},
                {},
                declared_external_names={"DEP"},
                environment_names={"DEP"},
                scratch_root=root / "artifacts",
            )

        directory, root, entries = self.fixture(
            "DEP ?=\nall: $(DEP)\n"
        )
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "symbolic Make variables can shape.*DEP",
        ):
            make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"all"},
                {},
                {},
                declared_external_names={"DEP"},
                environment_names={"DEP"},
                scratch_root=root / "artifacts",
                symbolic_recipe_names={"DEP"},
            )

        directory, root, entries = self.fixture(
            "DEP ?=\nall: $(DEP)\nchild:\n\t@printf child\\n\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "DEP": {
                        "kind": "explicit",
                        "values": ["", "child"],
                    }
                },
                environment_names={"DEP"},
            )
            self.assertEqual(
                set(authority["transitive"]),
                {"child"},
            )
            self.assertEqual(
                authority["prerequisite_domain_census"]["used"],
                ["DEP"],
            )

        directory, root, entries = self.fixture(
            "MESSAGE ?= fallback\n"
            "all:\n\t@printf '%s\\n' '$(MESSAGE)'\n"
        )
        with directory:
            authority = make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"all"},
                {},
                {},
                declared_external_names={"MESSAGE"},
                environment_names={"MESSAGE"},
                scratch_root=root / "artifacts",
                symbolic_recipe_names={"MESSAGE"},
            )["all"]
            self.assertEqual(
                authority["record"]["symbolic_recipe_names"],
                ["MESSAGE"],
            )
            self.assertEqual(len(authority["record"]["variants"]), 1)

    def test_transitive_graph_selectors_cannot_be_symbolic(self):
        fixtures = {
            "define-eval": (
                "DEP ?= child\n"
                "define RULE\n"
                "all: $(DEP)\n"
                "endef\n"
                "$(eval $(RULE))\n"
                "child:\n\t@printf child\\n\n",
                {"DEP"},
            ),
            "call": (
                "DEP ?= child\n"
                "RULE = all: $(1)\n"
                "$(eval $(call RULE,$(DEP)))\n"
                "child:\n\t@printf child\\n\n",
                {"DEP"},
            ),
            "foreach": (
                "DEP ?= child\n"
                "all: $(foreach item,$(DEP),$(item))\n"
                "child:\n\t@printf child\\n\n",
                {"DEP"},
            ),
            "computed-name": (
                "NAME ?= DEP\n"
                "DEP ?= child\n"
                "all: $($(NAME))\n"
                "child:\n\t@printf child\\n\n",
                {"DEP", "NAME"},
            ),
            "second-expansion": (
                ".SECONDEXPANSION:\n"
                "DEP ?= child\n"
                "all: $$(DEP)\n"
                "child:\n\t@printf child\\n\n",
                {"DEP"},
            ),
        }
        for label, (makefile, names) in fixtures.items():
            with self.subTest(label=label):
                directory, root, entries = self.fixture(makefile)
                with directory, self.assertRaisesRegex(
                    make_probe.MakeProbeError,
                    "symbolic Make variables can shape",
                ):
                    make_probe.run_probe(
                        reporter.AuthorityLoader(root, entries),
                        {"all"},
                        {},
                        {},
                        declared_external_names=names,
                        environment_names=names,
                        scratch_root=root / "artifacts",
                        symbolic_recipe_names=names,
                    )

    def test_census_reports_only_observed_domains(self):
        directory, root, entries = self.fixture(
            "all:\n\t@printf all\\n\n",
            files={
                "unloaded.mk": (
                    "UNLOADED ?= child\n"
                    "unrelated: $(UNLOADED)\n"
                )
            },
        )
        with directory:
            authority = self.probe(root, entries)
            self.assertEqual(
                authority["variable_census"]["external_defaults"],
                [],
            )

        directory, root, entries = self.fixture(
            "USED ?= child\n"
            "UNUSED ?= child\n"
            "all: $(USED)\n"
            "child:\n\t@printf child\\n\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "USED": {"kind": "tracked-fallback"},
                    "UNUSED": {"kind": "tracked-fallback"},
                },
            )
            self.assertEqual(
                authority["prerequisite_domain_census"]["used"],
                ["USED"],
            )
            self.assertEqual(
                authority["variable_census"]["external_defaults"],
                ["USED"],
            )

        directory, root, entries = self.fixture(
            "all: tools/example/generated\n"
            "tools/example/generated:\n"
            "\t@printf tools/example/generated-extra\\n\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                generated_path_names={
                    "tools/example/generate",
                    "tools/example/generated",
                    "tools/example/generated-extra",
                },
            )
            self.assertEqual(
                authority["prerequisite_domain_census"]["generated_paths"],
                ["tools/example/generated"],
            )

    def test_unloaded_branch_definitions_do_not_backfill_observed_domains(self):
        directory, root, entries = self.fixture(
            "MODE ?= a\n"
            "include $(MODE).mk\n"
            "one two:\n\t@printf '$@\\n'\n",
            files={
                "a.mk": "DEP ?= one\nall: $(DEP)\n",
                "b.mk": "DEP = $(SELECT)\nall: $(DEP)\n",
            },
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "MODE": {
                        "kind": "explicit",
                        "values": ["a"],
                    },
                    "DEP": {
                        "kind": "tracked-fallback",
                    },
                    "SELECT": {
                        "kind": "explicit",
                        "values": ["one", "two"],
                    },
                },
            )
        self.assertEqual(
            authority["prerequisite_domain_census"]["used"],
            ["DEP", "MODE"],
        )
        self.assertEqual(
            authority["variable_census"]["external_defaults"],
            ["DEP", "MODE"],
        )
        self.assertNotIn(
            "SELECT",
            {
                item[1]
                for variant in authority["record"]["variants"]
                for item in variant["assignments"]
            },
        )

    def test_branch_loaded_domain_rejects_unsealed_and_reaches_fixed_point(self):
        makefile = (
            "MODE ?= a\n"
            "include $(MODE).mk\n"
            "one two:\n\t@printf '$@\\n'\n"
        )
        files = {
            "a.mk": "all: one\n",
            "b.mk": "DEP ?= one\nall: $(DEP)\n",
        }
        directory, root, entries = self.fixture(makefile, files=files)
        with directory, self.assertRaisesRegex(
            make_probe.MakeProbeError,
            "external defaults lack sealed ambient authority.*DEP",
        ):
            self.probe(
                root,
                entries,
                domains={
                    "MODE": {
                        "kind": "explicit",
                        "values": ["a", "b"],
                    }
                },
            )

        domains = {
            "MODE": {
                "kind": "explicit",
                "values": ["a", "b"],
            },
            "DEP": {
                "kind": "explicit",
                "values": ["one", "two"],
            },
        }
        directory, root, entries = self.fixture(makefile, files=files)
        with directory:
            authority = self.probe(
                root,
                entries,
                domains=domains,
                environment_names={"DEP", "MODE"},
            )
        directory, root, entries = self.fixture(makefile, files=files)
        with directory:
            reversed_authority = self.probe(
                root,
                entries,
                domains=dict(reversed(list(domains.items()))),
                environment_names={"DEP", "MODE"},
            )
        self.assertEqual(authority, reversed_authority)
        self.assertEqual(set(authority["transitive"]), {"one", "two"})
        self.assertEqual(
            authority["prerequisite_domain_census"]["used"],
            ["DEP", "MODE"],
        )
        self.assertTrue(
            any(
                {
                    tuple(item)
                    for item in variant["assignments"]
                }
                >= {
                    ("command-line", "DEP", "two"),
                    ("command-line", "MODE", "b"),
                }
                for variant in authority["record"]["variants"]
            )
        )

    def test_nested_branch_include_domains_reach_fixed_point(self):
        directory, root, entries = self.fixture(
            "MODE ?= a\n"
            "include $(MODE).mk\n"
            "base one two:\n\t@printf '$@\\n'\n",
            files={
                "a.mk": "all: base\n",
                "b.mk": "DEP ?= x\ninclude b-$(DEP).mk\n",
                "b-x.mk": "all: one\n",
                "b-y.mk": "SELECT ?= p\ninclude $(SELECT).mk\n",
                "p.mk": "all: one\n",
                "q.mk": "all: two\n",
            },
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "MODE": {
                        "kind": "explicit",
                        "values": ["a", "b"],
                    },
                    "DEP": {
                        "kind": "explicit",
                        "values": ["x", "y"],
                    },
                    "SELECT": {
                        "kind": "explicit",
                        "values": ["p", "q"],
                    },
                },
            )
        self.assertEqual(
            authority["prerequisite_domain_census"]["used"],
            ["DEP", "MODE", "SELECT"],
        )
        self.assertEqual(
            set(authority["transitive"]),
            {"base", "one", "two"},
        )
        self.assertTrue(
            any(
                {item[1] for item in variant["assignments"]}
                == {"DEP", "MODE", "SELECT"}
                for variant in authority["record"]["variants"]
            )
        )

    def test_branch_only_symbolic_input_is_observed_as_recipe_only(self):
        directory, root, entries = self.fixture(
            "MODE ?= a\ninclude $(MODE).mk\n",
            files={
                "a.mk": "all:\n\t@printf a\\n\n",
                "b.mk": (
                    "MESSAGE ?= fallback\n"
                    "all:\n\t@printf '%s\\n' '$(MESSAGE)'\n"
                ),
            },
        )
        with directory:
            authority = make_probe.run_probe(
                reporter.AuthorityLoader(root, entries),
                {"all"},
                {
                    "MODE": {
                        "kind": "explicit",
                        "values": ["a", "b"],
                    }
                },
                {},
                declared_external_names={"MESSAGE", "MODE"},
                scratch_root=root / "artifacts",
                symbolic_recipe_names={"MESSAGE"},
            )["all"]
        self.assertEqual(
            authority["record"]["symbolic_recipe_names"],
            ["MESSAGE"],
        )
        self.assertEqual(
            authority["variable_census"]["external_defaults"],
            ["MESSAGE", "MODE"],
        )
        self.assertNotIn(
            "MESSAGE",
            {
                item[1]
                for variant in authority["record"]["variants"]
                for item in variant["assignments"]
            },
        )

    def test_recipe_only_finite_domain_is_censused_without_enumeration(self):
        directory, root, entries = self.fixture(
            "MESSAGE ?= fallback\n"
            "all:\n\t@printf '%s\\n' '$(MESSAGE)'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={
                    "MESSAGE": {
                        "kind": "explicit",
                        "values": ["fallback", "other"],
                    }
                },
                environment_names={"MESSAGE"},
            )
        self.assertEqual(
            authority["prerequisite_domain_census"]["used"],
            ["MESSAGE"],
        )
        self.assertEqual(
            authority["variable_census"]["external_defaults"],
            ["MESSAGE"],
        )
        self.assertEqual(
            [item["origin"] for item in authority["record"]["variants"]],
            ["fallback"],
        )

    def test_explicit_graph_assignment_skips_environment_origin(self):
        directory, root, entries = self.fixture(
            "DEP := child\n"
            "all: $(DEP)\n"
            "child:\n\t@printf child\\n\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={"DEP": {"kind": "tracked-fallback"}},
                environment_names={"DEP"},
            )
        self.assertEqual(
            [item["origin"] for item in authority["record"]["variants"]],
            ["fallback", "command-line"],
        )

    def test_same_value_tracked_fallback_skips_database_only_probes(self):
        directory, root, entries = self.fixture(
            "DEP ?= child\n"
            "all: $(DEP)\n"
            "child:\n\t@printf child\\n\n"
        )
        database_argvs = []
        original = make_probe._sandbox_run

        def wrapped(*args, **kwargs):
            argv = kwargs["argv"]
            environment = kwargs["environment"]
            if "--print-data-base" in argv and (
                "DEP=child" in argv or environment.get("DEP") == "child"
            ):
                database_argvs.append(tuple(argv))
            return original(*args, **kwargs)

        with directory, mock.patch(
            "scripts.validation_ownership.make_probe._sandbox_run",
            side_effect=wrapped,
        ):
            authority = self.probe(
                root,
                entries,
                domains={"DEP": {"kind": "tracked-fallback"}},
                environment_names={"DEP"},
            )
        self.assertEqual(
            [item["origin"] for item in authority["record"]["variants"]],
            ["fallback", "command-line", "environment"],
        )
        self.assertEqual(database_argvs, [])

    def test_same_value_recipe_graph_variant_reuses_baseline_without_introspection(self):
        directory, root, entries = self.fixture(
            "DEP ?= child\n"
            "all: $(DEP)\n"
            "child:\n\t@printf '%s\\n' '$(DEP)'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={"DEP": {"kind": "tracked-fallback"}},
                environment_names={"DEP"},
            )
        fallback = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "fallback"
        )
        command_line = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "command-line"
        )
        environment = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "environment"
        )
        self.assertEqual(
            command_line["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertNotIn("semantics", command_line)
        self.assertEqual(
            environment["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertNotIn("semantics", environment)

    def test_origin_introspection_keeps_same_value_recipe_variant_distinct(self):
        directory, root, entries = self.fixture(
            "DEP ?= child\n"
            "all: $(DEP)\n"
            "child:\n\t@printf '%s\\n' '$(origin DEP)'\n"
        )
        with directory:
            authority = self.probe(
                root,
                entries,
                domains={"DEP": {"kind": "tracked-fallback"}},
                environment_names={"DEP"},
            )
        fallback = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "fallback"
        )
        command_line = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "command-line"
        )
        environment = next(
            item
            for item in authority["record"]["variants"]
            if item["origin"] == "environment"
        )
        self.assertNotEqual(
            command_line["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertIn("semantics", command_line)
        self.assertNotEqual(
            environment["semantic_sha256"],
            fallback["semantic_sha256"],
        )
        self.assertIn("semantics", environment)

    def test_variant_fixed_point_caps_fail_closed(self):
        simple = "MODE ?= a\nall: $(MODE)\na b:\n\t@true\n"
        domains = {
            "MODE": {
                "kind": "explicit",
                "values": ["a", "b"],
            }
        }
        for constant, value, message in (
            ("MAX_VARIANT_STATES", 1, "state bound"),
            ("MAX_DISCOVERED_SOURCES", 0, "source bound"),
            ("MAX_DISCOVERED_DOMAINS", 0, "domain bound"),
            ("MAX_PROBE_SECONDS", -1, "time bound"),
        ):
            with self.subTest(constant=constant):
                directory, root, entries = self.fixture(simple)
                with directory, mock.patch.object(
                    make_probe,
                    constant,
                    value,
                ), self.assertRaisesRegex(
                    make_probe.MakeProbeError,
                    message,
                ):
                    self.probe(root, entries, domains=domains)

        branch_makefile = (
            "MODE ?= a\n"
            "include $(MODE).mk\n"
            "one two:\n\t@true\n"
        )
        branch_files = {
            "a.mk": "all: one\n",
            "b.mk": "DEP ?= one\nall: $(DEP)\n",
        }
        branch_domains = {
            **domains,
            "DEP": {
                "kind": "explicit",
                "values": ["one", "two"],
            },
        }
        for constant, value, message in (
            ("MAX_CONTEXT_STATES", 0, "combination bound"),
            ("MAX_CONTEXT_DEPTH", 1, "context depth bound"),
        ):
            with self.subTest(constant=constant):
                directory, root, entries = self.fixture(
                    branch_makefile,
                    files=branch_files,
                )
                with directory, mock.patch.object(
                    make_probe,
                    constant,
                    value,
                ), self.assertRaisesRegex(
                    make_probe.MakeProbeError,
                    message,
                ):
                    self.probe(
                        root,
                        entries,
                        domains=branch_domains,
                    )


if __name__ == "__main__":
    unittest.main()
