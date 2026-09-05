from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

from scripts.validation_ownership.budget import (
    MAX_PROBE_SECONDS,
    ProbeBudget,
    ProbeBudgetError,
    ProbeCache,
    ProbeLimits,
    run_bounded_futures,
)
from scripts.validation_ownership.make_probe import (
    MakeProbeError,
    MakeVariant,
    run_make_probe,
)
from scripts.validation_ownership.sandbox import (
    ExecutionSnapshot,
    ProbeSandboxError,
    RegisteredCommand,
    REQUEST_MAGIC,
    RESPONSE_MAGIC,
    _DispatchServer,
    run_bounded_process,
)
from scripts.validation_ownership.source_probe import (
    SourceContract,
    SourceProbeError,
    probe_generated_sources,
)


ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / "build/test-artifacts/validation-ownership-tests"


def limits(
    *,
    seconds: float = 60,
    byte_overrides: dict[str, int] | None = None,
    count_overrides: dict[str, int] | None = None,
    variants: int = 4096,
) -> ProbeLimits:
    defaults = ProbeLimits(seconds=seconds)
    byte_limits = dict(defaults.bytes)
    count_limits = dict(defaults.counts)
    if byte_overrides:
        byte_limits.update(byte_overrides)
    if count_overrides:
        count_limits.update(count_overrides)
    return ProbeLimits(
        seconds=seconds,
        variants=variants,
        subprocesses=defaults.subprocesses,
        workers=defaults.workers,
        bytes=byte_limits,
        counts=count_limits,
    )


class ProbeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(SCRATCH, ignore_errors=True)
        SCRATCH.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(SCRATCH, ignore_errors=True)

    def fixture(self, name: str, files: dict[str, bytes | str]) -> Path:
        root = SCRATCH / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                target.write_text(content, encoding="utf-8")
            else:
                target.write_bytes(content)
        return root

    def make_probe(
        self,
        root: Path,
        *,
        targets: set[str] | None = None,
        variants: list[MakeVariant] | None = None,
        owner_inputs: dict[str, set[str]] | None = None,
        commands: list[RegisteredCommand] | None = None,
        budget: ProbeBudget | None = None,
    ):
        selected_targets = {"all"} if targets is None else targets
        return run_make_probe(
            ExecutionSnapshot.capture(root),
            targets=selected_targets,
            variants=[MakeVariant()] if variants is None else variants,
            owner_inputs=(
                {target: {"Makefile"} for target in selected_targets}
                if owner_inputs is None
                else owner_inputs
            ),
            registered_commands=[] if commands is None else commands,
            scratch_root=SCRATCH,
            budget=(
                ProbeBudget(limits())
                if budget is None
                else budget
            ),
        )


class MakeIsolationTests(ProbeTestCase):
    def test_authentic_eval_pattern_and_variant_semantics(self):
        root = self.fixture(
            "make-authentic",
            {
                "Makefile": (
                    "MODE ?= one\n"
                    "define RULE\n"
                    "%.out: %.in\n"
                    "\t@echo $(MODE) $$@ $$<\n"
                    "endef\n"
                    "$(eval $(RULE))\n"
                ),
                "case.in": "input\n",
            },
        )
        observations = self.make_probe(
            root,
            targets={"case.out"},
            variants=[
                MakeVariant.from_dict({"MODE": "one"}),
                MakeVariant.from_dict({"MODE": "two"}),
            ],
            owner_inputs={"case.out": {"Makefile", "case.in"}},
        )
        self.assertEqual(len(observations), 2)
        self.assertIn(b"echo one case.out case.in", observations[0].raw_stdout)
        self.assertIn(b"echo two case.out case.in", observations[1].raw_stdout)
        self.assertNotEqual(
            observations[0].semantic_fingerprint,
            observations[1].semantic_fingerprint,
        )

    def test_registered_dispatch_is_exact_and_cached_per_probe(self):
        root = self.fixture(
            "make-dispatch",
            {
                "Makefile": (
                    "VALUE := $(shell printf token)\n"
                    "all:\n"
                    "\t@echo $(VALUE)\n"
                )
            },
        )
        calls = []

        def handler(command: str, _budget: ProbeBudget) -> bytes:
            calls.append(command)
            return b"trusted"

        observations = self.make_probe(
            root,
            variants=[
                MakeVariant.from_dict({"MODE": "one"}),
                MakeVariant.from_dict({"MODE": "two"}),
            ],
            commands=[
                RegisteredCommand(
                    "printf-token",
                    r"printf token",
                    handler,
                    ("printf",),
                )
            ],
        )
        self.assertEqual(calls, ["printf token"])
        self.assertEqual(
            [item.command_events[0]["id"] for item in observations],
            ["printf-token", "printf-token"],
        )
        self.assertTrue(
            all(b"echo trusted" in item.raw_stdout for item in observations)
        )

    def test_supervisor_built_native_handler_inherits_no_dispatch_descriptors(self):
        root = self.fixture(
            "make-native-handler",
            {
                "Makefile": (
                    "VALUE := $(shell native-tool verify)\n"
                    "all:\n"
                    "\t@echo $(VALUE)\n"
                ),
                "native-tool.c": (
                    "#include <errno.h>\n"
                    "#include <fcntl.h>\n"
                    "#include <stdio.h>\n"
                    "#include <unistd.h>\n"
                    "int main(void) {\n"
                    "  int fd;\n"
                    "  for (fd = 3; fd <= 5; ++fd) {\n"
                    "    errno = 0;\n"
                    "    if (fcntl(fd, F_GETFD) != -1 || errno != EBADF)\n"
                    "      return 2;\n"
                    "  }\n"
                    "  if (access(\"/control/dispatch.sock\", F_OK) == 0)\n"
                    "    return 3;\n"
                    "  if (access(\"/proc/self/fd\", F_OK) == 0)\n"
                    "    return 4;\n"
                    "  puts(\"native-fds-closed\");\n"
                    "  return 0;\n"
                    "}\n"
                ),
            },
        )
        native = SCRATCH / "supervisor-native-tool"
        subprocess.run(
            [
                "/usr/bin/cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(root / "native-tool.c"),
                "-o",
                str(native),
            ],
            check=True,
            capture_output=True,
        )
        observation = self.make_probe(
            root,
            commands=[
                RegisteredCommand.native(
                    "native-tool",
                    "native-tool verify",
                    native,
                    scratch_root=SCRATCH,
                )
            ],
        )[0]
        self.assertIn(b"native-fds-closed", observation.raw_stdout)
        self.assertRegex(
            observation.command_events[0]["authority"]["executable_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_unknown_parse_time_shell_fails_closed(self):
        root = self.fixture(
            "make-unknown-shell",
            {
                "Makefile": (
                    "VALUE := $(shell unregistered-command)\n"
                    "all:\n"
                    "\t@echo $(VALUE)\n"
                )
            },
        )
        with self.assertRaisesRegex(
            MakeProbeError,
            "failed safely|registered dispatch",
        ):
            self.make_probe(root)

    def test_load_shared_object_rejects_before_launch(self):
        root = self.fixture(
            "make-load",
            {
                "module.c": "int plugin_is_GPL_compatible;\n",
                "Makefile": "load module.so\nall:\n\t@echo forged\n",
            },
        )
        subprocess.run(
            [
                "/usr/bin/cc",
                "-shared",
                "-fPIC",
                str(root / "module.c"),
                "-o",
                str(root / "module.so"),
            ],
            check=True,
            capture_output=True,
        )
        budget = ProbeBudget(limits())
        with self.assertRaisesRegex(MakeProbeError, "loadable-module"):
            self.make_probe(root, budget=budget)
        self.assertEqual(budget.snapshot()["subprocesses"], 0)

    def test_dynamically_constructed_load_cannot_execute_shared_object(self):
        root = self.fixture(
            "make-dynamic-load",
            {
                "module.c": "int plugin_is_GPL_compatible;\n",
                "Makefile": (
                    "DIRECTIVE := load\n"
                    "$(eval $(DIRECTIVE) module.so)\n"
                    "all:\n"
                    "\t@echo forged\n"
                ),
            },
        )
        subprocess.run(
            [
                "/usr/bin/cc",
                "-shared",
                "-fPIC",
                str(root / "module.c"),
                "-o",
                str(root / "module.so"),
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(MakeProbeError):
            self.make_probe(root)

    def test_shell_and_make_execution_controls_reject_before_launch(self):
        cases = {
            "shell": "override SHELL := ./native\n",
            "export-shell": "override export SHELL = ./native\n",
            "eval-shell": "$(eval SHELL := ./native)\n",
            "flags": "override .SHELLFLAGS := -ec\n",
            "eval-flags": "$(eval .SHELLFLAGS := -ec)\n",
            "make": "override MAKE := ./native\n",
        }
        for label, control in cases.items():
            with self.subTest(label=label):
                root = self.fixture(
                    f"make-control-{label}",
                    {"Makefile": control + "all:\n\t@echo forged\n"},
                )
                budget = ProbeBudget(limits())
                with self.assertRaisesRegex(
                    MakeProbeError,
                    "reserved execution control",
                ):
                    self.make_probe(root, budget=budget)
                self.assertEqual(budget.snapshot()["subprocesses"], 0)

    def test_loaded_controls_reject_but_unloaded_make_fragments_are_irrelevant(self):
        root = self.fixture(
            "make-loaded-control",
            {
                "Makefile": "include active.mk\nall:\n\t@echo safe\n",
                "active.mk": "override SHELL := ./native\n",
                "dormant.mk": "override SHELL := ./other-native\n",
            },
        )
        with self.assertRaisesRegex(
            MakeProbeError,
            "reserved execution control",
        ):
            self.make_probe(root)
        (root / "Makefile").write_text(
            "all:\n\t@echo safe\n",
            encoding="ascii",
        )
        observation = self.make_probe(root)[0]
        self.assertIn(b"echo safe", observation.raw_stdout)

    def test_comments_and_shell_recipe_text_are_not_execution_controls(self):
        root = self.fixture(
            "make-control-comments",
            {
                "Makefile": (
                    "# override SHELL := ./comment-only\n"
                    "all:\n"
                    "\t@echo 'override SHELL := ./shell-text'\n"
                )
            },
        )
        observation = self.make_probe(root)[0]
        self.assertIn(b"override SHELL := ./shell-text", observation.raw_stdout)

    def test_normal_and_exported_shell_controls_are_disabled_by_prelude(self):
        for label, control in (
            ("normal", "SHELL := ./native\n.SHELLFLAGS := forged\n"),
            ("export", "export SHELL = ./native\nexport .SHELLFLAGS = forged\n"),
        ):
            with self.subTest(label=label):
                root = self.fixture(
                    f"make-disabled-control-{label}",
                    {
                        "Makefile": (
                            control
                            + "VALUE := $(shell printf token)\n"
                            + "all:\n\t@echo $(VALUE)\n"
                        )
                    },
                )
                observation = self.make_probe(
                    root,
                    commands=[
                        RegisteredCommand.fixed(
                            "trusted-printf",
                            "printf token",
                            b"trusted",
                        )
                    ],
                )[0]
                self.assertIn(b"echo trusted", observation.raw_stdout)
                self.assertEqual(
                    observation.command_events[0]["argv"],
                    ["/bin/vo-shell", "-c", "printf token"],
                )

    def test_candidate_dynamic_static_and_gbagfx_binaries_are_noexec(self):
        source = (
            "#include <stdio.h>\n"
            "int main(void) { puts(\"forged\"); return 0; }\n"
        )
        for label, compile_flags, path in (
            ("dynamic", [], "native"),
            ("static", ["-static"], "native"),
            ("gbagfx", [], "tools/gbagfx/gbagfx"),
        ):
            with self.subTest(label=label):
                root = self.fixture(
                    f"make-native-{label}",
                    {
                        "native.c": source,
                        "Makefile": (
                            "CONTROL := SHELL\n"
                            f"$(eval override $(CONTROL) := ./{path})\n"
                            "VALUE := $(shell forged)\n"
                            "all:\n"
                            "\t@echo $(VALUE)\n"
                        ),
                    },
                )
                output = root / path
                output.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "/usr/bin/cc",
                        *compile_flags,
                        str(root / "native.c"),
                        "-o",
                        str(output),
                    ],
                    check=True,
                    capture_output=True,
                )
                with self.assertRaisesRegex(
                    MakeProbeError,
                    "failed safely|registered dispatch",
                ):
                    self.make_probe(root)

    def test_file_include_and_unknown_parse_time_routes_cannot_forge(self):
        cases = {
            "file": (
                "$(file >/control/dispatch.sock,forged)\n"
                "all:\n\t@echo forged\n"
            ),
            "include": (
                "include /control/dispatch.sock\n"
                "all:\n\t@echo forged\n"
            ),
            "eval-shell": (
                "NAME := SHELL\n"
                "$(eval $(NAME) := ./native)\n"
                "VALUE := $(shell forged)\n"
                "all:\n\t@echo $(VALUE)\n"
            ),
        }
        for label, makefile in cases.items():
            with self.subTest(label=label):
                root = self.fixture(
                    f"make-route-{label}",
                    {"Makefile": makefile},
                )
                with self.assertRaises(MakeProbeError):
                    self.make_probe(root)

    def test_supervisor_descriptors_and_proc_are_absent(self):
        root = self.fixture(
            "make-fds",
            {
                "Makefile": (
                    "FD3 := $(file </proc/self/fd/3)\n"
                    "FD4 := $(file </proc/self/fd/4)\n"
                    "FD5 := $(file </proc/self/fd/5)\n"
                    "ifneq ($(strip $(FD3)$(FD4)$(FD5)),)\n"
                    "$(error inherited supervisor descriptor)\n"
                    "endif\n"
                    "all:\n"
                    "\t@echo descriptors-closed\n"
                )
            },
        )
        observation = self.make_probe(root)[0]
        self.assertIn(b"descriptors-closed", observation.raw_stdout)
        self.assertEqual(observation.command_events, ())

    def test_symlink_aliases_never_enter_execution_snapshot(self):
        root = self.fixture(
            "make-symlink",
            {"Makefile": "all:\n\t@echo safe\n", "real.mk": "X := 1\n"},
        )
        (root / "alias.mk").symlink_to("real.mk")
        with self.assertRaisesRegex(ProbeSandboxError, "symlink"):
            ExecutionSnapshot.capture(root)
        (root / "alias.mk").unlink()
        (root / "outside").mkdir()
        (root / "outside/hidden.mk").write_text("X := 1\n", encoding="ascii")
        (root / "alias-dir").symlink_to("outside", target_is_directory=True)
        with self.assertRaisesRegex(ProbeSandboxError, "symlink"):
            ExecutionSnapshot.capture(root)
        with self.assertRaisesRegex(ProbeSandboxError, "symlink"):
            ExecutionSnapshot.capture(root, {"Makefile", "alias-dir/hidden.mk"})

    def test_invalid_command_bytes_are_rejected_at_text_boundary(self):
        root = self.fixture(
            "make-invalid-bytes",
            {
                "Makefile": (
                    "VALUE := $(shell printf token)\n"
                    "all:\n"
                    "\t@echo $(VALUE)\n"
                )
            },
        )
        with self.assertRaisesRegex(MakeProbeError, "not valid UTF-8"):
            self.make_probe(
                root,
                commands=[RegisteredCommand.fixed("invalid", "printf token", b"\xff")],
            )


class SemanticIdentityTests(ProbeTestCase):
    def test_unrelated_snapshot_drift_is_semantically_stable(self):
        root = self.fixture(
            "semantic-stability",
            {
                "Makefile": "all: input\n\t@echo $<\n",
                "input": "real-v1\n",
                "docs/note.md": "unrelated-v1\n",
                "src/unrelated.c": "int unrelated = 1;\n",
            },
        )
        first = self.make_probe(
            root,
            owner_inputs={"all": {"Makefile", "input"}},
        )[0]
        (root / "docs/note.md").write_text("unrelated-v2\n", encoding="ascii")
        second = self.make_probe(
            root,
            owner_inputs={"all": {"Makefile", "input"}},
        )[0]
        self.assertNotEqual(
            first.execution_snapshot_sha256,
            second.execution_snapshot_sha256,
        )
        self.assertEqual(first.semantic_fingerprint, second.semantic_fingerprint)
        (root / "src/unrelated.c").write_text(
            "int unrelated = 2;\n",
            encoding="ascii",
        )
        third = self.make_probe(
            root,
            owner_inputs={"all": {"Makefile", "input"}},
        )[0]
        self.assertNotEqual(
            second.execution_snapshot_sha256,
            third.execution_snapshot_sha256,
        )
        self.assertEqual(second.semantic_fingerprint, third.semantic_fingerprint)

    def test_real_owner_input_drift_invalidates_semantics(self):
        root = self.fixture(
            "semantic-real-input",
            {
                "Makefile": "all: input\n\t@echo $<\n",
                "input": "real-v1\n",
                "docs/note.md": "unrelated\n",
            },
        )
        first = self.make_probe(
            root,
            owner_inputs={"all": {"Makefile", "input"}},
        )[0]
        (root / "input").write_text("real-v2\n", encoding="ascii")
        second = self.make_probe(
            root,
            owner_inputs={"all": {"Makefile", "input"}},
        )[0]
        self.assertNotEqual(first.semantic_fingerprint, second.semantic_fingerprint)


class GeneratedSourceConfinementTests(ProbeTestCase):
    def source_probe(
        self,
        root: Path,
        contract: SourceContract,
        *,
        load_arguments: list[str],
        budget: ProbeBudget | None = None,
    ):
        return probe_generated_sources(
            root,
            program_paths={"probe.py"},
            entrypoint="probe.py",
            metadata_arguments=["metadata"],
            load_arguments=load_arguments,
            contract=contract,
            scratch_root=SCRATCH,
            budget=ProbeBudget(limits()) if budget is None else budget,
        )

    def test_declared_directory_glob_supports_all_access_forms(self):
        root = self.fixture(
            "source-positive",
            {
                "data/a.txt": "alpha\n",
                "data/b.txt": "beta\n",
                "probe.py": (
                    "import glob,json,mmap,os,sys\n"
                    "from pathlib import Path\n"
                    "if sys.argv[1] == 'metadata':\n"
                    " print(json.dumps({'root':'data','pattern':'*.txt'}))\n"
                    "else:\n"
                    " paths = sorted(glob.glob('/repo/data/*.txt'))\n"
                    " names = sorted(item.name for item in os.scandir('/repo/data'))\n"
                    " for path in paths:\n"
                    "  os.stat(path); os.lstat(path)\n"
                    "  with open(path, 'rb') as stream:\n"
                    "   with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as view:\n"
                    "    assert view[:]\n"
                    " print(json.dumps(['data/' + name for name in names]))\n"
                ),
            },
        )
        observation = self.source_probe(
            root,
            SourceContract(
                "data",
                "*.txt",
                ("data/a.txt", "data/b.txt"),
                {"root": "data", "pattern": "*.txt"},
            ),
            load_arguments=["load"],
        )
        self.assertEqual(
            observation.permitted_sources,
            ("data/a.txt", "data/b.txt"),
        )

    def test_undeclared_open_mmap_metadata_directory_glob_and_dynamic_paths_reject(self):
        operations = {
            "open": "open(target, 'rb').read()",
            "mmap": (
                "stream=open(target,'rb'); "
                "view=mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ); "
                "view[:]"
            ),
            "stat": "os.stat(target)",
            "lstat": "os.lstat(target)",
            "scandir": "list(os.scandir('/repo/hidden'))",
            "glob": "assert glob.glob('/repo/hidden/*.txt')",
            "dynamic": "Path('/repo' + '/hidden' + '/secret.txt').read_bytes()",
        }
        for label, statement in operations.items():
            with self.subTest(label=label):
                root = self.fixture(
                    f"source-undeclared-{label}",
                    {
                        "data/a.txt": "alpha\n",
                        "hidden/secret.txt": "secret\n",
                        "probe.py": (
                            "import glob,json,mmap,os,sys\n"
                            "from pathlib import Path\n"
                            "if sys.argv[1] == 'metadata':\n"
                            " print(json.dumps({'root':'data','pattern':'*.txt'}))\n"
                            "else:\n"
                            " target='/repo/hidden/secret.txt'\n"
                            f" {statement}\n"
                            " print(json.dumps(['data/a.txt']))\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(
                    SourceProbeError,
                    "admitted-source confinement",
                ):
                    self.source_probe(
                        root,
                        SourceContract(
                            "data",
                            "*.txt",
                            ("data/a.txt",),
                            {"root": "data", "pattern": "*.txt"},
                        ),
                        load_arguments=["load"],
                    )

    def test_reported_permitted_and_consumed_sets_must_agree(self):
        root = self.fixture(
            "source-agreement",
            {
                "data/a.txt": "alpha\n",
                "data/b.txt": "beta\n",
                "probe.py": (
                    "import json,sys\n"
                    "from pathlib import Path\n"
                    "if sys.argv[1] == 'metadata':\n"
                    " print(json.dumps({'root':'data','pattern':'*.txt'}))\n"
                    "else:\n"
                    " Path('/repo/data/a.txt').read_bytes()\n"
                    " print(json.dumps(['data/a.txt','data/b.txt']))\n"
                ),
            },
        )
        with self.assertRaisesRegex(SourceProbeError, "not consumed"):
            self.source_probe(
                root,
                SourceContract(
                    "data",
                    "*.txt",
                    ("data/a.txt", "data/b.txt"),
                    {"root": "data", "pattern": "*.txt"},
                ),
                load_arguments=["load"],
            )

        (root / "probe.py").write_text(
            "import json,sys\n"
            "if sys.argv[1] == 'metadata':\n"
            " print(json.dumps({'root':'data','pattern':'*.txt'}))\n"
            "else:\n"
            " print(json.dumps(['data/a.txt']))\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(
            SourceProbeError,
            "declared/reported/permitted",
        ):
            self.source_probe(
                root,
                SourceContract(
                    "data",
                    "*.txt",
                    ("data/a.txt", "data/b.txt"),
                    {"root": "data", "pattern": "*.txt"},
                ),
                load_arguments=["load"],
            )

    def test_candidate_cannot_broaden_trusted_source_contract(self):
        root = self.fixture(
            "source-broad",
            {
                "data/a.txt": "alpha\n",
                "hidden/secret.txt": "secret\n",
                "probe.py": (
                    "import json,sys\n"
                    "if sys.argv[1] == 'metadata':\n"
                    " print(json.dumps({'root':'.','pattern':'*'}))\n"
                    "else:\n"
                    " print(json.dumps(['data/a.txt']))\n"
                ),
            },
        )
        with self.assertRaisesRegex(SourceProbeError, "trusted graph/schema"):
            self.source_probe(
                root,
                SourceContract(
                    "data",
                    "*.txt",
                    ("data/a.txt",),
                    {"root": "data", "pattern": "*.txt"},
                ),
                load_arguments=["load"],
            )

    def test_symlink_source_and_invalid_protocol_bytes_reject(self):
        root = self.fixture(
            "source-symlink",
            {
                "outside.txt": "secret\n",
                "probe.py": (
                    "import json,sys\n"
                    "if sys.argv[1] == 'metadata': print('{}')\n"
                    "else: print(json.dumps(['data/a.txt']))\n"
                ),
            },
        )
        (root / "data").mkdir()
        (root / "data/a.txt").symlink_to("../outside.txt")
        with self.assertRaisesRegex(SourceProbeError, "symlink|regular file"):
            self.source_probe(
                root,
                SourceContract(
                    "data/a.txt",
                    None,
                    ("data/a.txt",),
                    {},
                ),
                load_arguments=["load"],
            )
        (root / "data/a.txt").unlink()
        (root / "data").rmdir()
        (root / "real-data").mkdir()
        (root / "real-data/a.txt").write_text("secret\n", encoding="ascii")
        (root / "data").symlink_to("real-data", target_is_directory=True)
        with self.assertRaisesRegex(SourceProbeError, "symlink"):
            self.source_probe(
                root,
                SourceContract(
                    "data/a.txt",
                    None,
                    ("data/a.txt",),
                    {},
                ),
                load_arguments=["load"],
            )

        root = self.fixture(
            "source-invalid-bytes",
            {
                "data/a.txt": "alpha\n",
                "probe.py": (
                    "import os,sys\n"
                    "if sys.argv[1] == 'metadata': os.write(1,b'\\xff')\n"
                    "else: os.write(1,b'[]')\n"
                ),
            },
        )
        with self.assertRaisesRegex(SourceProbeError, "not valid UTF-8"):
            self.source_probe(
                root,
                SourceContract(
                    "data/a.txt",
                    None,
                    ("data/a.txt",),
                    {},
                ),
                load_arguments=["load"],
            )
        for label, payload in (
            ("nonfinite", b'{"value":NaN}\n'),
            ("duplicate", b'{"value":1,"value":1}\n'),
        ):
            with self.subTest(label=label):
                root = self.fixture(
                    f"source-invalid-json-{label}",
                    {
                        "data/a.txt": "alpha\n",
                        "probe.py": (
                            "import os,sys\n"
                            f"payload={payload!r}\n"
                            "if sys.argv[1] == 'metadata': os.write(1,payload)\n"
                            "else: os.write(1,b'[]')\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(SourceProbeError, "not valid JSON"):
                    self.source_probe(
                        root,
                        SourceContract(
                            "data/a.txt",
                            None,
                            ("data/a.txt",),
                            {},
                        ),
                        load_arguments=["load"],
                    )


class AggregateBudgetTests(ProbeTestCase):
    def test_deadline_cannot_exceed_existing_3600_second_ceiling(self):
        with self.assertRaises(ValueError):
            ProbeLimits(seconds=MAX_PROBE_SECONDS + 1)

    def test_variant_count_fails_before_any_launch(self):
        root = self.fixture(
            "budget-variants",
            {"Makefile": "all:\n\t@echo safe\n"},
        )
        budget = ProbeBudget(limits(variants=2))
        with self.assertRaisesRegex(ProbeBudgetError, "variant/state"):
            self.make_probe(
                root,
                variants=[
                    MakeVariant.from_dict({"STATE": str(index)})
                    for index in range(3)
                ],
                budget=budget,
            )
        self.assertEqual(budget.snapshot()["subprocesses"], 0)

    def test_4097_state_aggregate_rejects_without_launch(self):
        root = self.fixture(
            "budget-4097",
            {"Makefile": "all:\n\t@echo safe\n"},
        )
        budget = ProbeBudget(limits())
        with self.assertRaisesRegex(ProbeBudgetError, "variant/state"):
            self.make_probe(
                root,
                variants=[
                    MakeVariant.from_dict({"STATE": str(index)})
                    for index in range(4097)
                ],
                budget=budget,
            )
        self.assertEqual(budget.snapshot()["subprocesses"], 0)

    def test_4096_states_consume_one_global_allowance(self):
        budget = ProbeBudget(limits())
        budget.preflight_variants(4096)
        with self.assertRaisesRegex(ProbeBudgetError, "variant/state"):
            budget.preflight_variants(1)
        self.assertEqual(budget.snapshot()["variants"], 4096)

    def test_one_deadline_terminates_long_process_group(self):
        pid_path = SCRATCH / "long-process.pid"
        pid_path.unlink(missing_ok=True)
        budget = ProbeBudget(limits(seconds=0.2))
        with self.assertRaises(ProbeBudgetError):
            run_bounded_process(
                [
                    str(Path(sys.executable).resolve()),
                    "-c",
                    (
                        "import os,time,pathlib;"
                        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
                        "time.sleep(30)"
                    ),
                ],
                budget,
                environment={"PATH": "/usr/bin:/bin"},
            )
        pid = int(pid_path.read_text(encoding="ascii"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cache_rejects_theoretical_eight_gibibyte_entry(self):
        budget = ProbeBudget(limits(byte_overrides={"cache": 1024}))
        cache = ProbeCache(budget)
        with self.assertRaisesRegex(ProbeBudgetError, "cache bytes"):
            cache.put(("huge",), b"x", declared_size=8 * 1024**3)
        self.assertIsNone(cache.get(("huge",)))

    def test_event_mapping_output_and_count_budgets_are_global(self):
        budget = ProbeBudget(
            limits(
                byte_overrides={
                    "events": 8,
                    "mappings": 8,
                    "outputs": 8,
                },
                count_overrides={"events": 1, "mappings": 1},
            )
        )
        budget.charge_bytes("events", 8)
        budget.charge_count("events")
        budget.charge_bytes("mappings", 8)
        budget.charge_count("mappings")
        budget.charge_bytes("outputs", 8)
        for category, operation in (
            ("events", lambda: budget.charge_bytes("events", 1)),
            ("mappings", lambda: budget.charge_count("mappings")),
            ("outputs", lambda: budget.charge_bytes("outputs", 1)),
        ):
            with self.subTest(category=category), self.assertRaises(
                ProbeBudgetError
            ):
                operation()

    def test_future_fanout_rejects_before_worker_submission(self):
        budget = ProbeBudget(limits(count_overrides={"pending": 2, "futures": 2}))
        invoked = []
        with self.assertRaises(ProbeBudgetError):
            run_bounded_futures(
                budget,
                [1, 2, 3],
                lambda item, _remaining: invoked.append(item),
            )
        self.assertEqual(invoked, [])

    def test_output_overflow_kills_child(self):
        budget = ProbeBudget(limits(byte_overrides={"outputs": 128}))
        with self.assertRaisesRegex(ProbeBudgetError, "outputs bytes"):
            run_bounded_process(
                [
                    str(Path(sys.executable).resolve()),
                    "-c",
                    "import os; os.write(1, b'x' * 1024)",
                ],
                budget,
                environment={"PATH": "/usr/bin:/bin"},
            )

    def test_failure_cleans_socket_fifo_and_descriptor_state(self):
        root = self.fixture(
            "budget-cleanup",
            {
                "Makefile": (
                    "VALUE := $(shell printf fail)\n"
                    "all:\n"
                    "\t@echo $(VALUE)\n"
                )
            },
        )
        before_fds = len(list(Path("/proc/self/fd").iterdir()))

        def fail(_command: str, _budget: ProbeBudget) -> bytes:
            raise RuntimeError("handler failed")

        with self.assertRaises(ProbeSandboxError):
            self.make_probe(
                root,
                commands=[
                    RegisteredCommand(
                        "failure",
                        r"printf fail",
                        fail,
                        ("printf",),
                    )
                ],
            )
        after_fds = len(list(Path("/proc/self/fd").iterdir()))
        self.assertLessEqual(after_fds, before_fds + 1)
        self.assertEqual(list(SCRATCH.glob(".v*")), [])
        self.assertFalse(
            any(
                path.is_fifo()
                for path in SCRATCH.rglob("*")
                if path.exists()
            )
        )

    def test_same_uid_process_outside_sandbox_cannot_forge_dispatch(self):
        path = ROOT / "build/.peer-auth.sock"
        path.unlink(missing_ok=True)
        budget = ProbeBudget(limits())
        server = _DispatchServer(
            path,
            [RegisteredCommand.fixed("printf", "printf token", b"trusted")],
            budget,
            ProbeCache(budget),
            (),
        )
        server.set_root_pid(2**30)
        with self.assertRaisesRegex(
            ProbeSandboxError,
            "outside the sandbox process tree",
        ):
            with server:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(str(path))
                    arguments = [b"/bin/sh", b"-c", b"printf token"]
                    payload = REQUEST_MAGIC + struct.pack("<I", len(arguments))
                    for argument in arguments:
                        payload += struct.pack("<I", len(argument)) + argument
                    client.sendall(payload)
                    response = client.recv(64)
                    self.assertTrue(response.startswith(RESPONSE_MAGIC))
                finally:
                    client.close()


class PublicContractTests(ProbeTestCase):
    def test_public_validation_ownership_target_and_guards(self):
        completed = subprocess.run(
            ["make", "validation-ownership-check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        mixed = subprocess.run(
            ["make", "validation-ownership-check", "compare"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("sole Make goal", mixed.stderr)
        override = subprocess.run(
            ["make", "validation-ownership-check", "SHELL=/bin/bash"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(override.returncode, 0)
        self.assertRegex(
            override.stderr,
            "execution controls|variable overrides",
        )


if __name__ == "__main__":
    unittest.main()
