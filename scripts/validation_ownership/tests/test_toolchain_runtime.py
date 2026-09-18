"""Original modern toolchain recipe, live native authority and real ARM inputs."""

import ast
import copy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import shlex
import stat
import textwrap
import subprocess
import unittest
from unittest.mock import patch

from scripts.bash_parser import normalize_bash_script_commands, tokenize_bash_command
from scripts.validation_ownership import toolchain_runtime
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.graph_commands import MakeCommands, ROOT_RUNTIME_FILES
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


class ToolchainProtocolDataTests(unittest.TestCase):
    """Pure model-data controls for the dormant stage-4 protocol helpers."""

    def model(self, path="/work/ccL3VdjV.s"):
        driver = [
            "/usr/bin/arm-none-eabi-gcc", "-isystem", "/usr/include/newlib",
            "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-ffreestanding",
            "-fno-pic", "-fno-pie", "-c", "-x", "c", "-o", "/dev/null", "-",
        ]
        cc1 = [
            "/usr/lib/gcc/arm-none-eabi/13.2.1/cc1", "-quiet", "-imultilib",
            "thumb/nofp", "-D__USES_INITFINI__", "-isystem", "/usr/include/newlib",
            "-", "-quiet", "-dumpbase", "-", "-mcpu=arm7tdmi", "-mthumb",
            "-mthumb-interwork", "-mfloat-abi=soft", "-mlibarch=armv4t",
            "-march=armv4t", "-ffreestanding", "-fno-pie", "-o", path,
        ]
        assembler = [
            "/usr/lib/gcc/arm-none-eabi/13.2.1/../../../arm-none-eabi/bin/as",
            "-march=armv4t", "-mthumb-interwork", "-mfloat-abi=soft", "-meabi=5",
            "-o", "/dev/null", path,
        ]
        images = [
            ["/usr/bin/arm-none-eabi-gcc", 1, 11, 0o100755, 100, 101, 102],
            ["/usr/lib/gcc/arm-none-eabi/13.2.1/cc1", 1, 12, 0o100755, 200, 201, 202],
            ["/usr/lib/arm-none-eabi/bin/as", 1, 13, 0o100755, 300, 301, 302],
        ]
        executions = [
            {
                "stage": "compile", "sequence": sequence, "path": image[0],
                "identity": image[1:], "argv": argv, "environment": {"LANG": "C", "EMPTY": ""},
            }
            for sequence, (image, argv) in enumerate(zip(images, (driver, cc1, assembler)), 1)
        ]
        profile = {
            "version": 2, "stage": 4, "stdin": toolchain_runtime.COMPILE_INPUT,
            "inputs": [], "driver_identity": images[0][1:], "images": images,
            "workspace": [9, 10, stat.S_IFDIR | 0o700],
        }
        roles = toolchain_runtime.compile_operand_roles(
            executions, profile, driver, complete=True,
        )
        actor_pids = (401, 402, 403)
        mode = stat.S_IFREG | 0o600
        created = [21, 22, mode, 0, 1000, 1000, 1]
        writer_opened = [21, 22, mode, 0, 1000, 1001, 1]
        sealed = [21, 22, mode, 64, 1001, 1002, 1]
        retired = [21, 22, mode, 64, 1001, 1003, 0]
        digest = "a" * 64
        receipt = {
            "version": 1,
            "scope": "probe-model/make-root-1",
            "binding": "b" * 64,
            "stage": "compile",
            "role": "stage4-assembly",
            "path": path,
            "workspace": list(profile["workspace"]),
            "actors": [
                {
                    "exec_sequence": sequence,
                    "pid": actor_pids[sequence - 1],
                    "birth_sequence": 500 + sequence,
                    "exec_record_sha256": hashlib.sha256(
                        toolchain_runtime.encoded(row)
                    ).hexdigest(),
                }
                for sequence, row in enumerate(executions, 1)
            ],
            "creation": {
                "order": 1, "syscall_sequence": 10, "syscall": "openat",
                "exec_sequence": 1, "pid": actor_pids[0], "fd": 7,
                "flags": os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                "requested_mode": 0o600, "result": 7, "identity": created,
            },
            "creator_close": {
                "order": 2, "syscall_sequence": 11, "syscall": "close", "result": 0,
            },
            "writer": {
                "exec_sequence": 2, "pid": actor_pids[1],
                "operand": {"kind": "output", "option": "-o", "argv_index": roles.output.argv_index},
                "open": {
                    "order": 3, "syscall_sequence": 20, "syscall": "openat", "fd": 8,
                    "flags": os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC,
                    "requested_mode": 0, "result": 8, "identity": writer_opened,
                },
                "completed": {
                    "order": 6, "close_order": 4, "close_syscall_sequence": 30,
                    "close_result": 0, "write_calls": 2, "written_bytes": 64,
                    "extent": 64, "sha256": digest, "identity": sealed,
                },
                "exit": {"order": 5, "result": 0},
            },
            "reader": {
                "exec_sequence": 3, "pid": actor_pids[2],
                "operand": {"kind": "input", "argv_index": roles.input.argv_index},
                "open": {
                    "order": 7, "syscall_sequence": 40, "syscall": "openat", "fd": 9,
                    "flags": os.O_RDONLY | os.O_CLOEXEC, "result": 9, "identity": sealed,
                },
                "completed": {
                    "order": 8, "close_syscall_sequence": 50, "close_result": 0,
                    "read_calls": 2, "read_bytes": 64, "extent": 64,
                    "sha256": digest, "eof_observed": False, "identity": sealed,
                },
                "exit": {"order": 9, "result": 0},
            },
            "retirement": {
                "order": 10, "syscall_sequence": 60, "syscall": "unlinkat",
                "exec_sequence": 1, "pid": actor_pids[0], "result": 0,
                "before_identity": sealed, "after_identity": retired, "path_absent": True,
            },
            "driver_exit": {"order": 11, "result": 0},
            "complete": True,
        }
        limits = toolchain_runtime.IntermediateLimits(
            file_limit=4096, observation_count=128, observation_limit=65536,
            write_limit=4096, creation_limit=4, process_limit=8, memory_limit=65536,
            syscall_limit=100, deadline=1000.0,
        )
        return {
            "driver": driver, "executions": executions, "profile": profile, "roles": roles,
            "receipt": receipt, "launch": {
                "version": 2, "scope": receipt["scope"], "binding": receipt["binding"],
            },
            "limits": limits,
        }

    def wire(self, model, *, receipt=None):
        return toolchain_runtime.INTERMEDIATE_PREFIX + json.dumps(
            model["receipt"] if receipt is None else receipt,
            separators=(",", ":"), ensure_ascii=True,
        )

    def parse(self, model, *, receipt=None, values=None, reserve=lambda size: None):
        if values is None:
            values = [self.wire(model, receipt=receipt)]
        return toolchain_runtime.intermediate_record(
            values, profile=model["profile"], launch=model["launch"],
            executions=model["executions"], returncode=0, limits=model["limits"],
            reserve=reserve,
        )

    def probes(self, model):
        return tuple(copy.deepcopy(model["executions"])) + ({
            "stage": "compile", "stdin": toolchain_runtime.COMPILE_INPUT, "eof": True,
        },)

    def test_retained_argv_roles_are_grammar_derived_and_shift_with_options(self):
        model = self.model()
        roles = model["roles"]
        self.assertEqual((roles.output.argv_index, roles.input.argv_index), (20, 7))
        self.assertEqual((roles.output.value, roles.input.value), (
            "/work/ccL3VdjV.s", "/work/ccL3VdjV.s",
        ))
        shifted = copy.deepcopy(model["executions"])
        shifted[1]["argv"].insert(1, "-quiet")
        shifted[2]["argv"].insert(1, "-mthumb")
        shifted_roles = toolchain_runtime.compile_operand_roles(
            shifted, model["profile"], shifted[0]["argv"], complete=True,
        )
        self.assertEqual((shifted_roles.output.argv_index, shifted_roles.input.argv_index), (21, 8))
        self.assertEqual(shifted_roles.output.value, roles.output.value)

    def test_repeated_quiet_and_incomplete_actual_prefix_are_closed_data(self):
        model = self.model()
        driver_only = toolchain_runtime.compile_operand_roles(
            model["executions"][:1], model["profile"], model["driver"], complete=False,
        )
        writer_prefix = toolchain_runtime.compile_operand_roles(
            model["executions"][:2], model["profile"], model["driver"], complete=False,
        )
        self.assertEqual(driver_only, toolchain_runtime.CompileRoles(1, None, None, None, None))
        self.assertEqual(writer_prefix.writer_sequence, 2)
        self.assertIsNone(writer_prefix.reader_sequence)
        self.assertEqual(model["executions"][1]["argv"].count("-quiet"), 2)
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.compile_operand_roles(
                model["executions"][:2], model["profile"], model["driver"], complete=True,
            )

    def test_declared_separate_and_attached_child_options_remain_meaningful(self):
        model = self.model()
        rows = copy.deepcopy(model["executions"])
        output_index = rows[1]["argv"].index("-o")
        rows[1]["argv"][output_index:output_index] = [
            "-dumpbase-ext", ".c", "-mabi=apcs-gnu", "-fno-pic",
        ]
        input_index = len(rows[2]["argv"]) - 1
        rows[2]["argv"][input_index:input_index] = ["-mfpu=vfp"]
        roles = toolchain_runtime.compile_operand_roles(
            rows, model["profile"], rows[0]["argv"], complete=True,
        )
        self.assertEqual(roles.output.argv_index, model["roles"].output.argv_index + 4)
        self.assertEqual(roles.input.argv_index, model["roles"].input.argv_index + 1)
        changed = copy.deepcopy(rows)
        changed[1]["argv"][changed[1]["argv"].index("-dumpbase-ext") + 1] = ".ii"
        self.assertNotEqual(
            toolchain_runtime.encoded(rows), toolchain_runtime.encoded(changed),
        )

    def test_role_grammar_rejects_unknown_arity_positions_and_path_reuse(self):
        mutations = {
            "duplicate-cc1-output": lambda rows: rows[1]["argv"].extend(
                ["-o", "/work/ccL3VdjV.s"]
            ),
            "extra-cc1-positional": lambda rows: rows[1]["argv"].append("extra.c"),
            "response-file": lambda rows: rows[1]["argv"].append("@response"),
            "unknown-option": lambda rows: rows[1]["argv"].insert(1, "-pipe"),
            "empty-attached": lambda rows: rows[1]["argv"].insert(1, "-mcpu="),
            "non-string": lambda rows: rows[1]["argv"].append(7),
            "duplicate-as-output": lambda rows: rows[2]["argv"].extend(["-o", "/dev/null"]),
            "foreign-as-output": lambda rows: rows[2]["argv"].__setitem__(6, "/work/out.o"),
            "foreign-as-input": lambda rows: rows[2]["argv"].__setitem__(7, "/work/other.s"),
            "path-used-as-dumpbase": lambda rows: rows[1]["argv"].__setitem__(
                rows[1]["argv"].index("-dumpbase") + 1, "/work/ccL3VdjV.s"
            ),
            "too-many-argv": lambda rows: rows[1]["argv"].__setitem__(
                slice(1, 1), ["-quiet"] * toolchain_runtime.COMPILE_ARG_LIMIT
            ),
            "oversized-token": lambda rows: rows[1]["argv"].insert(1, "-D" + "X" * 4096),
        }
        model = self.model()
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                rows = copy.deepcopy(model["executions"])
                mutate(rows)
                with self.assertRaises(MakeProbeError):
                    toolchain_runtime.compile_operand_roles(
                        rows, model["profile"], rows[0]["argv"], complete=True,
                    )

    def test_exact_model_receipt_validates_and_is_canonical_immutable_bytes(self):
        model = self.model()
        charges = []
        canonical = self.parse(model, reserve=charges.append)
        self.assertIs(type(canonical), bytes)
        self.assertEqual(canonical, toolchain_runtime.encoded(model["receipt"]))
        self.assertGreaterEqual(len(charges), 4)
        self.assertTrue(all(type(value) is int and value > 0 for value in charges))
        self.assertEqual(charges[-1], len(canonical))
        reordered = {key: model["receipt"][key] for key in reversed(model["receipt"])}
        self.assertEqual(self.parse(model, receipt=reordered), canonical)

    def test_receipt_duplicate_fields_extras_and_foreign_stage_never_validate(self):
        model = self.model()
        payload = json.dumps(model["receipt"], separators=(",", ":"))
        duplicate = toolchain_runtime.INTERMEDIATE_PREFIX + payload.replace(
            '"version":1', '"version":1,"version":1', 1,
        )
        with self.assertRaises(MakeProbeError):
            self.parse(model, values=[duplicate])
        extra = copy.deepcopy(model["receipt"])
        extra["grant"] = True
        with self.assertRaises(MakeProbeError):
            self.parse(model, receipt=extra)
        with self.assertRaises(MakeProbeError):
            self.parse(model, values=[self.wire(model), self.wire(model)])
        foreign = copy.deepcopy(model)
        foreign["profile"]["stage"] = 3
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.intermediate_record(
                [self.wire(model)], profile=foreign["profile"], launch=model["launch"],
                executions=model["executions"], returncode=0, limits=model["limits"],
            )
        self.assertIsNone(toolchain_runtime.intermediate_record(
            [], profile=foreign["profile"], launch=model["launch"],
            executions=(), returncode=0, limits=model["limits"],
        ))
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.intermediate_record(
                [self.wire(model)], profile=model["profile"], launch=model["launch"],
                executions=model["executions"], returncode=1, limits=model["limits"],
            )

    def test_receipt_type_role_actor_and_success_result_matrix_rejects(self):
        mutations = (
            ("bool-order", ("creation", "order"), True),
            ("fd-bound", ("creation", "fd"), 128),
            ("failed-create", ("creation", "result"), -1),
            ("wrong-actor", ("writer", "pid"), 401),
            ("wrong-index", ("reader", "operand", "argv_index"), 6),
            ("wrong-exec-digest", ("actors", 1, "exec_record_sha256"), "c" * 64),
            ("failed-writer-exit", ("writer", "exit", "result"), 1),
            ("false-complete", ("complete",), False),
            ("foreign-binding", ("binding",), "d" * 64),
            ("preexisting-size", ("creation", "identity", 3), 1),
        )
        model = self.model()
        for name, path, value in mutations:
            with self.subTest(name=name):
                receipt = copy.deepcopy(model["receipt"])
                current = receipt
                for component in path[:-1]:
                    current = current[component]
                current[path[-1]] = value
                with self.assertRaises(MakeProbeError):
                    self.parse(model, receipt=receipt)

    def test_receipt_content_order_and_retirement_progression_are_independent(self):
        mutations = (
            (("writer", "completed", "written_bytes"), 63),
            (("reader", "completed", "read_bytes"), 63),
            (("reader", "completed", "sha256"), "e" * 64),
            (("reader", "open", "identity", 1), 99),
            (("writer", "completed", "order"), 4),
            (("retirement", "path_absent"), False),
            (("retirement", "after_identity", 5), 1002),
            (("retirement", "after_identity", 6), 1),
        )
        model = self.model()
        baseline = self.parse(model)
        for path, value in mutations:
            receipt = copy.deepcopy(model["receipt"])
            current = receipt
            for component in path[:-1]:
                current = current[component]
            current[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(MakeProbeError):
                self.parse(model, receipt=receipt)
            current[path[-1]] = copy.deepcopy(
                self.value_at(model["receipt"], path)
            )
            self.assertEqual(self.parse(model, receipt=receipt), baseline)

    @staticmethod
    def value_at(value, path):
        for component in path:
            value = value[component]
        return value

    def test_retirement_check_removal_exposes_the_targeted_model_adversary_then_restores(self):
        model = self.model()
        receipt = copy.deepcopy(model["receipt"])
        receipt["retirement"]["after_identity"][0] += 1
        with self.assertRaises(MakeProbeError):
            self.parse(model, receipt=receipt)
        with patch.object(toolchain_runtime, "_retirement_progression", return_value=True):
            self.assertIs(type(self.parse(model, receipt=receipt)), bytes)
        with self.assertRaises(MakeProbeError):
            self.parse(model, receipt=receipt)

    def test_receipt_pre_growth_wire_node_depth_and_issued_bounds_reject(self):
        model = self.model()
        oversized = toolchain_runtime.INTERMEDIATE_PREFIX + '{"path":"' + "x" * 65536 + '"}'
        deep = toolchain_runtime.INTERMEDIATE_PREFIX + "[" * 11 + "0" + "]" * 11
        nodes = toolchain_runtime.INTERMEDIATE_PREFIX + "[" + ",".join("0" for _ in range(513)) + "]"
        for name, value in (("wire", oversized), ("depth", deep), ("nodes", nodes)):
            with self.subTest(name=name), self.assertRaises(MakeProbeError):
                self.parse(model, values=[value])
        small = copy.deepcopy(model)
        small["limits"] = replace(model["limits"], observation_limit=len(self.wire(model)) - 1)
        with self.assertRaises(MakeProbeError):
            self.parse(small)
        small = copy.deepcopy(model)
        small["limits"] = replace(model["limits"], file_limit=63)
        with self.assertRaises(MakeProbeError):
            self.parse(small)
        long_path = "/work/" + "x" * 4091
        with self.assertRaises(MakeProbeError):
            self.model(long_path)

    def test_reservation_refusal_precedes_json_container_growth(self):
        model = self.model()
        calls = []

        def refuse(size):
            calls.append(size)
            raise MakeProbeError("model admission refused")

        with patch.object(toolchain_runtime.json, "loads", side_effect=AssertionError("decoded")):
            with self.assertRaisesRegex(MakeProbeError, "model admission refused"):
                self.parse(model, reserve=refuse)
        self.assertEqual(len(calls), 1)

    def test_projection_replaces_only_two_roles_and_preserves_raw_model_data(self):
        model = self.model()
        raw = self.probes(model)
        before = copy.deepcopy(raw)
        receipt = self.parse(model)
        projected = toolchain_runtime.project_compile_identity(raw, receipt, model["roles"])
        self.assertEqual(raw, before)
        self.assertEqual(set(projected), {"runtime_probes", "toolchain_semantics"})
        reference = {
            "kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly",
        }
        self.assertEqual(projected["runtime_probes"][1]["argv"][20], reference)
        self.assertEqual(projected["runtime_probes"][2]["argv"][7], reference)
        changed = [
            (row_index, argv_index)
            for row_index, (old, new) in enumerate(zip(raw, projected["runtime_probes"]))
            if "argv" in old
            for argv_index, (left, right) in enumerate(zip(old["argv"], new["argv"]))
            if left != right
        ]
        self.assertEqual(changed, [(1, 20), (2, 7)])
        summary, = projected["toolchain_semantics"]["intermediates"]
        self.assertEqual((summary["mode"], summary["bytes"], summary["sha256"]), (0o600, 64, "a" * 64))
        self.assertTrue(all(summary[name] for name in (
            "created", "writer_completed", "reader_completed", "retired",
        )))

    def test_retained_name_only_raw_failure_projects_to_equal_role_data(self):
        first = self.model("/work/ccL3VdjV.s")
        second = self.model("/work/ccBRFQFs.s")
        first_raw, second_raw = self.probes(first), self.probes(second)
        self.assertNotEqual(
            toolchain_runtime.encoded(first_raw), toolchain_runtime.encoded(second_raw),
        )
        first_projected = toolchain_runtime.project_compile_identity(
            first_raw, self.parse(first), first["roles"],
        )
        second_projected = toolchain_runtime.project_compile_identity(
            second_raw, self.parse(second), second["roles"],
        )
        self.assertEqual(first_projected, second_projected)

    def test_projection_keeps_meaningful_runtime_content_and_outer_facts_unequal(self):
        model = self.model()
        receipt = self.parse(model)
        baseline = toolchain_runtime.project_compile_identity(
            self.probes(model), receipt, model["roles"],
        )
        mutations = []
        environment = list(self.probes(model))
        environment[1]["environment"]["LANG"] = "C.UTF-8"
        mutations.append(("environment", environment, receipt, model["roles"]))
        identity = list(self.probes(model))
        identity[2]["identity"][1] += 1
        mutations.append(("executable-identity", identity, receipt, model["roles"]))
        stdin = list(self.probes(model))
        stdin[3]["stdin"] += " "
        mutations.append(("stdin", stdin, receipt, model["roles"]))
        ordered = list(self.probes(model))
        ordered[1]["argv"][1], ordered[1]["argv"][4] = ordered[1]["argv"][4], ordered[1]["argv"][1]
        ordered_roles = toolchain_runtime.compile_operand_roles(
            ordered[:3], model["profile"], ordered[0]["argv"], complete=True,
        )
        mutations.append(("non-role-order", ordered, receipt, ordered_roles))
        content_model = copy.deepcopy(model)
        content_model["receipt"]["writer"]["completed"]["sha256"] = "f" * 64
        content_model["receipt"]["reader"]["completed"]["sha256"] = "f" * 64
        content = self.parse(content_model)
        mutations.append(("assembly-content", self.probes(model), content, model["roles"]))
        for name, raw, selected_receipt, roles in mutations:
            with self.subTest(name=name):
                self.assertNotEqual(
                    toolchain_runtime.project_compile_identity(raw, selected_receipt, roles),
                    baseline,
                )
        outer = {
            **baseline, "returncode": 0, "stdout_sha256": "1" * 64,
            "publication": {"policy": "none"}, "header_kernel": {"digest": "2" * 64},
        }
        for field, value in (
            ("returncode", 1), ("stdout_sha256", "3" * 64),
            ("publication", {"policy": "replace"}), ("header_kernel", {"digest": "4" * 64}),
        ):
            changed = copy.deepcopy(outer)
            changed[field] = value
            self.assertNotEqual(changed, outer)

    def test_projection_dictionary_order_is_semantic_preserving_but_operand_mismatch_rejects(self):
        model = self.model()
        raw = self.probes(model)
        reordered = tuple(
            dict(reversed(list(row.items()))) if type(row) is dict else row for row in raw
        )
        receipt = self.parse(model)
        self.assertEqual(
            toolchain_runtime.project_compile_identity(raw, receipt, model["roles"]),
            toolchain_runtime.project_compile_identity(reordered, receipt, model["roles"]),
        )
        changed = list(copy.deepcopy(raw))
        changed[1]["argv"][model["roles"].output.argv_index] = "/work/other.s"
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.project_compile_identity(changed, receipt, model["roles"])


class ModernToolchainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        modern = (foundation.ROOT / "modern.mk").read_text()
        start = modern.index("expansion-modern-toolchain-check:\n")
        self.original_recipe = modern[start:modern.index("\nexpansion-modern-cohort:", start)]
        start = modern.index("$(MODERN_ALL_C_HEADER_DEPS): $(MODERN_OUTPUT_DIR)/%.headers.d: %.c\n")
        self.header_rule = modern[start:modern.index("\nexpansion-modern-clean:", start)]
        self.prerequisite, = [
            line for line in modern.splitlines()
            if line.startswith("$(MODERN_ALL_C_HEADER_DEPS): |")
        ]
        for path in sorted((foundation.ROOT / "include").rglob("*.h")):
            self.fixture.add(path.relative_to(foundation.ROOT).as_posix(), path.read_bytes())
        self.fixture.add("src/query.c", '#include "global.h"\n')
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := expansion-modern-all\n"
            "MODERN_OUTPUT_DIR := build/native\n"
            "MODERN_CC := arm-none-eabi-gcc\n"
            "MODERN_CONFIG := release\n"
            "MODERN_ABI := aapcs\n"
            "MODERN_BINUTILS_FLAG :=\n"
            "MODERN_DRIVER_FLAGS := -isystem /usr/include/newlib\n"
            "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork\n"
            "MODERN_LANGUAGE_FLAGS := -std=gnu11 -fgnu89-inline\n"
            "MODERN_DEFINE_FLAGS := -DMODERN=1 -DNONMATCHING=1 -DBUGFIX=1\n"
            "MODERN_INCLUDE_FLAGS := -Iinclude -I.\n"
            "MODERN_ABI_FLAGS :=\n"
            "MODERN_CFLAGS = $(MODERN_DRIVER_FLAGS) $(MODERN_ARCH_FLAGS) $(MODERN_LANGUAGE_FLAGS) "
            "$(MODERN_DEFINE_FLAGS) $(MODERN_INCLUDE_FLAGS)\n"
            "MODERN_ALL_C_HEADER_DEPS := build/native/src/query.headers.d\n"
            "MODERN_ALL_SOURCE_GOALS := expansion-modern-all\n"
            "MODERN_GENERATED_HEADER_BASENAME_RE := missing\\.h\n"
            + self.prerequisite + "\n" + self.original_recipe + "\n" + self.header_rule
            + "\nexpansion-modern-all: ;\n"
        ))
        data = json.loads((foundation.ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        self.contracts = {item["expression"]: item for item in data["contracts"]}

    def tearDown(self):
        self.fixture.tearDown()

    def session(self):
        return self.fixture.session(runtime_files=ROOT_RUNTIME_FILES)

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertFalse(session.budget.producer_waiters)
        self.assertFalse(session._toolchain.commands)
        self.assertFalse(session._toolchain.steps)
        self.assertFalse(session._toolchain.launches)
        self.assertFalse(session._toolchain.issued)
        self.assertIsNone(session._toolchain.active)
        self.assertFalse(session._header_launches)
        self.assertFalse(session._issued_header_launches)

    def ordinary(self):
        return subprocess.run(
            ["/usr/bin/make", "-rR", "--no-print-directory", toolchain_runtime.TARGET],
            cwd=self.fixture.root, env={**ENVIRONMENT, "TMPDIR": str(self.fixture.directory)},
            capture_output=True, timeout=20,
        )

    def source(self):
        return (self.fixture.root / "Makefile").read_text()

    def make(self, session):
        return session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))

    def capture(self, session):
        results = []
        execute = session._toolchain.execute
        def record(command):
            result = execute(command)
            results.append(result)
            return result
        with patch.object(session._toolchain, "execute", record):
            observed = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
        self.assertTrue(results)
        self.assertEqual(len(results), len([
            row for row in observed.semantics["native_dispatches"]
            if row["job"]["target"] == toolchain_runtime.TARGET
        ]))
        for result in results:
            self.assertEqual(
                (result.stdout, result.stderr, result.returncode, result.executed, result.input_identities),
                (results[0].stdout, results[0].stderr, results[0].returncode,
                 results[0].executed, results[0].input_identities),
            )
        self.results = tuple(results)
        return observed, results[0]

    def test_original_recipe_executes_real_driver_frontend_assembler_and_immutable_inputs(self):
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 0, expected.stderr)
        with self.session() as session:
            before = {
                name: ((session.tree / name).read_bytes(), (session.tree / name).stat())
                for name in session.snapshot.files
            }
            self.assertFalse((session.tree / "build").exists())
            observed, result = self.capture(session)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (expected.returncode, expected.stdout, expected.stderr))
            self.assertIn(expected.stdout, observed.stdout)
            self.assertIn("include/global.h", result.code_consumed)
            self.assertEqual(result.input_identities, tuple(session.snapshot.owners(result.code_consumed)))
            self.assertTrue(any(row[0].endswith("/stdint.h") for row in result.runtime_sources))
            self.assertEqual([Path(path).name for path in result.executed],
                             ["arm-none-eabi-gcc"] * 4 + ["cc1", "arm-none-eabi-gcc", "cc1", "as"])
            self.assertEqual(
                [row["stdin"] for row in result.runtime_probes if "stdin" in row],
                [toolchain_runtime.SYNTAX_INPUT, toolchain_runtime.COMPILE_INPUT],
            )
            self.assertTrue(all(row["eof"] for row in result.runtime_probes if "stdin" in row))
            for row in result.runtime_probes:
                if "identity" in row:
                    info = Path(row["path"]).stat()
                    self.assertEqual(row["identity"], [
                        info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                    ])
            for name, (data, info) in before.items():
                self.assertEqual((session.tree / name).stat(), info, name)
                self.assertEqual((session.tree / name).read_bytes(), data, name)
            self.assertEqual(len(observed.generated), 1)
            self.assertEqual(observed.generated[0].path, "build/native/src/query.headers.d")
        self.assert_clean(session)

    def test_supported_debug_and_abi_variants_preserve_real_results(self):
        base = self.source()
        for config, abi in (("debug", "apcs-gnu"), ("release", "aapcs")):
            source = base.replace("MODERN_CONFIG := release", "MODERN_CONFIG := " + config)
            source = source.replace("MODERN_ABI := aapcs", "MODERN_ABI := " + abi)
            source = source.replace("MODERN_ABI_FLAGS :=\n", "MODERN_ABI_FLAGS := -mabi=" + abi + "\n")
            self.fixture.add("Makefile", source)
            with self.subTest(config=config, abi=abi):
                expected = self.ordinary()
                self.assertEqual(expected.returncode, 0, expected.stderr)
                with self.session() as session:
                    _, result = self.capture(session)
                    self.assertEqual((result.returncode, result.stdout, result.stderr),
                                     (expected.returncode, expected.stdout, expected.stderr))
                    for row in result.runtime_probes:
                        if row.get("stage") == "compile" and row.get("sequence") == 1:
                            self.assertIn("-mabi=" + abi, row["argv"])
                    self.assertIn(Path(result.executed[-1]).name, {"as", "arm-none-eabi-as"})
                self.assert_clean(session)

    def test_genuine_immutable_view_reuses_headers_without_borrowing_prior_authority(self):
        from scripts.validation_ownership.authority import AuthorityLoader
        from scripts.validation_ownership.budget import ProbeBudget
        from scripts.validation_ownership.make_probe import ProbeSession

        budget = ProbeBudget()
        entries, revision = self.fixture.capture_tree(budget)
        loader = AuthorityLoader(self.fixture.root, entries, revision, budget=budget)
        with ProbeSession(loader, scratch_root=self.fixture.scratch, budget=budget, runtime_files=ROOT_RUNTIME_FILES) as session:
            original = session.tree
            with session.select_view(loader):
                self.assertIn("include/global.h", session.snapshot.reused_paths)
                source = session.tree / "include/global.h"
                self.assertGreater(source.stat().st_nlink, 1)
                self.assertEqual(source.stat().st_ino, (original / "include/global.h").stat().st_ino)
                before = source.read_bytes(), source.stat()
                _, result = self.capture(session)
                self.assertEqual(result.returncode, 0)
                self.assertEqual((source.read_bytes(), source.stat()), before)
                self.assertEqual(result.input_identities, tuple(session.snapshot.owners(result.code_consumed)))
            self.assertEqual(session.tree, original)
            self.assertFalse(session._toolchain.commands)
            self.assertFalse(session._toolchain.launches)
        self.assert_clean(session)

    def test_actual_B_selected_host_assembler_is_never_authorized_as_arm(self):
        base = self.source()
        for directory in ("/usr/bin", "/bin"):
            source = base.replace("MODERN_BINUTILS_FLAG :=\n", f"MODERN_BINUTILS_FLAG := -B{directory}/\n")
            source = source.replace("MODERN_DRIVER_FLAGS := ", "MODERN_DRIVER_FLAGS := $(MODERN_BINUTILS_FLAG) ")
            self.fixture.add("Makefile", source)
            selected = subprocess.run(
                ["/usr/bin/arm-none-eabi-gcc", "-B" + directory + "/", "-print-prog-name=as"],
                env=ENVIRONMENT, capture_output=True, timeout=10, check=True,
            ).stdout
            actual = Path(selected.decode().strip()).resolve(strict=True)
            expected = self.ordinary()
            with self.subTest(binutils=directory), self.session() as session:
                command, queries = session._command, []
                def record(value, **options):
                    step = session._toolchain.steps.get(id(value))
                    result = command(value, **options)
                    if step is not None and step.stage == 2:
                        queries.append(result)
                    return result
                with patch.object(session, "_command", record):
                    if str(actual) in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"}:
                        _, result = self.capture(session)
                        self.assertEqual((result.stdout, result.stderr, result.returncode),
                                         (expected.stdout, expected.stderr, expected.returncode))
                    else:
                        self.assertEqual(expected.returncode, 2)
                        with self.assertRaisesRegex(MakeProbeError, "unsupported target assembler") as failure:
                            self.make(session)
                        self.assertIn(str(actual), str(failure.exception))
                self.assertTrue(queries)
                self.assertEqual(queries[0].stdout, selected)
                self.assertEqual(queries[0].executed, ("/usr/bin/arm-none-eabi-gcc",))
            self.assert_clean(session)

    def test_ordered_macros_includes_and_success_stderr_are_actual_compiler_behavior(self):
        global_header = (self.fixture.root / "include/global.h").read_bytes()
        self.fixture.add("include/global.h", b'#if TC_CHOICE == 2\n#include <choice.h>\n#endif\n' + global_header)
        self.fixture.add("first/choice.h", '#warning "selected first header"\n')
        self.fixture.add("second/choice.h", '#error "wrong include precedence"\n')
        self.fixture.add("Makefile", self.source().replace(
            "-Iinclude -I.", "-Ifirst -Isecond -Iinclude -I. -DTC_CHOICE=1 -UTC_CHOICE -D TC_CHOICE=2",
        ))
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 0, expected.stderr)
        self.assertTrue(expected.stderr)
        with self.session() as session:
            observed, result = self.capture(session)
            self.assertEqual((result.stdout, result.stderr), (expected.stdout, expected.stderr))
            self.assertIn("first/choice.h", result.code_consumed)
            self.assertNotIn("second/choice.h", result.code_consumed)
            self.assertIn(result.stderr, observed.stderr)
        self.assert_clean(session)

    def test_removing_only_adapter_restores_original_native_refusal_then_restoration_succeeds(self):
        register = MakeCommands._register
        def absent(commands, command, contract):
            if contract["id"] == toolchain_runtime.CONTRACT:
                contract = {**contract, "id": "missing-toolchain-adapter"}
            return register(commands, command, contract)
        with self.session() as session:
            with patch.object(MakeCommands, "_register", absent):
                with self.assertRaisesRegex(MakeProbeError, "unconsumed active shell syntax"):
                    self.make(session)
            self.assertFalse(session.runtime_query_profiles)
        self.assert_clean(session)
        with self.session() as session:
            _, result = self.capture(session)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.executed)
        self.assert_clean(session)

    def test_complete_original_recipe_and_effective_flags_fail_closed_before_toolchain_launch(self):
        base = self.source()
        mutations = {
            "wrong-driver": base.replace("MODERN_CC := arm-none-eabi-gcc", "MODERN_CC := /usr/bin/cc"),
            "failure-branch": base.replace("exit 1;", "exit 0;", 1),
            "target-test": base.replace('if [ "$$target" != arm-none-eabi ]', 'if [ "$$target" = arm-none-eabi ]'),
            "syntax-stdin": base.replace("""'#include "global.h"'""", """'#include "prelude.h"'"""),
            "compile-stdin": base.replace("void modern_arm7tdmi_thumb_probe(void) {}", "void another_probe(void) {}"),
            "extra-command": base.replace("cc='$(MODERN_CC)';", "cc='$(MODERN_CC)'; /usr/bin/touch build/marker;"),
        }
        for flag in (
            "-fplugin=include/global.h", "-specs=include/global.h", "--sysroot=/usr",
            "-Binclude/", "-I/usr/include", "-include include/global.h", "-Wp,-I,/usr/include",
            "@include/global.h", "-std=gnu89", "-fsyntax-only -o build/marker",
        ):
            mutations["flag-" + flag] = base.replace(
                "MODERN_LANGUAGE_FLAGS := -std=gnu11 -fgnu89-inline",
                "MODERN_LANGUAGE_FLAGS := -std=gnu11 -fgnu89-inline " + flag,
            )
        for label, source in mutations.items():
            if getattr(self, "recipe_controls", None) is not None and label not in self.recipe_controls:
                continue
            self.fixture.add("Makefile", source)
            with self.subTest(defect=label), self.session() as session:
                run, launched = session._sandbox_run, []
                def counted(root, **options):
                    if "toolchain_launch" in options:
                        launched.append(True)
                    return run(root, **options)
                with patch.object(session, "_sandbox_run", counted), self.assertRaises(MakeProbeError):
                    self.make(session)
                self.assertFalse(launched)
                self.assertFalse((self.fixture.root / "build/marker").exists())
            self.assert_clean(session)

    def test_original_exported_search_and_loader_environment_cannot_grant_execution(self):
        base = self.source()
        for name, value in (
            ("CPATH", "include"), ("C_INCLUDE_PATH", "include"), ("GCC_EXEC_PREFIX", "/usr/bin/"),
            ("COMPILER_PATH", "/usr/bin"), ("LIBRARY_PATH", "/usr/lib"),
            ("DEPENDENCIES_OUTPUT", "build/marker"), ("LD_PRELOAD", "/foreign.so"),
            ("TMPDIR", "build/foreign"), ("PATH", "/bin:/usr/bin"),
        ):
            self.fixture.add("Makefile", f"export {name} := {value}\n" + base)
            with self.subTest(variable=name), self.session() as session:
                with self.assertRaises(MakeProbeError):
                    self.make(session)
                self.assertFalse(session.runtime_query_profiles)
            self.assert_clean(session)

    def test_only_issued_actual_launches_accept_original_arguments_inputs_and_lifetime(self):
        for defect in getattr(self, "launch_controls", (
            "missing", "copied", "forged", "replay", "closed", "epoch", "argv", "environment",
            "stdin", "executables", "sdk", "mount", "code", "workspace", "directory-object", "private-entry",
        )):
            with self.subTest(defect=defect), self.session() as session:
                run, reached = session._sandbox_run, []
                def changed(root, **options):
                    if "toolchain_launch" not in options or reached:
                        return run(root, **options)
                    reached.append(defect)
                    before = session.budget.runs
                    if defect == "replay":
                        result = run(root, **options)
                        before = session.budget.runs
                        with self.assertRaises(MakeProbeError):
                            run(root, **options)
                        self.assertEqual(session.budget.runs, before)
                        return result
                    if defect == "missing":
                        options.pop("toolchain_launch")
                    elif defect == "copied":
                        options["toolchain_launch"] = copy.copy(options["toolchain_launch"])
                    elif defect == "forged":
                        options["toolchain_launch"] = object()
                    elif defect == "closed":
                        session._toolchain.close()
                    elif defect == "epoch":
                        session._expire_namespaces()
                    elif defect == "argv":
                        options["argv"] = ["/usr/bin/arm-none-eabi-gcc", "-dumpmachine"]
                    elif defect == "environment":
                        options["environment"] = {**options["environment"], "CPATH": "include"}
                    elif defect in {"stdin", "sdk"}:
                        options["dependency"] = copy.deepcopy(options["dependency"])
                        if defect == "stdin":
                            options["dependency"]["toolchain_probe"]["stdin"] = "int foreign;\n"
                        else:
                            options["dependency"]["runtime_files"].append("/etc/passwd")
                    elif defect == "executables":
                        options["executables"] = ("/usr/bin/printf",)
                    elif defect == "mount":
                        options["mounts"] = copy.deepcopy(options["mounts"])
                        options["mounts"][1]["writable"] = True
                    elif defect == "code":
                        options["code"] = ("include/global.h",)
                    elif defect == "workspace":
                        root = root.parent / "other-owned-root"
                    elif defect in {"directory-object", "private-entry"}:
                        output, = [Path(item["source"]) for item in options["mounts"] if item["target"] == "/work"]
                        if defect == "directory-object":
                            output.rename(output.with_name("previous-output"))
                            output.mkdir()
                        else:
                            (output / "foreign").write_bytes(b"unissued")
                    with self.assertRaises(MakeProbeError):
                        run(root, **options)
                    self.assertEqual(session.budget.runs, before)
                    raise MakeProbeError("expected actual toolchain launch denial")
                with patch.object(session, "_sandbox_run", changed):
                    if defect == "replay":
                        self.make(session)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "expected actual toolchain launch denial"):
                            self.make(session)
                self.assertEqual(reached, [defect])
            self.assert_clean(session)

    def test_copied_descriptors_and_foreign_session_tokens_are_not_compiler_authority(self):
        for arguments in (
            ("--version",), ("-fsyntax-only", "-x", "c", "-"),
            ("-c", "-x", "c", "-o", "/dev/null", "-"),
        ):
            with self.subTest(arguments=arguments), self.session() as session:
                tool = session.runtime_tool("/usr/bin/arm-none-eabi-gcc")
                before = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    session.command(Command((tool.path, *arguments), runtime_tool=tool))
                self.assertEqual(session.budget.runs, before)
            self.assert_clean(session)
        with self.session() as session:
            with self.assertRaises(MakeProbeError):
                session.command(Command((
                    "/usr/bin/python3", "-c",
                    "import ctypes;ctypes.CDLL(None).syscall(39,ctypes.c_ulonglong(0x564f4d4b00000009),0,0)",
                )))
        self.assert_clean(session)
        with self.session() as session:
            execute, checked = session._toolchain.execute, []
            def copied(command):
                before = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    session._command(copy.copy(command))
                with self.assertRaises(MakeProbeError):
                    execute(replace(command))
                self.assertEqual(session.budget.runs, before)
                checked.append(True)
                return execute(command)
            with patch.object(session._toolchain, "execute", copied):
                self.make(session)
            self.assertTrue(checked)
        self.assert_clean(session)
        foreign_tokens = []
        for attempt in range(2):
            with self.subTest(session=attempt), self.session() as session:
                run, checked = session._sandbox_run, []
                def foreign(root, **options):
                    if "toolchain_launch" not in options or checked:
                        return run(root, **options)
                    checked.append(True)
                    if attempt == 0:
                        foreign_tokens.append(options["toolchain_launch"])
                        return run(root, **options)
                    options["toolchain_launch"] = foreign_tokens[0]
                    before = session.budget.runs
                    with self.assertRaises(MakeProbeError):
                        run(root, **options)
                    self.assertEqual(session.budget.runs, before)
                    raise MakeProbeError("foreign session launch denied")
                with patch.object(session, "_sandbox_run", foreign):
                    if attempt == 0:
                        self.make(session)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "foreign session launch denied"):
                            self.make(session)
                self.assertTrue(checked)
            self.assert_clean(session)

    def test_native_compiler_rejects_changed_sdk_and_source_content_before_consumption(self):
        for defect in getattr(self, "input_controls", ("sdk", "source")):
            with self.subTest(defect=defect), self.session() as session:
                capture, changed = session._capture_header_sdk, []
                def corrupt(*args):
                    backing, profile = capture(*args)
                    if defect == "sdk":
                        index = next(i for i, row in enumerate(profile["roots"]) if row[0] == "/usr/include/newlib")
                        target = backing / str(index) / "stdint.h"
                    else:
                        target = session.tree / "include/global.h"
                    data = target.read_bytes()
                    index = data.index(b" ")
                    target.write_bytes(data[:index] + b"\t" + data[index + 1:])
                    changed.append(True)
                    return backing, profile
                with patch.object(session, "_capture_header_sdk", corrupt):
                    with self.assertRaises(MakeProbeError):
                        self.make(session)
                self.assertEqual(changed, [True])
            self.assert_clean(session)
        header = (self.fixture.root / "include/global.h").read_bytes()
        self.fixture.add("include/foreign.data", "#define NOT_ADMITTED_C_HEADER 1\n")
        for operand in ("foreign.data", "/etc/passwd"):
            self.fixture.add("include/global.h", f'#include "{operand}"\n'.encode() + header)
            with self.subTest(input=operand), self.session() as session:
                with self.assertRaises(MakeProbeError):
                    self.make(session)
            self.assert_clean(session)

    @contextmanager
    def native_runtime(self, changes):
        from scripts.validation_ownership import make_probe

        root = self.fixture.directory / ("native-runtime-" + str(len(list(self.fixture.directory.iterdir()))))
        root.mkdir()
        for path in make_probe.TRUSTED_ROOT.iterdir():
            if path.is_file() and path.suffix in {".py", ".c", ".h"}:
                data = path.read_bytes()
                if path.name in changes:
                    tree = ast.parse(data)
                    changes[path.name](tree)
                    data = (ast.unparse(tree) + "\n").encode()
                (root / path.name).write_bytes(data)
        with patch.object(make_probe, "TRUSTED_ROOT", root):
            yield

    @staticmethod
    def replace_function(tree, name, statements):
        candidates = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(candidates) != 1:
            raise AssertionError("mutation did not select exactly one function")
        candidates[0].body = ast.parse(statements).body
        ast.fix_missing_locations(tree)

    @staticmethod
    def remove_condition(tree, function, constant=None, attribute=None):
        owners = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function]
        if len(owners) != 1:
            raise AssertionError("mutation did not select one owning function")
        candidates = [
            node for node in ast.walk(owners[0]) if isinstance(node, ast.If) and any(
                isinstance(item, ast.Constant) and constant is not None and item.value == constant
                or isinstance(item, ast.Attribute) and attribute is not None and item.attr == attribute
                for item in ast.walk(node.test)
            )
        ]
        if len(candidates) != 1:
            raise AssertionError("mutation did not select one enforcement condition")
        candidates[0].test = ast.Constant(False)
        ast.fix_missing_locations(tree)

    @staticmethod
    def function_mutant(function, change):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        change(tree)
        namespace = dict(function.__globals__)
        exec(compile(tree, "<owned-toolchain-mutation>", "exec"), namespace)
        return namespace[function.__name__]

    def assert_native_image_control(self):
        with self.session() as session:
            run, changed = session.budget.run, []
            def replace_image(argv, **options):
                if argv and str(argv[-1]).endswith(".json") and not changed:
                    path = Path(argv[-1])
                    config = json.loads(path.read_bytes())
                    if "toolchain_runtime" in config:
                        profile = config["dependency"]["toolchain_probe"]
                        profile["driver_identity"][1] += 1
                        profile["images"][0][2] += 1
                        config["toolchain_runtime"]["binding"] = toolchain_runtime.launch_binding(config)
                        path.write_text(json.dumps(config))
                        changed.append(True)
                return run(argv, **options)
            with patch.object(session.budget, "run", replace_image), self.assertRaises(MakeProbeError):
                self.make(session)
            self.assertEqual(changed, [True])
        self.assert_clean(session)

    def test_native_exec_checks_the_actual_image_even_after_valid_wire_shape(self):
        self.assert_native_image_control()

    def assert_native_stdin_control(self, *, remove_guard=False):
        def changed(tree):
            self.replace_function(tree, "input_bytes", """
return profile["stdin"].replace('#include ', '#include\\t').encode("utf-8")
""")
        def missing_guard(tree):
            owners = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "leave"]
            candidates = [
                node for node in ast.walk(owners[0]) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "actual"
                and any(isinstance(item, ast.Name) and item.id == "expected" for item in ast.walk(node.test))
            ]
            if len(candidates) != 1:
                raise AssertionError("stdin mutation did not select one actual-byte check")
            candidates[0].test = ast.Constant(False)
            ast.fix_missing_locations(tree)
        changes = {"toolchain_runtime.py": changed}
        if remove_guard:
            changes["syscall_guard.py"] = missing_guard
        with self.native_runtime(changes), self.session() as session:
            read, reports = session.budget.read_bytes, []
            def capture(path, category):
                data = read(path, category)
                if category == "control" and path.name.startswith("report-"):
                    report = json.loads(data)
                    if any(
                        value.startswith(toolchain_runtime.EXEC_PREFIX)
                        and json.loads(value[len(toolchain_runtime.EXEC_PREFIX):])["stage"] == "syntax"
                        for value in report["accessed"]
                    ):
                        reports.append(report)
                return data
            with patch.object(session.budget, "read_bytes", capture), self.assertRaises(MakeProbeError):
                self.make(session)
            report, = reports
            self.assertFalse(report["ok"])
            self.assertNotIn("include/global.h", report["code_consumed"])
        self.assert_clean(session)

    def test_native_stdin_bytes_reject_before_the_frontend_can_consume_headers(self):
        self.assert_native_stdin_control()

    def test_independent_enforcement_mutations_fail_the_corresponding_regressions(self):
        checks = []

        def run_control(name, control, patches):
            case = ModernToolchainTests("test_original_recipe_executes_real_driver_frontend_assembler_and_immutable_inputs")
            case.setUp()
            try:
                with patches(case), self.assertRaises(AssertionError):
                    control(case)
                self.assertFalse(case.fixture.scratch.exists())
                checks.append(name)
            finally:
                case.tearDown()

        @contextmanager
        def recipe_patch(case):
            case.recipe_controls = {"failure-branch"}
            def changed(tree):
                self.replace_function(tree, "expect", """
nonlocal position
position += len(_shell_tokens(fragment, "toolchain grammar"))
""")
            value = self.function_mutant(toolchain_runtime.parse_recipe, changed)
            with patch.object(toolchain_runtime, "parse_recipe", value):
                yield
        run_control("complete-original-grammar",
                    lambda case: case.test_complete_original_recipe_and_effective_flags_fail_closed_before_toolchain_launch(),
                    recipe_patch)

        @contextmanager
        def capability_patch(case):
            case.launch_controls = ("missing",)
            def unissued(controller, token, config):
                return {
                    "version": 1, "scope": toolchain_runtime.launch_scope(config["root"]),
                    "binding": toolchain_runtime.launch_binding(config),
                }
            with patch.object(toolchain_runtime.Controller, "consume_launch", unissued):
                yield
        run_control("actual-one-shot-launch-authority",
                    lambda case: case.test_only_issued_actual_launches_accept_original_arguments_inputs_and_lifetime(),
                    capability_patch)

        @contextmanager
        def target_patch(case):
            changed = lambda tree: self.remove_condition(tree, "execute", constant=b"arm-none-eabi")
            value = self.function_mutant(toolchain_runtime.Controller.execute, changed)
            with patch.object(toolchain_runtime.Controller, "execute", value):
                yield
        run_control("actual-target-result", lambda case: case.test_captured_driver_changes_and_actual_target_result_adversary_never_succeed(),
                    target_patch)

        @contextmanager
        def receipt_patch(case):
            with patch.object(toolchain_runtime, "records", return_value=()):
                yield
        run_control("authenticated-executable-input-results", lambda case: case.assert_receipt_control("identity"), receipt_patch)

        @contextmanager
        def workspace_patch(case):
            case.launch_controls = ("directory-object",)
            changed = lambda tree: self.replace_function(tree, "verify_workspace", "pass")
            with case.native_runtime({"toolchain_runtime.py": changed}), patch.object(toolchain_runtime, "verify_workspace"):
                yield
        run_control("actual-owned-workspace", lambda case: case.test_only_issued_actual_launches_accept_original_arguments_inputs_and_lifetime(),
                    workspace_patch)

        @contextmanager
        def sdk_patch(case):
            case.input_controls = ("sdk",)
            changed = lambda tree: self.remove_condition(tree, "header_runtime_access", attribute="hexdigest")
            with case.native_runtime({"syscall_guard.py": changed}):
                yield
        run_control("actual-immutable-C-SDK", lambda case: case.test_native_compiler_rejects_changed_sdk_and_source_content_before_consumption(),
                    sdk_patch)

        @contextmanager
        def image_patch(case):
            changed = lambda tree: self.replace_function(tree, "verify_dependency_image", "pass")
            with case.native_runtime({"syscall_guard.py": changed}):
                yield
        run_control("actual-native-executable-image", lambda case: case.assert_native_image_control(), image_patch)

        @contextmanager
        def unchanged(case):
            yield
        run_control("stdin-before-frontend-effects", lambda case: case.assert_native_stdin_control(remove_guard=True), unchanged)

        @contextmanager
        def ignored_status_patch(case):
            from scripts.validation_ownership.make_probe import ProbeSession
            changed = lambda tree: self.remove_condition(tree, "_make", constant="toolchain_check")
            value = self.function_mutant(ProbeSession._make, changed)
            with patch.object(ProbeSession, "_make", value):
                yield
        run_control("failed-required-check-cannot-certify",
                    lambda case: case.test_ignored_real_recipe_failure_cannot_become_an_ownership_certificate(),
                    ignored_status_patch)
        self.assertEqual(len(checks), 9)
        self.mutation_results = checks

    def test_semantics_preserving_parser_local_rename_keeps_real_execution_green(self):
        def rename(tree):
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "tokens":
                    node.id = "parsed_words"
            ast.fix_missing_locations(tree)
        refactor = self.function_mutant(toolchain_runtime.parse_recipe, rename)
        with patch.object(toolchain_runtime, "parse_recipe", refactor):
            self.test_original_recipe_executes_real_driver_frontend_assembler_and_immutable_inputs()

    def test_real_syntax_failure_keeps_original_stderr_and_failed_make_status(self):
        header = (self.fixture.root / "include/global.h").read_bytes()
        self.fixture.add("include/global.h", b'#error "controlled original syntax failure"\n' + header)
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 2)
        with self.session() as session:
            execute, results = session._toolchain.execute, []
            def record(command):
                result = execute(command)
                results.append(result)
                return result
            with patch.object(session._toolchain, "execute", record):
                with self.assertRaisesRegex(MakeProbeError, "GNU Make failed after live producers: 2") as failure:
                    self.make(session)
            result, = results
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, expected.stdout)
            self.assertIn(result.stderr, expected.stderr)
            self.assertIn("controlled original syntax failure", str(failure.exception))
            self.assertNotIn("compile", [row["stage"] for row in result.runtime_probes])
            self.assertEqual([Path(path).name for path in result.executed],
                             ["arm-none-eabi-gcc"] * 4 + ["cc1"])
        self.assert_clean(session)

    def test_ignored_real_recipe_failure_cannot_become_an_ownership_certificate(self):
        header = (self.fixture.root / "include/global.h").read_bytes()
        self.fixture.add("include/global.h", b"int invalid_declaration = ;\n" + header)
        self.fixture.add("Makefile", ".IGNORE: expansion-modern-toolchain-check\n" + self.source())
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 0)
        self.assertTrue(expected.stderr)
        with self.session() as session:
            execute, failures = session._toolchain.execute, []
            def record(value):
                result = execute(value)
                failures.append(result.returncode)
                return result
            with patch.object(session._toolchain, "execute", record):
                with self.assertRaisesRegex(MakeProbeError, "required modern toolchain check failed"):
                    self.make(session)
            self.assertTrue(failures)
            self.assertEqual(set(failures), {1})
        self.assert_clean(session)

    def test_literal_quote_and_whitespace_refactors_preserve_recipe_behavior(self):
        self.fixture.add("Makefile", self.source().replace(
            "set -eu; \\", "set -eu;   \\",
        ).replace("exit 1;", "'exit' '1';").replace(
            "'$(MODERN_CONFIG)' '$(MODERN_ABI)'", '"$(MODERN_CONFIG)" "$(MODERN_ABI)"',
        ))
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 0, expected.stderr)
        with self.session() as session:
            _, result = self.capture(session)
            self.assertEqual((result.stdout, result.stderr, result.returncode),
                             (expected.stdout, expected.stderr, expected.returncode))
        self.assert_clean(session)

    def expanded_recipe(self):
        result = subprocess.run(
            ["/usr/bin/make", "-rR", "--no-print-directory", "-n", toolchain_runtime.TARGET],
            cwd=self.fixture.root, env=ENVIRONMENT, capture_output=True, text=True, timeout=10, check=True,
        )
        command, = normalize_bash_script_commands(result.stdout, "original fixture recipe")
        return command

    def shell_role(self, script, *arguments):
        return subprocess.run(
            ["/bin/sh", "-c", script, "lexical-role", *arguments], cwd=self.fixture.root,
            env=ENVIRONMENT, capture_output=True, timeout=5,
        )

    @staticmethod
    def token_variant(command, token, value):
        return command[:token.start] + value + command[token.end:]

    def test_shell_keywords_grouping_and_pipeline_roles_require_real_recognition(self):
        original = self.expanded_recipe()
        tokens = tokenize_bash_command(original)
        probes = {
            "if": ("if true; then printf yes; fi", "if"),
            "then": ("if true; then printf yes; fi", "then"),
            "fi": ("if true; then printf yes; fi", "fi"),
            "case": ("case as in *) printf yes;; esac", "case"),
            "in": ("case as in *) printf yes;; esac", "in"),
            "esac": ("case as in *) printf yes;; esac", "esac"),
            "{": ("{ printf yes; }", "{"),
            "}": ("{ printf yes; }", "}"),
            "!": ("if ! false; then printf yes; else printf no; fi", "!"),
        }
        for word, (script, needle) in probes.items():
            expected = self.shell_role(script)
            self.assertEqual((expected.returncode, expected.stdout), (0, b"yes"))
            for spelling in dict.fromkeys((
                "'" + word + "'", '"' + word + '"', "\\" + word, word + "''",
                word[:1] + "''" + word[1:],
            )):
                actual = self.shell_role(script.replace(needle, spelling, 1))
                self.assertNotEqual((actual.returncode, actual.stdout), (expected.returncode, expected.stdout))
                for index, token in enumerate(tokens):
                    if token.value != word or word == "!" and tokens[index - 1].value != "if":
                        continue
                    with self.subTest(role=word, spelling=spelling, occurrence=index), self.assertRaises(MakeProbeError):
                        toolchain_runtime.parse_recipe(self.token_variant(original, token, spelling))
        for token in tokens:
            if token.operator or token.io_number:
                with self.subTest(operator=token.value, position=token.start), self.assertRaises(MakeProbeError):
                    toolchain_runtime.parse_recipe(self.token_variant(original, token, "'" + token.value + "'"))

    def test_assignment_roles_preserve_unquoted_name_equals_and_quoted_values(self):
        original = self.expanded_recipe()
        token, = [item for item in tokenize_bash_command(original) if item.value == "cc=arm-none-eabi-gcc"]
        expected = toolchain_runtime.parse_recipe(original)
        valid = (
            "cc=arm-none-eabi-gcc", "cc='arm-none-eabi-gcc'", 'cc="arm-none-eabi-gcc"',
            "cc=''arm-none-eabi-gcc", "cc=arm-none-'eabi-gcc'", r"cc=arm-none-eabi-gc\c",
        )
        invalid = (
            "c''c=arm-none-eabi-gcc", "'cc'=arm-none-eabi-gcc", r"cc\=arm-none-eabi-gcc",
            "'cc=arm-none-eabi-gcc'", r"c\c=arm-none-eabi-gcc", "cc''=arm-none-eabi-gcc",
            "cc'='arm-none-eabi-gcc",
        )
        for spelling in (*valid, *invalid):
            with self.subTest(assignment=spelling):
                actual = self.shell_role("set -eu; " + spelling + "; printf '%s\\n' \"$cc\"")
                command = self.token_variant(original, token, spelling)
                if spelling in valid:
                    self.assertEqual((actual.returncode, actual.stdout), (0, b"arm-none-eabi-gcc\n"))
                    self.assertEqual(toolchain_runtime.parse_recipe(command), expected)
                else:
                    self.assertNotEqual(actual.returncode, 0)
                    with self.assertRaises(MakeProbeError):
                        toolchain_runtime.parse_recipe(command)

    def test_case_pattern_roles_preserve_active_wildcards_not_literal_quote_spelling(self):
        original = self.expanded_recipe()
        expected = toolchain_runtime.parse_recipe(original)
        tokens = tokenize_bash_command(original)
        values = ("/bin/as", "as", "/*", "*", "", "/x*")
        cases = (
            ("/*", ("/*", "'/'*", '"/"*', r"\/*", "/''*"), ("'/*'", '"/*"', r"/\*", "/'*'")),
            ("*", ("*", "''*", '""*'), ("'*'", '"*"', r"\*")),
        )
        for word, valid, invalid in cases:
            token, = [item for item in tokens if item.value == word]
            def shell(pattern):
                absolute, fallback = (pattern, "*") if word == "/*" else ("/*", pattern)
                return self.shell_role(
                    'for value do case "$value" in ' + absolute
                    + ') printf "absolute\\n";; ' + fallback + ') printf "other\\n";; esac; done',
                    *values,
                )
            baseline = shell(word)
            self.assertEqual(baseline.returncode, 0)
            for spelling in (*valid, *invalid):
                with self.subTest(pattern=word, spelling=spelling):
                    actual = shell(spelling)
                    self.assertEqual(actual.returncode, 0)
                    command = self.token_variant(original, token, spelling)
                    if spelling in valid:
                        self.assertEqual(actual.stdout, baseline.stdout)
                        self.assertEqual(toolchain_runtime.parse_recipe(command), expected)
                    else:
                        self.assertNotEqual(actual.stdout, baseline.stdout)
                        with self.assertRaises(MakeProbeError):
                            toolchain_runtime.parse_recipe(command)

    def test_test_command_argument_negation_is_not_pipeline_negation(self):
        original = self.expanded_recipe()
        expected = toolchain_runtime.parse_recipe(original)
        tokens = tokenize_bash_command(original)
        for spelling in ("'!'", r"\!", "''!", '"!"'):
            actual = self.shell_role("[ " + spelling + " -x /nonexistent ] && printf yes")
            self.assertEqual((actual.returncode, actual.stdout), (0, b"yes"))
            for index, token in enumerate(tokens):
                if token.value == "!" and tokens[index - 1].value == "[":
                    self.assertEqual(
                        toolchain_runtime.parse_recipe(self.token_variant(original, token, spelling)), expected,
                    )

    def assert_native_lexical_rejection(self, old, new):
        self.lexical_admission_witness = None
        source = self.source()
        self.assertIn(old, source)
        self.fixture.add("Makefile", source.replace(old, new, 1))
        try:
            with self.session() as session:
                run, execute, stages, outcomes = session._sandbox_run, session._toolchain.execute, [], []
                def record(root, **options):
                    if "toolchain_launch" in options:
                        stages.append(options["dependency"]["toolchain_probe"]["stage"])
                    return run(root, **options)
                def outcome(command):
                    result = execute(command)
                    outcomes.append(result.returncode)
                    return result
                with patch.object(session, "_sandbox_run", record), patch.object(session._toolchain, "execute", outcome):
                    with self.assertRaises(MakeProbeError):
                        observed = self.make(session)
                        self.lexical_admission_witness = {
                            "stages": stages, "outcomes": outcomes,
                            "generated": [item.path for item in observed.generated],
                            "stdout": observed.stdout.decode("utf-8"),
                            "stderr": observed.stderr.decode("utf-8"),
                        }
                self.assertEqual(stages, [])
                self.assertEqual(outcomes, [])
                self.assertFalse(session.published_sources)
            self.assert_clean(session)
        finally:
            self.fixture.add("Makefile", source)

    def test_native_malformed_lexical_roles_reject_before_any_compiler_substep(self):
        original = self.source()
        self.fixture.add("Makefile", original.replace('if [ -z "$$cc_path" ]', '\'if\' [ -z "$$cc_path" ]', 1))
        ordinary = self.ordinary()
        self.assertEqual(ordinary.returncode, 2)
        self.fixture.add("Makefile", original)
        for old, new in (
            ('if [ -z "$$cc_path" ]', '\'if\' [ -z "$$cc_path" ]'),
            ("; then \\", "; th'en' \\"),
            ("if ! printf", "if '!' printf"),
            ("cc='$(MODERN_CC)'", "'cc'='$(MODERN_CC)'"),
            ("|| { \\", "|| '{' \\"),
            ("/*) resolved_as=", "'/*') resolved_as="),
            ("\n\t\t*) resolved_as=", "\n\t\t'*') resolved_as="),
        ):
            with self.subTest(old=old, new=new):
                self.assert_native_lexical_rejection(old, new)

    def test_native_equivalent_argv_assignment_and_pattern_quoting_preserve_actual_results(self):
        expected = self.ordinary()
        self.assertEqual(expected.returncode, 0, expected.stderr)
        source = self.source().replace("cc='$(MODERN_CC)'", 'cc="$(MODERN_CC)"', 1)
        source = source.replace("[ ! -x", "[ '!' -x").replace("exit 1;", "'exit' '1';")
        source = source.replace("/*) resolved_as=", "'/'*) resolved_as=", 1)
        source = source.replace("\n\t\t*) resolved_as=", "\n\t\t''*) resolved_as=", 1)
        self.fixture.add("Makefile", source)
        ordinary = self.ordinary()
        self.assertEqual((ordinary.returncode, ordinary.stdout, ordinary.stderr),
                         (expected.returncode, expected.stdout, expected.stderr))
        with self.session() as session:
            before = {
                name: ((session.tree / name).read_bytes(), (session.tree / name).stat())
                for name in session.snapshot.files
            }
            observed, result = self.capture(session)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (ordinary.returncode, ordinary.stdout, ordinary.stderr))
            self.assertEqual(result.executed[-1], "/usr/lib/arm-none-eabi/bin/as")
            self.assertEqual(
                [row["stdin"] for row in result.runtime_probes if "stdin" in row],
                [toolchain_runtime.SYNTAX_INPUT, toolchain_runtime.COMPILE_INPUT],
            )
            self.assertEqual(result.input_identities, tuple(session.snapshot.owners(result.code_consumed)))
            self.assertTrue(result.runtime_sources)
            self.assertEqual(observed.generated[0].path, "build/native/src/query.headers.d")
            for name, (data, info) in before.items():
                self.assertEqual((session.tree / name).stat(), info, name)
                self.assertEqual((session.tree / name).read_bytes(), data, name)
        self.assert_clean(session)

    def test_restoring_only_old_role_erasure_recovers_native_false_admissions(self):
        def erase(tree):
            self.replace_function(tree, "signature", """
active = token.raw if "$" in token.raw or "`" in token.raw else None
return token.value, token.operator, token.io_number, active
""")
        mutant = self.function_mutant(toolchain_runtime.parse_recipe, erase)
        self.lexical_mutation_witnesses = []
        for old, new in (
            ('if [ -z "$$cc_path" ]', '\'if\' [ -z "$$cc_path" ]'),
            ("cc='$(MODERN_CC)'", "'cc'='$(MODERN_CC)'"),
            ("/*) resolved_as=", "'/*') resolved_as="),
        ):
            with self.subTest(old=old), patch.object(toolchain_runtime, "parse_recipe", mutant):
                with self.assertRaises(AssertionError):
                    self.assert_native_lexical_rejection(old, new)
            self.assertIsNotNone(self.lexical_admission_witness)
            self.assertEqual(self.lexical_admission_witness["stages"], list(range(5)) * 2)
            self.assertEqual(self.lexical_admission_witness["outcomes"], [0, 0])
            self.assertEqual(self.lexical_admission_witness["generated"], ["build/native/src/query.headers.d"])
            self.lexical_mutation_witnesses.append({"old": old, "new": new, **self.lexical_admission_witness})
            self.assertFalse(self.fixture.scratch.exists())

    def test_captured_driver_changes_and_actual_target_result_adversary_never_succeed(self):
        with self.session() as session:
            session.runtime_inputs = tuple(
                replace(item, data=b"!" + item.data[1:])
                if item.canonical == "/usr/bin/arm-none-eabi-gcc" else item
                for item in session.runtime_inputs
            )
            with self.assertRaisesRegex(MakeProbeError, "trusted runtime tool changed after capture"):
                self.make(session)
            self.assertFalse(session.runtime_query_profiles)
        self.assert_clean(session)
        with self.session() as session:
            command, execute, results, actual = session._command, session._toolchain.execute, [], []
            def wrong_target(value, **options):
                step = session._toolchain.steps.get(id(value))
                result = command(value, **options)
                if step is not None and step.stage == 1:
                    actual.append(result)
                    return replace(result, stdout=b"foreign-target\n")
                return result
            def record(value):
                result = execute(value)
                results.append(result)
                return result
            with patch.object(session, "_command", wrong_target), patch.object(session._toolchain, "execute", record):
                with self.assertRaises(MakeProbeError):
                    self.make(session)
            self.assertEqual(actual[0].stdout, b"arm-none-eabi\n")
            result, = results
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"error: modern compiler targets 'foreign-target'; expected 'arm-none-eabi'\n")
            self.assertEqual(result.executed, ("/usr/bin/arm-none-eabi-gcc",) * 2)
        self.assert_clean(session)

    def assert_receipt_control(self, defect):
        with self.session() as session:
            read, changed = session.budget.read_bytes, []
            def rewrite(path, category):
                data = read(path, category)
                if category != "control" or not path.name.startswith("report-") or changed:
                    return data
                report = json.loads(data)
                prefix = toolchain_runtime.INPUT_PREFIX if defect == "stdin" else toolchain_runtime.EXEC_PREFIX
                index = next((i for i, value in enumerate(report["accessed"]) if value.startswith(prefix)), None)
                if index is None or defect == "sdk" and not any(value.startswith("arm-header:") for value in report["accessed"]):
                    return data
                row = json.loads(report["accessed"][index][len(prefix):])
                if defect == "executed":
                    report["executed"] = []
                elif defect == "sdk":
                    report["accessed"] = [value for value in report["accessed"] if not value.startswith("arm-header:")]
                elif defect == "missing":
                    report["accessed"] = [value for value in report["accessed"] if not value.startswith(prefix)]
                else:
                    if defect == "identity":
                        row["identity"][1] += 1
                    elif defect == "argv":
                        row["argv"][-1] = "foreign"
                    elif defect == "environment":
                        row["environment"]["CPATH"] = "include"
                    elif defect == "stdin":
                        row["stdin"] = ""
                    elif defect != "ordering":
                        self.fail("unknown receipt control")
                    row = dict(reversed(list(row.items())))
                    report["accessed"][index] = prefix + json.dumps(row)
                changed.append(defect)
                return json.dumps(report).encode()
            with patch.object(session.budget, "read_bytes", rewrite):
                if defect == "ordering":
                    observed = self.make(session)
                    self.assertTrue(observed.generated)
                else:
                    with self.assertRaises(MakeProbeError):
                        self.make(session)
            self.assertEqual(changed, [defect])
        self.assert_clean(session)

    def test_authenticated_native_observations_cannot_be_omitted_or_forged(self):
        for defect in ("executed", "missing", "identity", "argv", "environment", "stdin", "sdk", "ordering"):
            with self.subTest(defect=defect):
                self.assert_receipt_control(defect)

    def test_exact_native_owner_runtime_and_indexed_human_case_are_retained(self):
        from scripts import check_docs
        from scripts.validation_ownership import ci_verifier, reporter, tests as ownership_tests
        from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
        from scripts.validation_ownership.budget import ProbeBudget
        from tests.workflows.test_ownership_probe import PROBE_TEST_MODULES, case_ids

        root = foundation.ROOT
        graph = reporter.load_json(root / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(root, budget=budget)
            sources = reporter._path_admission_sources(AuthorityLoader(root, entries, "HEAD", budget=budget), set())
            for path, expected in (
                ("scripts/validation_ownership/toolchain_runtime.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_toolchain_runtime.py", "paths.ownership-native"),
            ):
                rule, = [item for item in graph["path_rules"] if reporter._path_rule_matches(item, path, set())]
                self.assertEqual(rule["id"], expected)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    altered = {**rule, "include": [item for item in rule["include"] if item.get("path") != path]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, altered, sources)
        finally:
            budget.close()
        self.assertIn(__name__, PROBE_TEST_MODULES)
        discovered = set(case_ids(ownership_tests.load_tests(unittest.TestLoader(), unittest.TestSuite(), "test_*.py")))
        native = set(case_ids(unittest.defaultTestLoader.loadTestsFromName(__name__)))
        self.assertTrue(native)
        self.assertTrue(native.isdisjoint(discovered))
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case_id = "TC-WORKFLOW-OWNERSHIP-MODERN-TOOLCHAIN-001"
        case, = [item for item in registry["cases"] if item["id"] == case_id]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        self.assertIn(case_id, feature["required_cases"])
        self.assertEqual(case["issue_urls"], ["https://github.com/laqieer/fireemblem8-expansion/issues/180"])
        selected = {
            **registry, "features": [{**feature, "required_cases": [case_id]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_toolchain_runtime.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


if __name__ == "__main__":
    unittest.main()
