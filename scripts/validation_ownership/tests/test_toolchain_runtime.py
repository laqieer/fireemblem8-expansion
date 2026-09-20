"""Original modern toolchain recipe, live native authority and real ARM inputs."""

import ast
import copy
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import errno
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import stat
import textwrap
import tracemalloc
import subprocess
import threading
import time
from types import FunctionType, SimpleNamespace
import unittest
import weakref
from unittest.mock import patch

from scripts.bash_parser import normalize_bash_script_commands, tokenize_bash_command
from scripts.validation_ownership import toolchain_runtime
from scripts.validation_ownership import make_probe, syscall_guard
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import MakeCommands, ROOT_RUNTIME_FILES
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


class _MutationPreparationError(RuntimeError):
    """Invalid preparation is never an expected regression assertion."""


class _UnexpectedTargetStage(AssertionError):
    """A bound foreign target reached a later execution stage."""


def _mutation_function(tree, qualified):
    names = qualified.split(".")
    if len(names) == 1:
        candidates = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == qualified
        ]
    else:
        current = tree
        for name in names[:-1]:
            candidates = [
                node for node in current.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name
            ]
            if len(candidates) != 1:
                raise _MutationPreparationError("mutation has no unique owning scope: " + qualified)
            current = candidates[0]
        candidates = [
            node for node in current.body
            if isinstance(node, ast.FunctionDef) and node.name == names[-1]
        ]
    if len(candidates) != 1:
        raise _MutationPreparationError("mutation has no unique function: " + qualified)
    return candidates[0]


def _replace_mutation_function(tree, name, statements):
    _mutation_function(tree, name).body = ast.parse(statements).body
    ast.fix_missing_locations(tree)


def _remove_mutation_condition(tree, function, constant=None, attribute=None):
    owner = _mutation_function(tree, function)
    candidates = [
        node for node in ast.walk(owner) if isinstance(node, ast.If) and any(
            isinstance(item, ast.Constant) and constant is not None and item.value == constant
            or isinstance(item, ast.Attribute) and attribute is not None and item.attr == attribute
            for item in ast.walk(node.test)
        )
    ]
    if len(candidates) != 1:
        raise _MutationPreparationError("mutation has no unique enforcement condition")
    candidates[0].test = ast.Constant(False)
    ast.fix_missing_locations(tree)


def _disable_mutation_comparison(tree, function, expression):
    owner = _mutation_function(tree, function)
    expected = ast.dump(ast.parse(expression, mode="eval").body)
    selected = [node for node in ast.walk(owner) if isinstance(node, ast.Compare) and ast.dump(node) == expected]
    if len(selected) != 1:
        raise _MutationPreparationError("mutation has no unique bounded comparison")
    class Remove(ast.NodeTransformer):
        def visit_Compare(self, node):
            return ast.copy_location(ast.Constant(False), node) if node is selected[0] else self.generic_visit(node)
    Remove().visit(owner)
    ast.fix_missing_locations(tree)


def _remove_launch_membership(tree):
    _disable_mutation_comparison(tree, "consume_launch", "token not in self.issued")


def _remove_record_identity(tree):
    _disable_mutation_comparison(
        tree, "records", 'row["identity"] != profile["images"][row["sequence"] - 1][1:]',
    )


def _remove_policy_stdin_guard(tree):
    owner = _mutation_function(tree, "Policy.leave")
    selected = [
        node for node in ast.walk(owner) if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "actual"
        and any(isinstance(item, ast.Name) and item.id == "expected" for item in ast.walk(node.test))
    ]
    if len(selected) != 1:
        raise _MutationPreparationError("stdin mutation has no unique Policy actual-byte check")
    selected[0].test = ast.Constant(False)
    ast.fix_missing_locations(tree)


def _changed_stdin_input(tree):
    _replace_mutation_function(
        tree, "input_bytes",
        """return profile["stdin"].replace('#include ', '#include\\t').encode("utf-8")""",
    )


def _function_mutant(function, change):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    try:
        change(tree)
    except AssertionError as error:
        raise _MutationPreparationError("function mutation preflight failed") from error
    ast.fix_missing_locations(tree)
    namespace = dict(function.__globals__)
    exec(compile(tree, "<owned-toolchain-mutation>", "exec"), namespace)
    prepared = namespace[function.__name__]
    result = FunctionType(
        prepared.__code__, function.__globals__, function.__name__,
        prepared.__defaults__, prepared.__closure__,
    )
    result.__kwdefaults__ = prepared.__kwdefaults__
    return result


def _prepare_native_runtime(changes):
    prepared = {}
    for name, change in changes.items():
        if name not in {"toolchain_runtime.py", "syscall_guard.py"} or not callable(change):
            raise _MutationPreparationError("native mutation has an unowned source or transform")
        tree = ast.parse((make_probe.TRUSTED_ROOT / name).read_bytes())
        try:
            change(tree)
        except AssertionError as error:
            raise _MutationPreparationError("native mutation preflight failed: " + name) from error
        ast.fix_missing_locations(tree)
        compile(tree, name, "exec")
        prepared[name] = (ast.unparse(tree) + "\n").encode()
    return prepared


def _prepare_enforcement_mutations():
    def grammar(tree):
        _replace_mutation_function(tree, "expect", """
nonlocal position
position += len(_shell_tokens(fragment, "toolchain grammar"))
""")
    def workspace(tree):
        _replace_mutation_function(tree, "verify_workspace", "pass")
    return {
        "grammar": _function_mutant(toolchain_runtime.parse_recipe, grammar),
        "launch": _function_mutant(toolchain_runtime.Controller.consume_launch, _remove_launch_membership),
        "target": _function_mutant(
            toolchain_runtime.Controller.execute,
            lambda tree: _remove_mutation_condition(tree, "execute", constant=b"arm-none-eabi"),
        ),
        "records": _function_mutant(toolchain_runtime.records, _remove_record_identity),
        "workspace": (
            _prepare_native_runtime({"toolchain_runtime.py": workspace}),
            _function_mutant(toolchain_runtime.verify_workspace, workspace),
        ),
        "sdk": _prepare_native_runtime({
            "syscall_guard.py": lambda tree: _remove_mutation_condition(
                tree, "Policy.header_runtime_access", attribute="hexdigest",
            ),
        }),
        "image": _prepare_native_runtime({
            "syscall_guard.py": lambda tree: _replace_mutation_function(
                tree, "Policy.verify_dependency_image", "pass",
            ),
        }),
        "stdin": _prepare_native_runtime({
            "toolchain_runtime.py": _changed_stdin_input, "syscall_guard.py": _remove_policy_stdin_guard,
        }),
        "ignored-status": _function_mutant(
            make_probe.ProbeSession._make,
            lambda tree: _remove_mutation_condition(tree, "_make", constant="toolchain_check"),
        ),
    }


class ToolchainProtocolDataTests(unittest.TestCase):
    """Pure model-data controls for the dormant stage-4 protocol helpers."""

    def setUp(self):
        self.reservations = []

    def reserve(self, size):
        self.assertIs(type(size), int)
        self.assertGreater(size, 0)
        self.reservations.append(size)

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

    def parse(self, model, *, reserve, receipt=None, values=None):
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

    def test_role_grammar_rejects_intermediate_mentions_in_all_actor_environments(self):
        model = self.model()
        path = model["roles"].output.value
        aliases = (
            ("INTERMEDIATE_ALIAS", path),
            ("INTERMEDIATE_ALIAS", '"' + path + '"'),
            ("INTERMEDIATE_ALIAS", "/usr/bin:" + path + ":/bin"),
            (path, "value"),
            ("ALIAS_" + path, "value"),
        )
        for actor in range(3):
            for key, value in aliases:
                for count in ((2, 3) if actor < 2 else (3,)):
                    rows = copy.deepcopy(model["executions"][:count])
                    rows[actor]["environment"][key] = value
                    before = copy.deepcopy(rows)
                    with self.subTest(actor=actor, key=key, value=value, count=count):
                        with self.assertRaises(MakeProbeError):
                            toolchain_runtime.compile_operand_roles(
                                rows, model["profile"], rows[0]["argv"], complete=count == 3,
                            )
                        self.assertEqual(rows, before)

    def test_role_grammar_rejects_intermediate_mentions_in_non_role_child_options(self):
        model = self.model()
        path = model["roles"].output.value
        options = (
            (1, False, ("-imultilib", "-isystem", "-dumpbase", "-dumpbase-ext")),
            (1, True, ("-D", "-mcpu=", "-march=", "-mfloat-abi=", "-mlibarch=", "-mabi=")),
            (2, True, ("-march=", "-mcpu=", "-mfpu=", "-mfloat-abi=", "-meabi=")),
        )
        for actor, attached, names in options:
            for option in names:
                for value in (path, 'prefix:"' + path + '":suffix'):
                    for count in ((2, 3) if actor == 1 else (3,)):
                        rows = copy.deepcopy(model["executions"][:count])
                        argv = rows[actor]["argv"]
                        if attached:
                            word = option + ("INTERMEDIATE_ALIAS=" if option == "-D" else "") + value
                            position = next((
                                index for index, old in enumerate(argv)
                                if option != "-D" and old.startswith(option)
                            ), None)
                            if position is None:
                                argv.insert(1, word)
                            else:
                                argv[position] = word
                        elif option in argv:
                            argv[argv.index(option) + 1] = value
                        else:
                            argv[1:1] = [option, value]
                        before = copy.deepcopy(rows)
                        with self.subTest(actor=actor, option=option, value=value, count=count):
                            with self.assertRaises(MakeProbeError):
                                toolchain_runtime.compile_operand_roles(
                                    rows, model["profile"], rows[0]["argv"], complete=count == 3,
                                )
                            self.assertEqual(rows, before)

    def test_child_isystem_is_only_the_actual_parent_admitted_selection(self):
        model = self.model()
        admitted = "/usr/include/newlib"
        for parent in ("separate", "attached", "absent"):
            for child in (None, admitted, "/not-the-admitted-newlib", admitted + "/", "/usr/include/./newlib"):
                for count in (2, 3):
                    rows = copy.deepcopy(model["executions"][:count])
                    driver = rows[0]["argv"]
                    index = driver.index("-isystem")
                    if parent == "attached":
                        driver[index:index + 2] = ["-isystem" + admitted]
                    elif parent == "absent":
                        del driver[index:index + 2]
                    cc1 = rows[1]["argv"]
                    index = cc1.index("-isystem")
                    if child is None:
                        del cc1[index:index + 2]
                    else:
                        cc1[index + 1] = child
                    before = copy.deepcopy(rows)
                    with self.subTest(parent=parent, child=child, count=count):
                        if child is None or parent != "absent" and child == admitted:
                            roles = toolchain_runtime.compile_operand_roles(
                                rows, model["profile"], driver, complete=count == 3,
                            )
                            self.assertEqual(cc1[roles.output.argv_index], model["roles"].output.value)
                            if count == 3:
                                self.assertEqual(rows[2]["argv"][roles.input.argv_index], roles.output.value)
                            else:
                                self.assertIsNone(roles.input)
                        else:
                            with self.assertRaises(MakeProbeError):
                                toolchain_runtime.compile_operand_roles(
                                    rows, model["profile"], driver, complete=count == 3,
                                )
                        self.assertEqual(rows, before)

    def test_sdk_binding_preserves_closed_child_forms_and_parent_refusals(self):
        model = self.model()
        for path in ("/not-the-admitted-newlib", "/usr/include/newlib/", "/usr/include/./newlib"):
            for count in (1, 2, 3):
                rows = copy.deepcopy(model["executions"][:count])
                driver = rows[0]["argv"]
                driver[driver.index("-isystem") + 1] = path
                if count >= 2:
                    cc1 = rows[1]["argv"]
                    cc1[cc1.index("-isystem") + 1] = path
                with self.subTest(parent=path, count=count), self.assertRaises(MakeProbeError):
                    toolchain_runtime.compile_operand_roles(
                        rows, model["profile"], driver, complete=count == 3,
                    )
        for replacement in (
            ["-isystem/usr/include/newlib"],
            ["-isystem", "/usr/include/newlib", "-isystem", "/usr/include/newlib"],
            ["-isystem", "/usr/include/newlib", "-isystem", "/not-the-admitted-newlib"],
            ["-isystem"], ["-isystem", ""], ["-isystem", "@response"],
        ):
            rows = copy.deepcopy(model["executions"])
            cc1 = rows[1]["argv"]
            index = cc1.index("-isystem")
            cc1[index:index + 2] = replacement
            with self.subTest(child=replacement), self.assertRaises(MakeProbeError):
                toolchain_runtime.compile_operand_roles(
                    rows, model["profile"], rows[0]["argv"], complete=True,
                )
        for actor in (0, 2):
            rows = copy.deepcopy(model["executions"])
            rows[actor]["argv"][1:1] = ["-isystem", "/usr/include/newlib"]
            with self.subTest(actor=actor), self.assertRaises(MakeProbeError):
                toolchain_runtime.compile_operand_roles(
                    rows, model["profile"], rows[0]["argv"], complete=True,
                )

    def test_accepted_role_forms_preserve_raw_values_order_and_only_two_projection_slots(self):
        baseline = None
        for variant in ("original", "value-looking-flags", "attached-parent-sdk", "without-sdk", "without-child-sdk"):
            model = self.model()
            rows = model["executions"]
            driver, cc1, assembler = (row["argv"] for row in rows)
            if variant == "value-looking-flags":
                driver.insert(1, "-mabi=apcs-gnu")
                cc1[cc1.index("-imultilib") + 1] = "-isystem"
                cc1[cc1.index("-dumpbase") + 1] = "-o"
                cc1[1:1] = [
                    "-dumpbase-ext", "-isystem", "-quiet", "-fno-pic", "-mabi=apcs-gnu",
                    "-DROLE=-o", "-DROLE=/work/other.s", "-DROLE=/elsewhere/ccL3VdjV.s",
                ]
                assembler[1:1] = ["-mthumb", "-mfpu=vfp"]
                for row in rows:
                    row["environment"]["UNRELATED"] = "/work/other.s:/elsewhere/ccL3VdjV.s"
            elif variant == "attached-parent-sdk":
                index = driver.index("-isystem")
                driver[index:index + 2] = ["-isystem/usr/include/newlib"]
            elif variant == "without-sdk":
                index = driver.index("-isystem")
                del driver[index:index + 2]
                index = cc1.index("-isystem")
                del cc1[index:index + 2]
            elif variant == "without-child-sdk":
                index = cc1.index("-isystem")
                del cc1[index:index + 2]
            before = copy.deepcopy(rows)
            with self.subTest(variant=variant):
                roles = toolchain_runtime.compile_operand_roles(
                    rows, model["profile"], driver, complete=True,
                )
                self.assertEqual(rows, before)
                model["receipt"]["writer"]["operand"]["argv_index"] = roles.output.argv_index
                model["receipt"]["reader"]["operand"]["argv_index"] = roles.input.argv_index
                for row, actor in zip(rows, model["receipt"]["actors"]):
                    actor["exec_record_sha256"] = hashlib.sha256(toolchain_runtime.encoded(row)).hexdigest()
                receipt = self.parse(model, reserve=self.reserve)
                raw = self.probes(model)
                original = copy.deepcopy(raw)
                projected = toolchain_runtime.project_compile_identity(
                    raw, receipt, roles, reserve=self.reserve,
                )
                expected = list(copy.deepcopy(raw))
                for operand in (roles.output, roles.input):
                    expected[operand.exec_sequence - 1]["argv"][operand.argv_index] = {
                        "kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly",
                    }
                self.assertEqual(projected["runtime_probes"], expected)
                self.assertEqual(raw, original)
                if baseline is None:
                    baseline = projected
                else:
                    self.assertNotEqual(projected, baseline)

    def test_receipt_role_admission_rejects_self_consistent_alias_and_sdk_models(self):
        for mutation in ("environment", "macro", "sdk"):
            model = self.model()
            rows = model["executions"]
            path = model["roles"].output.value
            if mutation == "environment":
                rows[1]["environment"]["INTERMEDIATE_ALIAS"] = path
            elif mutation == "macro":
                rows[1]["argv"].insert(1, "-DINTERMEDIATE_ALIAS=" + path)
                model["receipt"]["writer"]["operand"]["argv_index"] += 1
            else:
                index = rows[1]["argv"].index("-isystem")
                rows[1]["argv"][index + 1] = "/not-the-admitted-newlib"
            for row, actor in zip(rows, model["receipt"]["actors"]):
                actor["exec_record_sha256"] = hashlib.sha256(toolchain_runtime.encoded(row)).hexdigest()
            before = copy.deepcopy(model)
            with self.subTest(mutation=mutation), self.assertRaises(MakeProbeError):
                self.parse(model, reserve=self.reserve)
            self.assertEqual(model, before)

    def test_exact_model_receipt_validates_and_is_canonical_immutable_bytes(self):
        model = self.model()
        charges = []
        canonical = self.parse(model, reserve=charges.append)
        self.assertIs(type(canonical), bytes)
        self.assertEqual(canonical, toolchain_runtime.encoded(model["receipt"]))
        self.assertGreaterEqual(len(charges), 4)
        self.assertTrue(all(type(value) is int and value > 0 for value in charges))
        self.assertGreater(charges[-1], len(canonical))
        reordered = {key: model["receipt"][key] for key in reversed(model["receipt"])}
        self.assertEqual(self.parse(model, receipt=reordered, reserve=self.reserve), canonical)

    def test_receipt_duplicate_fields_extras_and_foreign_stage_never_validate(self):
        model = self.model()
        payload = json.dumps(model["receipt"], separators=(",", ":"))
        duplicate = toolchain_runtime.INTERMEDIATE_PREFIX + payload.replace(
            '"version":1', '"version":1,"version":1', 1,
        )
        with self.assertRaises(MakeProbeError):
            self.parse(model, values=[duplicate], reserve=self.reserve)
        extra = copy.deepcopy(model["receipt"])
        extra["grant"] = True
        with self.assertRaises(MakeProbeError):
            self.parse(model, receipt=extra, reserve=self.reserve)
        with self.assertRaises(MakeProbeError):
            self.parse(model, values=[self.wire(model), self.wire(model)], reserve=self.reserve)
        foreign = copy.deepcopy(model)
        foreign["profile"]["stage"] = 3
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.intermediate_record(
                [self.wire(model)], profile=foreign["profile"], launch=model["launch"],
                executions=model["executions"], returncode=0, limits=model["limits"],
                reserve=self.reserve,
            )
        self.assertIsNone(toolchain_runtime.intermediate_record(
            [], profile=foreign["profile"], launch=model["launch"],
            executions=(), returncode=0, limits=model["limits"],
            reserve=self.reserve,
        ))
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.intermediate_record(
                [self.wire(model)], profile=model["profile"], launch=model["launch"],
                executions=model["executions"], returncode=1, limits=model["limits"],
                reserve=self.reserve,
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
                    self.parse(model, receipt=receipt, reserve=self.reserve)

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
        baseline = self.parse(model, reserve=self.reserve)
        for path, value in mutations:
            receipt = copy.deepcopy(model["receipt"])
            current = receipt
            for component in path[:-1]:
                current = current[component]
            current[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(MakeProbeError):
                self.parse(model, receipt=receipt, reserve=self.reserve)
            current[path[-1]] = copy.deepcopy(
                self.value_at(model["receipt"], path)
            )
            self.assertEqual(self.parse(model, receipt=receipt, reserve=self.reserve), baseline)

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
            self.parse(model, receipt=receipt, reserve=self.reserve)
        with patch.object(toolchain_runtime, "_retirement_progression", return_value=True):
            self.assertIs(type(self.parse(model, receipt=receipt, reserve=self.reserve)), bytes)
        with self.assertRaises(MakeProbeError):
            self.parse(model, receipt=receipt, reserve=self.reserve)

    def test_receipt_pre_growth_wire_node_depth_and_issued_bounds_reject(self):
        model = self.model()
        oversized = toolchain_runtime.INTERMEDIATE_PREFIX + '{"path":"' + "x" * 65536 + '"}'
        deep = toolchain_runtime.INTERMEDIATE_PREFIX + "[" * 11 + "0" + "]" * 11
        nodes = toolchain_runtime.INTERMEDIATE_PREFIX + "[" + ",".join("0" for _ in range(513)) + "]"
        for name, value in (("wire", oversized), ("depth", deep), ("nodes", nodes)):
            with self.subTest(name=name), self.assertRaises(MakeProbeError):
                self.parse(model, values=[value], reserve=self.reserve)
        small = copy.deepcopy(model)
        small["limits"] = replace(model["limits"], observation_limit=len(self.wire(model)) - 1)
        with self.assertRaises(MakeProbeError):
            self.parse(small, reserve=self.reserve)
        small = copy.deepcopy(model)
        small["limits"] = replace(model["limits"], file_limit=63)
        with self.assertRaises(MakeProbeError):
            self.parse(small, reserve=self.reserve)
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
        receipt = self.parse(model, reserve=self.reserve)
        projected = toolchain_runtime.project_compile_identity(
            raw, receipt, model["roles"], reserve=self.reserve,
        )
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
            first_raw, self.parse(first, reserve=self.reserve), first["roles"],
            reserve=self.reserve,
        )
        second_projected = toolchain_runtime.project_compile_identity(
            second_raw, self.parse(second, reserve=self.reserve), second["roles"],
            reserve=self.reserve,
        )
        self.assertEqual(first_projected, second_projected)

    def test_projection_keeps_meaningful_runtime_content_and_outer_facts_unequal(self):
        model = self.model()
        receipt = self.parse(model, reserve=self.reserve)
        baseline = toolchain_runtime.project_compile_identity(
            self.probes(model), receipt, model["roles"], reserve=self.reserve,
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
        content = self.parse(content_model, reserve=self.reserve)
        mutations.append(("assembly-content", self.probes(model), content, model["roles"]))
        for name, raw, selected_receipt, roles in mutations:
            with self.subTest(name=name):
                self.assertNotEqual(
                    toolchain_runtime.project_compile_identity(
                        raw, selected_receipt, roles, reserve=self.reserve,
                    ),
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
        receipt = self.parse(model, reserve=self.reserve)
        self.assertEqual(
            toolchain_runtime.project_compile_identity(raw, receipt, model["roles"], reserve=self.reserve),
            toolchain_runtime.project_compile_identity(reordered, receipt, model["roles"], reserve=self.reserve),
        )
        changed = list(copy.deepcopy(raw))
        changed[1]["argv"][model["roles"].output.argv_index] = "/work/other.s"
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.project_compile_identity(changed, receipt, model["roles"], reserve=self.reserve)

    def test_both_data_helpers_require_explicit_callable_admission(self):
        model = self.model()
        values = [self.wire(model)]
        receipt = self.parse(model, reserve=self.reserve)
        arguments = {
            "profile": model["profile"], "launch": model["launch"],
            "executions": model["executions"], "returncode": 0, "limits": model["limits"],
        }
        with self.assertRaises(TypeError):
            toolchain_runtime.intermediate_record(values, **arguments)
        with self.assertRaises(TypeError):
            toolchain_runtime.project_compile_identity(self.probes(model), receipt, model["roles"])
        for reserve in (None, 0, [], {}):
            with self.subTest(reserve=reserve), self.assertRaises(MakeProbeError):
                toolchain_runtime.intermediate_record(values, **arguments, reserve=reserve)
            with self.subTest(reserve=reserve), self.assertRaises(MakeProbeError):
                toolchain_runtime.project_compile_identity(
                    self.probes(model), receipt, model["roles"], reserve=reserve,
                )

    def test_complete_wire_type_and_bounds_reject_before_copy_admission(self):
        class ForeignText(str):
            def __getitem__(self, key):
                raise AssertionError("foreign string sliced")

            def encode(self, *args, **kwargs):
                raise AssertionError("foreign string encoded")

        model = self.model()
        wire = self.wire(model)
        bound = toolchain_runtime.INTERMEDIATE_RECORD_LIMIT
        full = wire + " " * (bound - len(wire))
        self.assertEqual(
            self.parse(model, values=[full], reserve=self.reserve),
            self.parse(model, reserve=self.reserve),
        )
        for value in (
            ForeignText(wire), wire.encode("ascii"), None, [], {},
            toolchain_runtime.INTERMEDIATE_PREFIX, full + " ",
        ):
            charges = []
            with self.subTest(kind=type(value).__name__), self.assertRaises(MakeProbeError):
                self.parse(model, values=[value], reserve=charges.append)
            self.assertEqual(charges, [])
        smaller = copy.deepcopy(model)
        smaller["limits"] = replace(model["limits"], observation_limit=len(wire) - 1)
        charges = []
        with self.assertRaises(MakeProbeError):
            self.parse(smaller, values=[wire], reserve=charges.append)
        self.assertEqual(charges, [])

    def test_non_ascii_complete_wire_rejects_without_payload_copy_allocation(self):
        model = self.model()
        for character in ("\u0100", "\U00010000", "\ud800"):
            values = [toolchain_runtime.INTERMEDIATE_PREFIX + character * 60000]
            charges = []
            with self.subTest(codepoint=ord(character)):
                tracemalloc.start()
                try:
                    with self.assertRaises(MakeProbeError):
                        self.parse(model, values=values, reserve=charges.append)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(charges, [])
                self.assertLess(peak, 32768)
        receipt = b"\xff" * 60000
        probes = self.probes(model)
        charges = []
        with self.assertRaises(MakeProbeError):
            toolchain_runtime.project_compile_identity(
                probes, receipt, model["roles"], reserve=charges.append,
            )
        self.assertEqual(charges, [])

    def test_representation_workspace_is_admitted_before_each_growth_phase(self):
        for character in ("x", "\u0100", "\U00010000"):
            path = "/work/" + character * (4090 // len(character.encode("utf-8")))
            model = self.model(path)
            for row, actor in zip(model["executions"], model["receipt"]["actors"]):
                row["environment"]["ESCAPED"] = "\x01" * 4096
                actor["exec_record_sha256"] = hashlib.sha256(toolchain_runtime.encoded(row)).hexdigest()
            wire = self.wire(model)
            values = [wire + " " * (toolchain_runtime.INTERMEDIATE_RECORD_LIMIT - len(wire))]
            probes = self.probes(model)
            receipt = self.parse(model, values=values, reserve=self.reserve)
            operations = (
                lambda reserve: self.parse(model, values=values, reserve=reserve),
                lambda reserve: toolchain_runtime.project_compile_identity(
                    probes, receipt, model["roles"], reserve=reserve,
                ),
            )
            for index, operation in enumerate(operations):
                with self.subTest(codepoint=ord(character), operation=index):
                    admitted = 0
                    checkpoints = []

                    def reserve(size):
                        nonlocal admitted
                        _, peak = tracemalloc.get_traced_memory()
                        if admitted:
                            checkpoints.append((peak, admitted))
                        admitted += size

                    tracemalloc.start()
                    try:
                        result = operation(reserve)
                        _, peak = tracemalloc.get_traced_memory()
                    finally:
                        tracemalloc.stop()
                    self.assertIsNotNone(result)
                    self.assertTrue(checkpoints)
                    for observed, prior_admission in checkpoints:
                        self.assertLessEqual(observed, prior_admission)
                    self.assertLessEqual(peak, admitted)

    def test_verification_admission_remains_cumulative_and_refusal_propagates(self):
        model = self.model()
        receipt = self.parse(model, reserve=self.reserve)
        probes = self.probes(model)
        values = [self.wire(model)]
        operations = (
            lambda reserve: self.parse(model, values=values, reserve=reserve),
            lambda reserve: toolchain_runtime.project_compile_identity(
                probes, receipt, model["roles"], reserve=reserve,
            ),
        )
        for index, operation in enumerate(operations):
            charges = []
            expected = operation(charges.append)
            for limit in (max(charges), sum(charges) - 1):
                with self.subTest(operation=index, limit=limit):
                    remaining = limit
                    accepted = []

                    def reserve(size):
                        nonlocal remaining
                        if size > remaining:
                            raise MakeProbeError("model cumulative admission refused")
                        remaining -= size
                        accepted.append(size)

                    with self.assertRaisesRegex(MakeProbeError, "model cumulative admission refused"):
                        operation(reserve)
                    self.assertTrue(accepted)
                    self.assertEqual(remaining, limit - sum(accepted))
                    self.assertLess(remaining, max(charges))
                    self.assertEqual(operation(self.reserve), expected)

    def test_intermediate_consumed_shapes_refuse_controlled_protocol_errors(self):
        model = self.model()
        mutations = (
            (("executions",), []), (("executions",), None), (("executions",), 1),
            (("executions",), {}), (("executions", 0), None),
            (("executions", 0), {}), (("executions", 0, "argv"), 7),
            (("executions", 0, "argv"), [[]]),
            (("executions", 1, "identity"), None),
            (("executions", 1, "identity", 0), True),
            (("executions", 1, "sequence"), []),
            (("executions", 1, "path"), []),
            (("executions", 1, "environment"), []),
            (("executions", 1, "environment"), {"LANG": None}),
            (("executions", 1, "environment"), {1: "C"}),
            (("profile",), None), (("profile",), []), (("profile",), 1),
            (("profile", "version"), True), (("profile", "stage"), []),
            (("profile", "stdin"), None), (("profile", "inputs"), None),
            (("profile", "driver_identity"), None),
            (("profile", "driver_identity", 0), True),
            (("profile", "images"), None), (("profile", "images"), []),
            (("profile", "images", 0), None), (("profile", "images", 0), []),
            (("profile", "images", 0, 0), []),
            (("profile", "images", 0, 1), True),
            (("profile", "workspace"), None), (("profile", "workspace"), 1),
            (("profile", "workspace"), {}), (("profile", "workspace"), []),
            (("profile", "workspace"), [1, 2]),
            (("profile", "workspace", 0), []),
            (("profile", "workspace", 1), True),
            (("profile", "workspace", 2), "directory"),
            (("launch",), None), (("launch",), []), (("launch", "version"), True),
            (("launch", "scope"), None), (("launch", "binding"), []),
        )
        for path, value in mutations:
            changed = copy.deepcopy(model)
            self.value_at(changed, path[:-1])[path[-1]] = value
            with self.subTest(path=path, kind=type(value).__name__), self.assertRaises(MakeProbeError):
                self.parse(changed, reserve=self.reserve)
        for container in (("profile",), ("launch",), ("executions", 0)):
            for field in self.value_at(model, container):
                changed = copy.deepcopy(model)
                del self.value_at(changed, container)[field]
                with self.subTest(container=container, missing=field), self.assertRaises(MakeProbeError):
                    self.parse(changed, reserve=self.reserve)

    def test_role_parser_validates_parent_and_execution_shapes_before_use(self):
        model = self.model()
        for argv in (None, 1, True, {}, [], [None], [[]], [1], ("", None)):
            with self.subTest(parent=argv), self.assertRaises(MakeProbeError):
                toolchain_runtime.compile_operand_roles(
                    model["executions"], model["profile"], argv, complete=True,
                )
        for field, values in (
            ("argv", (None, 7, True, "argv", {}, (), [], [None], [[]])),
            ("sequence", (None, True, [], {}, "2")),
            ("identity", (None, [], [True] * 6, [[1]] * 6)),
            ("environment", (None, [], {"LANG": []}, {1: "C"})),
        ):
            for value in values:
                rows = copy.deepcopy(model["executions"])
                rows[1][field] = value
                with self.subTest(field=field, kind=type(value).__name__), self.assertRaises(MakeProbeError):
                    toolchain_runtime.compile_operand_roles(
                        rows, model["profile"], model["driver"], complete=True,
                    )

    def test_projection_consumed_shapes_refuse_controlled_protocol_errors(self):
        model = self.model()
        receipt = self.parse(model, reserve=self.reserve)
        raw = self.probes(model)
        malformed = [
            None, 7, True, {}, [], [None], raw[1:], (raw[0], raw[1], raw[1], raw[2]),
        ]
        for field, values in (
            ("argv", (None, 7, True, "argv", {}, (), [], [None], [[]])),
            ("sequence", (None, True, [], {}, "2")),
            ("path", (None, [])),
            ("identity", (None, [], [True] * 6, [[1]] * 6)),
            ("environment", (None, [], {"LANG": []}, {1: "C"})),
            ("stage", (None, [])),
        ):
            for value in values:
                changed = list(copy.deepcopy(raw))
                changed[1][field] = value
                malformed.append(changed)
        for index in (0, 1, 2, 3):
            for field in raw[index]:
                changed = list(copy.deepcopy(raw))
                del changed[index][field]
                malformed.append(changed)
        for field, value in (("stdin", 7), ("eof", 1), ("stage", [])):
            changed = list(copy.deepcopy(raw))
            changed[3][field] = value
            malformed.append(changed)
        for index, value in enumerate(malformed):
            with self.subTest(case=index), self.assertRaises(MakeProbeError):
                toolchain_runtime.project_compile_identity(
                    value, receipt, model["roles"], reserve=self.reserve,
                )

    def test_projection_nested_role_shapes_refuse_before_operand_access(self):
        model = self.model()
        receipt = self.parse(model, reserve=self.reserve)
        raw = self.probes(model)
        roles = model["roles"]
        malformed = [None, {}, 1]
        for field, value in (
            ("creator_sequence", True), ("writer_sequence", None), ("reader_sequence", []),
            ("output", None), ("output", 7), ("input", {}), ("input", []),
        ):
            malformed.append(replace(roles, **{field: value}))
        for name in ("output", "input"):
            for field, value in (
                ("role", []), ("kind", None), ("exec_sequence", True),
                ("argv_index", True), ("argv_index", []), ("argv_index", -1),
                ("argv_index", toolchain_runtime.COMPILE_ARG_LIMIT), ("value", None),
            ):
                malformed.append(replace(
                    roles, **{name: replace(getattr(roles, name), **{field: value})},
                ))
        for index, value in enumerate(malformed):
            with self.subTest(case=index), self.assertRaises(MakeProbeError):
                toolchain_runtime.project_compile_identity(raw, receipt, value, reserve=self.reserve)


class _IntermediateModel:
    """Synthetic stopped processes and effects; never opens a real descriptor."""

    def __init__(self, body=b"model assembly\n", *, observation_limit=16 * 1024 * 1024):
        self.model = ToolchainProtocolDataTests().model()
        self.body = b""
        self.expected = body
        self.exists = False
        self.mode = stat.S_IFREG | 0o600
        self.device, self.inode, self.mtime, self.ctime, self.links = 21, 22, 1000, 1000, 1
        self.handles, self.trace_fds, self.process_handles = {}, {}, set()
        self.peak = 0
        self.closed, self.peeks, self.preads = [], [], []
        self.memory = b""
        self.policy = syscall_guard.Policy.__new__(syscall_guard.Policy)
        self.policy.config = {
            **{name: getattr(self.model["limits"], name) for name in (
                "file_limit", "observation_count", "observation_limit", "write_limit",
                "creation_limit", "process_limit", "memory_limit", "syscall_limit", "deadline",
            )},
            "root": "/inert/command-root-1", "mode": "compile",
            "argv": self.model["driver"], "toolchain_runtime": self.model["launch"],
            "executables": [row["path"] for row in self.model["executions"]],
            "file_limit": max(4096, len(body)), "write_limit": max(4096, len(body)),
            "observation_limit": observation_limit,
        }
        self.policy.toolchain = self.model["profile"]
        self.policy.mode = "compile"
        self.policy.processes = {}
        self.policy.calls = self.policy.written = self.policy.created = 0
        self.policy.observation_bytes = 0
        self.policy.observation_attempts = {name: set() for name in ("consumed", "code_consumed", "accessed")}
        self.policy.accessed = set()
        self.policy.toolchain_temporaries = {}
        self.policy.kernel_streams = {}
        self.policy.filter_kernel = self.policy.read_trace = self.policy.private_install = None
        self.policy.metadata = []
        self.policy.metadata_seen = set()
        self.policy.observer = lambda *args: False
        self.policy.path = self.path
        self.stack = ExitStack()

    def __enter__(self):
        replacements = (
            (syscall_guard.time, "monotonic", lambda: 100.0),
            (syscall_guard.os, "open", self.open),
            (syscall_guard.os, "close", self.close),
            (syscall_guard.os, "stat", self.stat),
            (syscall_guard.os, "fstat", self.fstat),
            (syscall_guard.os, "read", self.read),
            (syscall_guard.os, "pread", self.pread),
            (syscall_guard.os, "readlink", self.readlink),
            (syscall_guard, "ptrace", self.ptrace),
            (syscall_guard, "memory", self.forbidden),
            (syscall_guard.signal, "pthread_sigmask", lambda *args: set()),
            (syscall_guard.signal, "sigpending", lambda: set()),
            (Path, "stat", lambda path, **kwargs: self.stat(str(path), **kwargs)),
            (Path, "lstat", lambda path: self.stat(str(path), follow_symlinks=False)),
            (toolchain_runtime, "verify_workspace", self.verify_workspace),
        )
        for owner, name, value in replacements:
            self.stack.enter_context(patch.object(owner, name, value))
        self.tracker = self.policy.reserve_toolchain_intermediate()
        self.policy.toolchain_intermediate = self.tracker
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.tracker.close()
        finally:
            self.stack.close()

    @staticmethod
    def forbidden(*args, **kwargs):
        raise AssertionError("whole-buffer or unexpected effect escaped the inert model")

    def info(self):
        return SimpleNamespace(
            st_dev=self.device, st_ino=self.inode, st_mode=self.mode, st_size=len(self.body),
            st_mtime_ns=self.mtime, st_ctime_ns=self.ctime, st_nlink=self.links,
        )

    def directory(self):
        dev, ino, mode = self.model["profile"]["workspace"]
        return SimpleNamespace(st_dev=dev, st_ino=ino, st_mode=mode)

    def path(self, pid, state, pointer, dirfd=-100, **kwargs):
        path = self.model["roles"].output.value
        state.path_context = path, dirfd, None
        return path

    @contextmanager
    def readlink_paths(self, spelling=None):
        literal = [self.model["roles"].output.value if spelling is None else spelling]
        root = self.policy.config["root"]
        original_lstat = Path.lstat
        def pathname(pid, address):
            assert address == 0x2000
            return literal[0]
        def link(path):
            value = str(path)
            if value.startswith("/proc/"):
                return self.readlink(path)
            assert value.startswith(root + "/")
            if value == root + "/work-alias":
                return "/work"
            raise OSError(errno.EINVAL, "MODEL nonsymlink component")
        def lstat(path):
            if str(path) == root + "/work/ccFOREGN.s":
                raise FileNotFoundError("MODEL foreign absent temporary")
            return original_lstat(path)
        with (
            patch.object(syscall_guard, "cstring", pathname),
            patch.object(syscall_guard.os, "readlink", link),
            patch.object(self.policy, "path", syscall_guard.Policy.path.__get__(self.policy)),
            patch.object(Path, "lstat", lstat),
        ):
            yield literal

    def readlink_query(self, actor, number, *, dirfd=-100, requested=64, result=-errno.EINVAL, kernel=None):
        if number == 89:
            self.syscall(actor, number, 0x2000, 0x3000, requested, result=result, kernel=kernel)
        else:
            assert number == 267
            self.syscall(actor, number, dirfd, 0x2000, 0x3000, requested, result=result, kernel=kernel)

    def verify_workspace(self, path, expected):
        assert str(path) == "/inert/command-root-1/work"
        assert not self.exists
        assert list(expected) == self.model["profile"]["workspace"]

    def open(self, path, flags, *, dir_fd=None):
        path = str(path)
        if path == "/inert/command-root-1/work":
            value = ("workspace", None)
        elif path == Path(self.model["roles"].output.value).name:
            assert self.exists and self.handles[dir_fd][0] == "workspace"
            value = ("file", None)
        elif path.startswith("/proc/") and "/fdinfo/" in path:
            _, _, pid, _, descriptor = path.split("/")
            assert (int(pid), int(descriptor)) in self.trace_fds
            value = ("fdinfo", (int(pid), int(descriptor)))
        else:
            self.forbidden()
        descriptor = next(number for number in range(10, 30) if number not in self.handles)
        self.handles[descriptor] = value
        self.peak = max(self.peak, len(self.handles))
        return descriptor

    def close(self, descriptor):
        if descriptor in self.process_handles:
            self.process_handles.remove(descriptor)
        else:
            assert descriptor in self.handles
            del self.handles[descriptor]
        self.closed.append(descriptor)

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        path = str(path)
        if path == "/inert/command-root-1/work":
            return self.directory()
        if path.startswith("/proc/") and "/fd/" in path:
            _, _, pid, _, descriptor = path.split("/")
            assert (int(pid), int(descriptor)) in self.trace_fds
            return self.info()
        if (
            path == "/inert/command-root-1" + self.model["roles"].output.value
            or path == Path(self.model["roles"].output.value).name
            and self.handles[dir_fd][0] == "workspace"
        ):
            if not self.exists:
                raise FileNotFoundError(path)
            return self.info()
        self.forbidden()

    def fstat(self, descriptor):
        kind, _ = self.handles[descriptor]
        return self.directory() if kind == "workspace" else self.info()

    def readlink(self, path):
        _, _, pid, _, descriptor = str(path).split("/")
        assert (int(pid), int(descriptor)) in self.trace_fds
        return str(Path(self.policy.config["root"]) / self.model["roles"].output.value.lstrip("/"))

    def read(self, descriptor, count):
        kind, owner = self.handles[descriptor]
        assert kind == "fdinfo" and count == 4097
        position, flags = self.trace_fds[owner]
        return f"pos:\t{position}\nflags:\t{flags:o}\n".encode()

    def pread(self, descriptor, count, offset):
        assert self.handles[descriptor][0] == "file"
        assert 0 < count <= 65536
        self.preads.append((count, offset))
        return self.body[offset:offset + count]

    def ptrace(self, request, pid, address):
        assert request == syscall_guard.PEEKDATA
        assert address % 8 == 0
        self.peeks.append(address)
        offset = address - 0x1000
        assert 0 <= offset < len(self.memory)
        return int.from_bytes(self.memory[offset:offset + 8].ljust(8, b"\0"), "little")

    def actor(self, sequence):
        row = copy.deepcopy(self.model["executions"][sequence - 1])
        pid = 400 + sequence
        state = syscall_guard.Process("compiler", pidfd=700 + sequence)
        self.process_handles.add(state.pidfd)
        self.policy.processes[pid] = state
        self.tracker.birth(pid, state)
        state.toolchain_exec = row["argv"], row["environment"]
        self.tracker.exec_entry(pid, state)
        self.tracker.executed(pid, state, row, toolchain_runtime.encoded(row))
        state.toolchain_exec = None
        state.dependency_image = row["path"]
        return pid, state

    def syscall(self, actor, number, a, b=0, c=0, d=0, e=0, f=0, *, result=0, kernel=None):
        pid, state = actor
        registers = syscall_guard.Registers()
        registers.orig_rax = number
        registers.rdi, registers.rsi, registers.rdx, registers.r10 = a, b, c, d
        registers.r8, registers.r9 = e, f
        state.kernel_call = number
        self.policy.entry(pid, state, registers)
        if kernel is not None:
            kernel()
        registers.rax = result & ((1 << 64) - 1)
        self.policy.leave(pid, state, registers)

    def open_actor(self, actor, flags, mode, *, number=2, descriptor=7):
        pid, _ = actor
        def kernel():
            if not self.exists:
                assert actor[1].toolchain_exec_sequence == 1, "MODEL writer must not create or repair an object"
                self.exists = True
            self.trace_fds[pid, descriptor] = [0, flags]
        if number == 2:
            self.syscall(actor, number, 0x2000, flags, mode, result=descriptor, kernel=kernel)
        else:
            assert number == 257
            self.syscall(actor, number, -100, 0x2000, flags, mode, result=descriptor, kernel=kernel)

    def close_actor(self, actor, *, descriptor=7):
        self.syscall(actor, 3, descriptor, kernel=lambda: self.trace_fds.pop((actor[0], descriptor)))

    def exit_actor(self, actor, status=0):
        pid, state = actor
        self.tracker.exited(pid, state, status)
        del self.policy.processes[pid]
        state.close()

    def write(self, actor, *, descriptor=7):
        def kernel():
            self.body = self.expected
            self.mtime += 1
            self.ctime += 1
            self.trace_fds[actor[0], descriptor][0] = len(self.body)
        self.syscall(actor, 1, descriptor, 0x1003, len(self.expected), result=len(self.expected), kernel=kernel)

    def read_actor(self, actor, data=None, *, descriptor=7):
        data = self.body if data is None else data
        self.memory = b"xxx" + data + b"\0" * 8
        def kernel():
            self.trace_fds[actor[0], descriptor][0] += len(data)
        self.syscall(actor, 0, descriptor, 0x1003, len(data), result=len(data), kernel=kernel)

    def writer(self, descriptor):
        self.created()
        writer = self.actor(2)
        if descriptor == 0:
            self.syscall(writer, 3, 0)
        self.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0, descriptor=descriptor)
        return writer

    @contextmanager
    def memory_effects(self):
        runtime = "/usr/lib/model-immutable.so"
        self.policy.config["dependency"] = {"executables": self.policy.config["executables"]}
        self.policy.toolchain_inputs = {}
        self.policy.header_roots = {}
        self.policy.dependency_files = {runtime}
        self.policy.memory_peak = 0
        for pid, state in self.policy.processes.items():
            state.memory_group, state.memory_limit = pid, 4096
            state.break_end = 4096
        original_lstat = Path.lstat
        def lstat(path):
            if str(path) == self.policy.config["root"] + runtime:
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o444)
            return original_lstat(path)
        grants = []
        def limit(pid, resource, values):
            assert pid in self.policy.processes and resource == syscall_guard.resource.RLIMIT_AS
            grants.append((pid, values))
            return 0, 0
        with (
            patch.object(self.policy, "virtual_memory", return_value=4096),
            patch.object(syscall_guard.resource, "prlimit", limit),
            patch.object(Path, "lstat", lstat),
        ):
            yield runtime, grants

    def created(self):
        self.driver = self.actor(1)
        self.open_actor(self.driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        self.close_actor(self.driver)
        return self.driver

    def sealed(self, *, writer_request=None):
        self.created()
        writer = self.actor(2)
        number, flags, mode = (2, os.O_WRONLY | os.O_TRUNC, 0) if writer_request is None else writer_request
        self.open_actor(writer, flags, mode, number=number)
        self.write(writer)
        self.close_actor(writer)
        self.exit_actor(writer)
        return self.driver

    def consumed(self, *, writer_request=None):
        self.sealed(writer_request=writer_request)
        reader = self.actor(3)
        self.open_actor(reader, os.O_RDONLY, 0)
        self.read_actor(reader)
        self.close_actor(reader)
        self.exit_actor(reader)
        return self.driver

    def complete(self, *, writer_request=None):
        self.consumed(writer_request=writer_request)
        def unlink():
            self.exists = False
            self.links = 0
            self.ctime += 1
        self.syscall(self.driver, 87, 0x2000, kernel=unlink)
        self.exit_actor(self.driver)

    def finish(self, *, writer_request=None):
        self.complete(writer_request=writer_request)
        self.tracker.emit(0)
        value, = self.policy.accessed
        return value


class ToolchainIntermediateInertTests(unittest.TestCase):
    def producer(self, model, reserve):
        return toolchain_runtime.encode_intermediate_record(
            model["receipt"], profile=model["profile"], launch=model["launch"],
            executions=model["executions"], returncode=0, limits=model["limits"], reserve=reserve,
        )

    def test_producer_emission_fits_derived_admission_without_wire_roundtrip(self):
        with _IntermediateModel() as model:
            model.complete()
            setup = model.policy.observation_bytes
            charges = []
            expected = toolchain_runtime.encode_intermediate_record(
                model.tracker.record, profile=model.policy.toolchain,
                launch=model.policy.config["toolchain_runtime"], executions=model.tracker.executions,
                returncode=0, limits=model.tracker.limits, reserve=charges.append,
            )
            publication = len(expected) + 128
            required = setup + sum(charges) + publication
            payload_size = len(expected) - len(toolchain_runtime.INTERMEDIATE_PREFIX)
            nodes = toolchain_runtime._json_shape(expected[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
            old_decode_boundary = (
                setup + 4 * toolchain_runtime.INTERMEDIATE_RECORD_LIMIT
                + syscall_guard._TOOLCHAIN_TRACKER_STORAGE + 4 * len(expected) + 4096
                + payload_size + 4096 + 4 * payload_size + 1024 * nodes + 8192 - 1
            )
            self.assertLess(required, old_decode_boundary)
        for allowance in (required, old_decode_boundary, required - 1):
            with self.subTest(admitted=allowance), _IntermediateModel(observation_limit=allowance) as model:
                model.complete()
                self.assertEqual(model.policy.observation_bytes, setup)
                if allowance >= required:
                    try:
                        with patch.object(toolchain_runtime.json, "loads", side_effect=AssertionError("producer decoded a duplicate graph")):
                            model.tracker.emit(0)
                    except syscall_guard.Violation as error:
                        self.fail("cost-derived exact emission admission refused: " + str(error))
                    self.assertEqual(model.policy.accessed, {expected})
                    self.assertEqual(model.policy.observation_bytes, required)
                    self.assertEqual(model.tracker.phase, "emitted")
                else:
                    with self.assertRaises(syscall_guard.Violation):
                        model.tracker.emit(0)
                    self.assertFalse(model.policy.accessed)
                    self.assertEqual(model.tracker.phase, "emitting")
                    self.assertGreater(model.policy.observation_bytes, allowance)
                self.assertFalse(model.handles)

    def test_producer_workspace_intervals_are_admitted_before_encoder_allocations(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        model = case.model()
        funded, peaks = [0], []
        def reserve(size):
            current, peak = tracemalloc.get_traced_memory()
            if funded[0]:
                peaks.append((peak, funded[0]))
                self.assertLessEqual(peak, funded[0])
            funded[0] += size
            tracemalloc.reset_peak()
        tracemalloc.start()
        try:
            wire = self.producer(model, reserve)
            current, peak = tracemalloc.get_traced_memory()
            self.assertLessEqual(peak, funded[0])
        finally:
            tracemalloc.stop()
        self.assertTrue(peaks)
        self.assertTrue(wire.isascii())
        self.assertGreater(funded[0], len(wire))

    def test_producer_plans_wire_nodes_and_real_workspace_before_record_encoding(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        model = case.model()
        model["receipt"]["scope"] = model["launch"]["scope"] = "MODEL-\u4e2d-\U00010000/root"
        plan_charges = []
        wire_size, workspace = toolchain_runtime._intermediate_encoding_plan(model["receipt"], plan_charges.append)
        nodes = toolchain_runtime._json_shape(toolchain_runtime.encoded(model["receipt"]).decode("ascii"))
        self.assertEqual(workspace, 8192 + 1024 * nodes + 4 * wire_size)
        required = 8192 + sum(plan_charges) + workspace + toolchain_runtime._json_cost(model["executions"])
        for allowance in (required, required - 1):
            charged, encoded_records = [0], []
            def reserve(size):
                charged[0] += size
                if charged[0] > allowance:
                    raise MakeProbeError("MODEL admission exhausted")
            original = toolchain_runtime.encoded
            def encode(value):
                if value is model["receipt"]:
                    self.assertEqual(charged[0], required)
                    encoded_records.append(True)
                return original(value)
            with patch.object(toolchain_runtime, "encoded", encode):
                if allowance == required:
                    wire = self.producer(model, reserve)
                    self.assertEqual(len(wire), wire_size)
                    self.assertTrue(wire.isascii())
                    self.assertEqual(encoded_records, [True])
                else:
                    with self.assertRaisesRegex(MakeProbeError, "MODEL admission exhausted"):
                        self.producer(model, reserve)
                    self.assertFalse(encoded_records)
        self.assertGreater(charged[0], allowance)

    def test_producer_and_hostile_parser_share_all_receipt_rejections(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        for defect in (
            "schema", "role", "scope", "binding", "actor", "object", "actual-mode", "flags",
            "request", "content", "close", "order", "retirement", "complete", "limit",
        ):
            model = case.model()
            record = model["receipt"]
            if defect == "schema":
                record["extra"] = 0
            elif defect == "role":
                record["writer"]["operand"]["argv_index"] += 1
            elif defect == "scope":
                record["scope"] += "-foreign"
            elif defect == "binding":
                record["binding"] = "0" * 64
            elif defect == "actor":
                record["actors"][1]["exec_record_sha256"] = "0" * 64
            elif defect == "object":
                record["writer"]["open"]["identity"][1] += 1
            elif defect == "actual-mode":
                record["writer"]["open"]["identity"][2] = stat.S_IFREG | 0o666
            elif defect == "flags":
                record["writer"]["open"]["flags"] |= os.O_APPEND
            elif defect == "request":
                record["writer"]["open"]["requested_mode"] = 0o644
            elif defect == "content":
                record["writer"]["completed"]["sha256"] = "0" * 64
            elif defect == "close":
                record["reader"]["completed"]["close_result"] = -1
            elif defect == "order":
                record["writer"]["exit"]["order"] = record["writer"]["completed"]["order"]
            elif defect == "retirement":
                record["retirement"]["after_identity"][6] = 1
            elif defect == "complete":
                record["complete"] = False
            else:
                model["limits"] = replace(model["limits"], write_limit=0)
            with self.subTest(defect=defect, consumer="producer"), self.assertRaises(MakeProbeError):
                self.producer(model, case.reserve)
            with self.subTest(defect=defect, consumer="hostile-wire"), self.assertRaises(MakeProbeError):
                case.parse(model, reserve=case.reserve)

    def test_producer_bounds_and_validation_refuse_before_output_or_publication(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        for defect in ("nodes", "depth", "wire", "scalar", "subclass", "cycle", "observation"):
            model = case.model()
            if defect == "nodes":
                model["receipt"]["extra"] = [0] * toolchain_runtime.INTERMEDIATE_NODE_LIMIT
            elif defect == "depth":
                nested = 0
                for _ in range(toolchain_runtime.INTERMEDIATE_DEPTH_LIMIT + 1):
                    nested = [nested]
                model["receipt"]["extra"] = nested
            elif defect == "wire":
                model["receipt"]["path"] = "x" * toolchain_runtime.INTERMEDIATE_RECORD_LIMIT
            elif defect == "scalar":
                model["receipt"]["creation"]["fd"] = 1 << 64
            elif defect == "subclass":
                class Foreign(dict):
                    pass
                model["receipt"] = Foreign(model["receipt"])
            elif defect == "cycle":
                model["receipt"]["extra"] = model["receipt"]
            else:
                model["limits"] = replace(model["limits"], observation_limit=1)
            with patch.object(toolchain_runtime, "encoded", side_effect=AssertionError("unadmitted encoder")):
                with self.subTest(defect=defect), self.assertRaises(MakeProbeError):
                    self.producer(model, case.reserve)
        with _IntermediateModel() as model:
            model.complete()
            model.tracker.record["retirement"]["path_absent"] = False
            with self.assertRaises(syscall_guard.Violation):
                model.tracker.emit(0)
            self.assertFalse(model.policy.accessed)
            self.assertEqual(model.tracker.phase, "emitting")
            with self.assertRaises(syscall_guard.Violation):
                model.tracker.emit(0)
            model.tracker.close()
            self.assertFalse(model.handles)
        with _IntermediateModel() as model:
            model.complete()
            model.policy.config["observation_count"] = sum(
                len(values) for values in model.policy.observation_attempts.values()
            )
            with self.assertRaisesRegex(syscall_guard.Violation, "aggregate filesystem-observation budget"):
                model.tracker.emit(0)
            self.assertFalse(model.policy.accessed)
            self.assertEqual(model.tracker.phase, "emitting")

    def test_producer_exact_wire_semantics_and_cumulative_verification_remain_bound(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        model = case.model()
        charges = []
        wire = self.producer(model, charges.append)
        canonical = case.parse(model, reserve=case.reserve)
        self.assertEqual(wire, toolchain_runtime.INTERMEDIATE_PREFIX + canonical.decode("ascii"))
        projected = toolchain_runtime.project_compile_identity(
            case.probes(model), canonical, model["roles"], reserve=case.reserve,
        )
        reordered = copy.deepcopy(model)
        reordered["receipt"] = dict(reversed(list(reordered["receipt"].items())))
        self.assertEqual(self.producer(reordered, case.reserve), wire)
        self.assertEqual(toolchain_runtime.project_compile_identity(
            case.probes(model), wire[len(toolchain_runtime.INTERMEDIATE_PREFIX):].encode("ascii"),
            model["roles"], reserve=case.reserve,
        ), projected)
        allowance = 2 * sum(charges) - 1
        charged = [0]
        def reserve(size):
            charged[0] += size
            if charged[0] > allowance:
                raise MakeProbeError("MODEL cumulative admission exhausted")
        self.assertEqual(self.producer(model, reserve), wire)
        with self.assertRaisesRegex(MakeProbeError, "MODEL cumulative admission exhausted"):
            self.producer(model, reserve)
        self.assertGreater(charged[0], allowance)

    def test_non_fd_scalar_collisions_do_not_acquire_intermediate_ownership(self):
        for descriptor in (0, 7):
            with self.subTest(MODEL_fd=descriptor), _IntermediateModel() as model:
                writer = model.writer(descriptor)
                before = copy.deepcopy(model.tracker.record)
                slots = tuple(model.tracker.descriptors)
                with model.memory_effects() as (_, grants):
                    for number, arguments, result in (
                        (9, (descriptor, 4096, syscall_guard.PROT_READ | syscall_guard.PROT_WRITE,
                             syscall_guard.MAP_PRIVATE | syscall_guard.MAP_ANONYMOUS, descriptor), 0x40000),
                        (10, (descriptor, 4096, syscall_guard.PROT_READ, descriptor, descriptor), 0),
                        (11, (descriptor, 4096, descriptor), 0),
                        (12, (descriptor, descriptor, descriptor), 4096),
                        (25, (descriptor, 4096, 8192, 1, descriptor), 0x40000),
                        (28, (descriptor, 4096, 0), 0),
                        (7, (descriptor, 0, 0), 0),
                        (23, (descriptor, 0, 0, 0), -errno.EINVAL),
                        (39, (descriptor, descriptor, descriptor), writer[0]),
                        (61, (descriptor, 0, 1), -errno.ECHILD),
                        (95, (descriptor,), 0),
                    ):
                        with self.subTest(syscall=number):
                            reached = []
                            try:
                                model.syscall(
                                    writer, number, *arguments, result=result,
                                    kernel=lambda: reached.append("MODEL"),
                                )
                            except syscall_guard.Violation as error:
                                self.fail("non-FD scalar became private ownership: " + str(error))
                            self.assertEqual(reached, ["MODEL"])
                            self.assertEqual(model.tracker.record, before)
                            self.assertEqual(tuple(model.tracker.descriptors), slots)
                            self.assertEqual(model.tracker.phase, "writer-open")
                            self.assertFalse(model.tracker.failed)
                            self.assertIsNone(writer[1].toolchain_pending)
                    self.assertTrue(grants)
                    self.assertLessEqual(model.policy.memory_peak, model.policy.config["memory_limit"])

    def test_mmap_uses_its_actual_fd_and_keeps_generic_mutable_backing_denial(self):
        for descriptor in (0, 7):
            with self.subTest(MODEL_fd=descriptor), _IntermediateModel() as model:
                writer = model.writer(descriptor)
                with model.memory_effects() as (runtime, grants):
                    writer[1].fds[11] = runtime
                    before = copy.deepcopy(model.tracker.record)
                    reached = []
                    model.syscall(
                        writer, 9, descriptor, 4096, syscall_guard.PROT_READ,
                        syscall_guard.MAP_PRIVATE, 11, result=0x40000, kernel=lambda: reached.append("MODEL"),
                    )
                    self.assertEqual(reached, ["MODEL"])
                    self.assertIn(runtime, model.policy.accessed)
                    self.assertEqual(model.tracker.record, before)
                    reached.clear()
                    with self.assertRaisesRegex(syscall_guard.Violation, "aggregate address-space budget"):
                        model.syscall(
                            writer, 9, 0, model.policy.config["memory_limit"], syscall_guard.PROT_READ,
                            syscall_guard.MAP_PRIVATE | syscall_guard.MAP_ANONYMOUS,
                            descriptor, kernel=lambda: reached.append("MODEL"),
                        )
                    self.assertFalse(reached)
                    self.assertTrue(grants)
            for aliased in (False, True):
                with self.subTest(MODEL_fd=descriptor, alias=aliased), _IntermediateModel() as model:
                    writer = model.writer(descriptor)
                    backing = 12 if aliased else descriptor
                    writer[1].fds[backing] = model.model["roles"].output.value
                    reached = []
                    with model.memory_effects(), self.assertRaisesRegex(
                        syscall_guard.Violation, "^mutable backing-file mappings/argument races are forbidden$",
                    ):
                        model.syscall(
                            writer, 9, 0x40000, 4096, syscall_guard.PROT_READ,
                            syscall_guard.MAP_PRIVATE, backing, result=0x50000,
                            kernel=lambda: reached.append("MODEL"),
                        )
                    self.assertFalse(reached)
                    self.assertEqual(model.tracker.phase, "writer-open")

    def test_dup_ignored_argument_is_not_a_destination_but_real_destinations_stay_denied(self):
        for descriptor in (0, 7):
            with self.subTest(MODEL_fd=descriptor), _IntermediateModel() as model:
                writer = model.writer(descriptor)
                with model.memory_effects() as (runtime, _):
                    writer[1].fds[11] = runtime
                    reached = []
                    model.syscall(
                        writer, 32, 11, descriptor, descriptor, result=12,
                        kernel=lambda: reached.append("MODEL"),
                    )
                    self.assertEqual(reached, ["MODEL"])
                    self.assertEqual(writer[1].fds[12], runtime)
                    model.syscall(writer, 72, 11, 3, descriptor)
                    for number, source, destination in (
                        (32, descriptor, 13), (33, descriptor, 13), (292, descriptor, 13),
                        (33, 11, descriptor), (292, 11, descriptor),
                    ):
                        reached.clear()
                        with self.assertRaises(syscall_guard.Violation):
                            model.syscall(
                                writer, number, source, destination, 0, result=13,
                                kernel=lambda: reached.append("MODEL"),
                            )
                        self.assertFalse(reached)
                        self.assertEqual(writer[1].fds[descriptor], model.model["roles"].output.value)

    def test_absolute_path_operations_ignore_dirfd_numbers_without_losing_path_authority(self):
        for descriptor in (0, 7):
            with self.subTest(MODEL_fd=descriptor), _IntermediateModel() as model:
                writer = model.writer(descriptor)
                before = copy.deepcopy(model.tracker.record)
                with model.memory_effects() as (runtime, _), model.readlink_paths(runtime):
                    reached = []
                    for number, c, d, result in (
                        (257, os.O_RDONLY, 0, 12),
                        (262, 0x3000, 0, 0),
                        (267, 0x3000, 64, -errno.EINVAL),
                    ):
                        model.syscall(
                            writer, number, descriptor, 0x2000, c, d, result=result,
                            kernel=lambda: reached.append("MODEL"),
                        )
                    self.assertEqual(reached, ["MODEL"] * 3)
                    self.assertEqual(writer[1].fds[12], runtime)
                    self.assertEqual(model.tracker.record, before)
                    self.assertEqual(model.tracker.descriptors[1], descriptor)
                with model.readlink_paths("relative-name"), self.assertRaises(syscall_guard.Violation):
                    model.syscall(writer, 257, descriptor, 0x2000, os.O_RDONLY)

    def test_real_zero_and_nonzero_fds_keep_io_metadata_close_and_receipt_binding(self):
        for descriptor in (0, 7):
            with self.subTest(MODEL_fd=descriptor), _IntermediateModel() as model:
                writer = model.writer(descriptor)
                model.syscall(writer, 5, descriptor, 0x3000)
                model.syscall(writer, 72, descriptor, 1, 0)
                model.syscall(writer, 72, descriptor, 3, 0)
                model.write(writer, descriptor=descriptor)
                model.close_actor(writer, descriptor=descriptor)
                model.exit_actor(writer)
                reader = model.actor(3)
                if descriptor == 0:
                    model.syscall(reader, 3, 0)
                model.open_actor(reader, os.O_RDONLY, 0, descriptor=descriptor)
                model.read_actor(reader, descriptor=descriptor)
                model.close_actor(reader, descriptor=descriptor)
                model.exit_actor(reader)
                def unlink():
                    model.exists, model.links = False, 0
                    model.ctime += 1
                model.syscall(model.driver, 87, 0x2000, kernel=unlink)
                model.exit_actor(model.driver)
                model.tracker.emit(0)
                wire, = model.policy.accessed
                proof = json.loads(wire[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
                self.assertEqual(proof["writer"]["open"]["fd"], descriptor)
                self.assertEqual(proof["reader"]["open"]["fd"], descriptor)
                self.assertTrue(proof["complete"])
                self.assertFalse(model.handles)

    def test_true_private_fd_mutations_aliases_and_foreign_actors_still_refuse(self):
        for descriptor in (0, 7):
            for number, b, c in ((8, 0, 0), (17, 0x1003, 4), (19, 0x1003, 1),
                                 (18, 0x1003, 4), (20, 0x1003, 1), (72, 2, 1), (72, 4, 0)):
                with self.subTest(MODEL_fd=descriptor, syscall=number), _IntermediateModel() as model:
                    writer = model.writer(descriptor)
                    reached = []
                    with self.assertRaises(syscall_guard.Violation):
                        model.syscall(writer, number, descriptor, b, c, kernel=lambda: reached.append("MODEL"))
                    self.assertFalse(reached)
            with _IntermediateModel() as model:
                writer = model.writer(descriptor)
                writer[1].fds[12] = model.model["roles"].output.value
                with self.assertRaises(syscall_guard.Violation):
                    model.syscall(writer, 0, 12, 0x1003, 4)
            with _IntermediateModel() as model:
                writer = model.writer(descriptor)
                model.policy.processes[writer[0]] = copy.copy(writer[1])
                with self.assertRaisesRegex(syscall_guard.Violation, "foreign process/exec/birth"):
                    model.syscall(writer, 39, descriptor)
            with _IntermediateModel() as model:
                model.sealed()
                reader = model.actor(3)
                if descriptor == 0:
                    model.syscall(reader, 3, 0)
                model.open_actor(reader, os.O_RDONLY, 0, descriptor=descriptor)
                with model.memory_effects() as (runtime, _):
                    reader[1].fds[descriptor] = runtime
                    with patch.object(syscall_guard.os, "readlink", return_value=model.policy.config["root"] + runtime):
                        with self.assertRaisesRegex(syscall_guard.Violation, "descriptor no longer names"):
                            model.syscall(reader, 0, descriptor, 0x1003, 4)

    def test_existing_writer_requested_modes_keep_same_actual_0600_object(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        for number in (2, 257):
            for mode in (0o600, 0o666):
                with self.subTest(number=number, MODEL_mode=mode), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    before = model.tracker.object_identity()
                    self.assertTrue(model.exists)
                    try:
                        model.open_actor(writer, flags, mode, number=number)
                    except syscall_guard.Violation as error:
                        self.fail("valid MODEL existing-object request refused: " + str(error))
                    after = model.tracker.object_identity()
                    self.assertEqual(before[:4], after[:4])
                    self.assertEqual(after[2], stat.S_IFREG | 0o600)
                    self.assertEqual(after[6], 1)
                    self.assertEqual(model.tracker.phase, "writer-open")
                    opened = model.tracker.record["writer"]["open"]
                    self.assertEqual((opened["requested_mode"], opened["flags"]), (mode, flags))
                    self.assertEqual(opened["identity"], list(after))
                    self.assertEqual(model.trace_fds[writer[0], 7], [0, flags])

    def test_existing_writer_modes_roundtrip_raw_without_changing_actual_semantics(self):
        case = ToolchainProtocolDataTests()
        case.setUp()
        raw, semantic = [], []
        for mode in (0o600, 0o666):
            with self.subTest(MODEL_mode=mode):
                model = case.model()
                opened = model["receipt"]["writer"]["open"]
                opened["flags"] = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                opened["requested_mode"] = mode
                try:
                    canonical = case.parse(model, reserve=case.reserve)
                except MakeProbeError as error:
                    self.fail("valid MODEL receipt refused: " + str(error))
                observed = json.loads(canonical)
                self.assertEqual(observed["writer"]["open"]["requested_mode"], mode)
                self.assertEqual(observed["writer"]["open"]["flags"], opened["flags"])
                self.assertEqual(observed["writer"]["open"]["identity"][2], stat.S_IFREG | 0o600)
                projected = toolchain_runtime.project_compile_identity(
                    case.probes(model), canonical, model["roles"], reserve=case.reserve,
                )
                self.assertEqual(projected["toolchain_semantics"]["intermediates"][0]["mode"], 0o600)
                raw.append(canonical)
                semantic.append(projected)
        self.assertEqual(len(raw), 2, "both coherent existing-object request modes must parse")
        self.assertNotEqual(raw[0], raw[1])
        self.assertEqual(semantic[0], semantic[1])

    def test_existing_writer_complete_models_bind_raw_request_and_proved_actual_mode(self):
        values = []
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        for mode in (0o600, 0o666):
            with self.subTest(MODEL_mode=mode), _IntermediateModel() as model:
                wire = model.finish(writer_request=(257, flags, mode))
                proof = json.loads(wire[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
                self.assertEqual(proof["writer"]["open"]["requested_mode"], mode)
                self.assertEqual(proof["writer"]["open"]["flags"], flags)
                self.assertEqual(proof["creation"]["requested_mode"], 0o600)
                self.assertTrue(all(
                    row[2] == stat.S_IFREG | 0o600 for row in (
                        proof["creation"]["identity"], proof["writer"]["open"]["identity"],
                        proof["writer"]["completed"]["identity"], proof["reader"]["open"]["identity"],
                        proof["retirement"]["after_identity"],
                    )
                ))
                self.assertTrue(proof["complete"])
                self.assertFalse(model.handles)
                values.append(proof["writer"]["completed"]["sha256"])
        self.assertEqual(values[0], values[1])

    def test_existing_writer_never_creates_repairs_or_accepts_a_foreign_preopen_object(self):
        for defect in ("absent", "inode", "actual-mode", "links", "symlink", "nonempty", "actor"):
            with self.subTest(defect=defect), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                if defect == "absent":
                    model.exists = False
                elif defect == "inode":
                    model.inode += 1
                elif defect == "actual-mode":
                    model.mode = stat.S_IFREG | 0o666
                elif defect == "links":
                    model.links = 2
                elif defect == "symlink":
                    model.mode = stat.S_IFLNK | 0o777
                elif defect == "nonempty":
                    model.body = b"not-empty"
                else:
                    model.policy.processes[writer[0]] = copy.copy(writer[1])
                attempted = []
                def kernel():
                    attempted.append(True)
                    raise AssertionError("MODEL writer must not repair/create this object")
                with self.assertRaises((OSError, syscall_guard.Violation)):
                    model.syscall(
                        writer, 257, -100, 0x2000, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                        0o666, result=7, kernel=kernel,
                    )
                self.assertFalse(attempted)
                self.assertIsNone(model.tracker.descriptors[1])
                self.assertNotIn("writer", model.tracker.record)

    def test_existing_writer_revalidates_postopen_object_entry_and_actual_mode(self):
        for defect in ("absent", "inode", "actual-mode", "links", "symlink", "fd-object"):
            with self.subTest(defect=defect), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                original = model.stat
                def stat_value(path, **options):
                    info = original(path, **options)
                    if defect == "fd-object" and str(path).startswith("/proc/"):
                        info.st_ino += 1
                    return info
                def kernel():
                    model.trace_fds[writer[0], 7] = [0, flags]
                    if defect == "absent":
                        model.exists = False
                    elif defect == "inode":
                        model.inode += 1
                    elif defect == "actual-mode":
                        model.mode = stat.S_IFREG | 0o666
                    elif defect == "links":
                        model.links = 2
                    elif defect == "symlink":
                        model.mode = stat.S_IFLNK | 0o777
                with patch.object(syscall_guard.os, "stat", stat_value):
                    with self.assertRaises((OSError, syscall_guard.Violation)):
                        model.syscall(writer, 257, -100, 0x2000, flags, 0o666, result=7, kernel=kernel)
                self.assertIsNone(model.tracker.descriptors[1])
                with self.assertRaises(syscall_guard.Violation):
                    model.tracker.emit(0)
                self.assertFalse(model.policy.accessed)

    def test_existing_writer_extension_keeps_creator_bad_request_flags_and_actual_receipt_denials(self):
        for flags, mode in (
            (os.O_WRONLY | os.O_CREAT, 0), (os.O_WRONLY | os.O_CREAT, 0o644),
            (os.O_WRONLY | os.O_CREAT, 0o664), (os.O_WRONLY | os.O_CREAT, 0o777),
            (os.O_WRONLY | os.O_CREAT, 0o4000),
            (os.O_WRONLY | os.O_TRUNC, 0o600), (os.O_WRONLY | os.O_TRUNC, 0o666),
            (os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666),
            (os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666),
            (os.O_WRONLY | os.O_CREAT | os.O_PATH, 0o666),
        ):
            with self.subTest(flags=flags, mode=mode), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                with self.assertRaises(syscall_guard.Violation):
                    model.open_actor(writer, flags, mode)
                self.assertNotIn((writer[0], 7), model.trace_fds)
        with _IntermediateModel() as model:
            driver = model.actor(1)
            with self.assertRaises(syscall_guard.Violation):
                model.open_actor(driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
            self.assertFalse(model.exists)
        case = ToolchainProtocolDataTests()
        case.setUp()
        for defect in ("creator-mode", "actual-mode", "inode", "links", "symlink", "requested-mode", "flags"):
            model = case.model()
            opened = model["receipt"]["writer"]["open"]
            opened["flags"] |= os.O_CREAT
            opened["requested_mode"] = 0o666
            if defect == "creator-mode":
                model["receipt"]["creation"]["requested_mode"] = 0o666
            elif defect == "actual-mode":
                opened["identity"][2] = stat.S_IFREG | 0o666
            elif defect == "inode":
                opened["identity"][1] += 1
            elif defect == "links":
                opened["identity"][6] = 2
            elif defect == "symlink":
                opened["identity"][2] = stat.S_IFLNK | 0o777
            elif defect == "requested-mode":
                opened["requested_mode"] = 0o664
            else:
                opened["flags"] |= os.O_EXCL
            with self.subTest(receipt=defect), self.assertRaises(MakeProbeError):
                case.parse(model, reserve=case.reserve)

    def test_writer_mode_refusal_reports_only_validated_bounded_fields(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW
        for number, form in ((2, "open"), (257, "openat")):
            for mode in (0, 0o644, 0o777):
                with self.subTest(number=number, MODEL_mode=mode), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    before = copy.deepcopy(model.tracker.record)
                    with self.assertRaises(syscall_guard.Violation) as refused:
                        model.open_actor(writer, flags, mode, number=number)
                    self.assertEqual(
                        str(refused.exception),
                        "toolchain writer changed its requested mode "
                        f"[syscall={number} form={form} role=writer phase=writer-exec "
                        f"flags=0x{flags:x} create=yes mode=0o{mode:03o}]",
                    )
                    self.assertLessEqual(len(str(refused.exception).encode("ascii")), 256)
                    self.assertEqual(model.tracker.record, before)
                    self.assertEqual(model.tracker.phase, "writer-exec")
                    self.assertIsNone(model.tracker.descriptors[1])
                    self.assertNotIn((writer[0], 7), model.trace_fds)

    def test_writer_mode_unused_out_of_domain_and_unknown_values_do_not_escape(self):
        for create, mode, label in (
            (False, 0o666, "unused"), (False, 1 << 63, "unused"),
            (True, 0o4000, "out-of-domain"), (True, (1 << 64) - 1, "out-of-domain"),
        ):
            with self.subTest(create=create, MODEL_mode=mode), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                flags = os.O_WRONLY | os.O_TRUNC | (os.O_CREAT if create else 0)
                with self.assertRaises(syscall_guard.Violation) as refused:
                    model.open_actor(writer, flags, mode)
                message = str(refused.exception)
                self.assertIn(f"create={'yes' if create else 'no'} mode={label}", message)
                self.assertNotIn(str(mode), message)
                self.assertNotIn(oct(mode), message)
                self.assertNotIn("/work/", message)
                self.assertLessEqual(len(message), 256)
        class OpaqueMode:
            def __str__(self):
                raise AssertionError("mode stringification escaped")
            def __repr__(self):
                raise AssertionError("mode repr escaped")
            def __format__(self, spec):
                raise AssertionError("mode formatting escaped")
        with _IntermediateModel() as model:
            model.created()
            pid, state = model.actor(2)
            path = model.model["roles"].output.value
            state.path_context = path, -100, None
            model.tracker.note_path(state, path)
            registers = SimpleNamespace(
                orig_rax=2, rdi=0x2000, rsi=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                rdx=OpaqueMode(), r10=0,
            )
            with self.assertRaises(syscall_guard.Violation) as refused:
                model.tracker.enter(pid, state, registers)
            self.assertIn("create=yes mode=unknown", str(refused.exception))

    def test_writer_mode_diagnostic_admission_failure_preserves_first_refusal(self):
        for failure in (syscall_guard.Violation("MODEL diagnostic admission"), MemoryError("MODEL allocation")):
            with self.subTest(failure=type(failure).__name__), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                with patch.object(model.tracker, "reserve", side_effect=failure) as reserve:
                    with self.assertRaises(syscall_guard.Violation) as refused:
                        model.open_actor(writer, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                reserve.assert_called_once_with(4096)
                self.assertEqual(str(refused.exception), "toolchain writer changed its requested mode")
                self.assertIs(refused.exception.__cause__, failure)
                self.assertEqual(model.tracker.phase, "writer-exec")
                self.assertIsNone(model.tracker.descriptors[1])
                model.tracker.close()
                self.assertFalse(model.handles)
                self.assertTrue(model.exists)
        with _IntermediateModel() as model:
            model.created()
            writer = model.actor(2)
            before = model.policy.observation_bytes
            model.policy.config["observation_limit"] = before
            with self.assertRaises(syscall_guard.Violation) as refused:
                model.open_actor(writer, os.O_WRONLY | os.O_CREAT, 0o644)
            self.assertEqual(str(refused.exception), "toolchain writer changed its requested mode")
            self.assertEqual(model.policy.observation_bytes, before + 4096)

    def test_writer_mode_diagnostic_does_not_replace_actor_path_phase_or_flag_guards(self):
        for defect in ("actor", "path", "alias", "phase", "flags"):
            with self.subTest(defect=defect), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                if defect == "actor":
                    model.policy.processes[writer[0]] = copy.copy(writer[1])
                elif defect == "phase":
                    model.tracker.phase = "reader-exec"
                elif defect == "flags":
                    flags |= os.O_APPEND
                path = (
                    "/work/ccFOREGN.s" if defect == "path"
                    else "/work/./ccL3VdjV.s" if defect == "alias"
                    else model.model["roles"].output.value
                )
                with model.readlink_paths(path), self.assertRaises(syscall_guard.Violation) as refused:
                    model.open_actor(writer, flags, 0o666)
                self.assertNotIn("toolchain writer changed its requested mode", str(refused.exception))
                self.assertNotIn("create=", str(refused.exception))
                self.assertNotIn((writer[0], 7), model.trace_fds)

    def test_writer_mode_accepted_forms_remain_exact_for_open_and_openat(self):
        for number in (2, 257):
            for create, mode in ((False, 0), (True, 0o600), (True, 0o666)):
                with self.subTest(number=number, create=create), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    flags = os.O_WRONLY | os.O_TRUNC | (os.O_CREAT if create else 0)
                    model.open_actor(writer, flags, mode, number=number)
                    self.assertEqual(model.tracker.phase, "writer-open")
                    opened = model.tracker.record["writer"]["open"]
                    self.assertEqual((opened["flags"], opened["requested_mode"]), (flags, mode))
                    self.assertEqual(opened["syscall"], "open" if number == 2 else "openat")
                    self.assertEqual(model.tracker.descriptors[1], 7)

    def test_readlink_metadata_models_preserve_state_and_complete_both_forms(self):
        for number in (89, 267):
            for result in (-errno.EINVAL, -errno.EIO):
                with self.subTest(number=number, MODEL_result=result), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    before = copy.deepcopy(model.tracker.record)
                    position = (
                        model.tracker.phase, model.tracker.order, tuple(model.tracker.descriptors),
                        model.tracker.written, model.tracker.read_bytes,
                    )
                    with model.readlink_paths():
                        try:
                            model.readlink_query(writer, number, result=result)
                        except syscall_guard.Violation as error:
                            self.fail("admitted MODEL regular-file metadata rejected: " + str(error))
                    self.assertEqual(model.tracker.record, before)
                    self.assertEqual(position, (
                        model.tracker.phase, model.tracker.order, tuple(model.tracker.descriptors),
                        model.tracker.written, model.tracker.read_bytes,
                    ))
                    self.assertFalse(model.tracker.failed)
                    self.assertIsNone(writer[1].toolchain_pending)
                    self.assertEqual(len(model.handles), 2)
                    model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                    model.write(writer)
                    model.close_actor(writer)
                    model.exit_actor(writer)
                    reader = model.actor(3)
                    model.open_actor(reader, os.O_RDONLY, 0)
                    model.read_actor(reader)
                    model.close_actor(reader)
                    model.exit_actor(reader)
                    def unlink():
                        model.exists, model.links = False, 0
                        model.ctime += 1
                    model.syscall(model.driver, 87, 0x2000, kernel=unlink)
                    model.exit_actor(model.driver)
                    model.tracker.emit(0)
                    proof, = model.policy.accessed
                    self.assertTrue(json.loads(proof[len(toolchain_runtime.INTERMEDIATE_PREFIX):])["complete"])
                    self.assertFalse(model.handles)

    def test_readlink_metadata_does_not_replace_the_existing_observer_or_capture_buffer(self):
        for number in (89, 267):
            with self.subTest(number=number), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                path = model.model["roles"].output.value
                model.policy.config["runtime_files"] = [path]
                attempts = len(model.policy.observation_attempts["accessed"])
                with model.readlink_paths(), patch.object(
                    syscall_guard, "memory", return_value=b"?" * 64,
                ) as memory:
                    model.readlink_query(writer, number)
                self.assertEqual(memory.call_count, 2)
                self.assertEqual([call.args[2] for call in memory.call_args_list], [64, 64])
                row, = model.policy.metadata
                self.assertEqual(tuple(row[:7]), (number, path, 0, 0, 64, 0, -errno.EINVAL))
                self.assertEqual(row[7:], [(b"?" * 64).hex()] * 2)
                self.assertEqual(len(model.policy.metadata_seen), 1)
                self.assertEqual(len(model.policy.observation_attempts["accessed"]), attempts + 2)
                self.assertFalse(model.policy.accessed)
                self.assertFalse(model.tracker.failed)
                self.assertEqual(model.tracker.phase, "writer-exec")

    def test_readlink_metadata_refuses_uncreated_foreign_empty_dirfd_and_alias_contexts(self):
        with _IntermediateModel() as model:
            driver = model.actor(1)
            with model.readlink_paths(), self.assertRaises(syscall_guard.Violation):
                model.readlink_query(driver, 89)
        for number, spelling, dirfd in (
            (89, "/work/ccFOREGN.s", -100),
            (89, "/work/./ccL3VdjV.s", -100),
            (89, "/work/../work/ccL3VdjV.s", -100),
            (89, "/work-alias/ccL3VdjV.s", -100),
            (89, "ccL3VdjV.s", -100),
            (267, "/work/ccL3VdjV.s", 7),
            (267, "/work/ccL3VdjV.s", 42),
            (267, "", 7),
        ):
            with self.subTest(number=number, spelling=spelling, dirfd=dirfd), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                writer[1].cwd = "/work"
                returned = []
                with model.readlink_paths(spelling), self.assertRaises(syscall_guard.Violation):
                    model.readlink_query(writer, number, dirfd=dirfd, kernel=lambda: returned.append(True))
                self.assertFalse(returned)
                self.assertEqual(model.tracker.phase, "writer-open")

    def test_readlink_metadata_revalidates_actor_root_path_pin_type_and_content(self):
        for defect in ("actor", "root", "path", "inode", "symlink", "content", "phase"):
            for number in (89, 267):
                with self.subTest(defect=defect, number=number), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                    model.write(writer)
                    before = copy.deepcopy(model.tracker.record)
                    with model.readlink_paths() as literal:
                        def changed():
                            if defect == "actor":
                                model.policy.processes[writer[0]] = copy.copy(writer[1])
                            elif defect == "root":
                                model.policy.config["root"] = "/foreign/command-root-1"
                            elif defect == "path":
                                literal[0] = "/work/ccFOREGN.s"
                            elif defect == "inode":
                                model.inode += 1
                            elif defect == "symlink":
                                model.mode = stat.S_IFLNK | 0o777
                            elif defect == "content":
                                model.body = b"x" * len(model.body)
                                model.mtime += 1
                                model.ctime += 1
                            else:
                                model.tracker.phase = "writer-closed"
                        with self.assertRaises(syscall_guard.Violation):
                            model.readlink_query(writer, number, kernel=changed)
                    self.assertEqual(model.tracker.record, before)
                    self.assertFalse(model.policy.accessed)

    def test_readlink_metadata_rejects_success_and_exhaustion_without_creating_proof(self):
        for number in (89, 267):
            for result in (0, 1, 65):
                with self.subTest(number=number, MODEL_result=result), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    before = copy.deepcopy(model.tracker.record)
                    with model.readlink_paths(), self.assertRaisesRegex(
                        syscall_guard.Violation, "unexpected readlink success",
                    ):
                        model.readlink_query(writer, number, result=result)
                    self.assertEqual(model.tracker.record, before)
                    self.assertEqual(model.tracker.phase, "writer-exec")
                    self.assertFalse(model.policy.accessed)
            for boundary in ("entry", "exit"):
                with self.subTest(number=number, boundary=boundary), _IntermediateModel() as model:
                    model.created()
                    writer = model.actor(2)
                    before = model.policy.observation_bytes
                    if boundary == "entry":
                        model.policy.config["observation_limit"] = before
                    returned = []
                    def kernel():
                        returned.append(True)
                        model.policy.config["observation_limit"] = model.policy.observation_bytes
                    with model.readlink_paths(), self.assertRaises(syscall_guard.Violation):
                        model.readlink_query(writer, number, kernel=kernel)
                    self.assertEqual(returned, [] if boundary == "entry" else [True])
                    self.assertGreater(model.policy.observation_bytes, before)
                    model.tracker.close()
                    self.assertFalse(model.handles)
                    self.assertTrue(model.exists)

    def test_actual_unsupported_entry_has_bounded_authenticated_attribution(self):
        for role in ("driver", "writer", "reader"):
            with self.subTest(role=role), _IntermediateModel() as model:
                if role == "driver":
                    actor = model.actor(1)
                    model.open_actor(actor, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                elif role == "writer":
                    model.created()
                    actor = model.actor(2)
                    model.open_actor(actor, os.O_WRONLY | os.O_TRUNC, 0)
                else:
                    model.sealed()
                    actor = model.actor(3)
                    model.open_actor(actor, os.O_RDONLY, 0)
                phase = model.tracker.phase
                before = model.policy.observation_bytes
                with self.assertRaises(syscall_guard.Violation) as refused:
                    model.syscall(actor, 72, 7, 2, 1)
                self.assertEqual(
                    str(refused.exception),
                    "toolchain intermediate used an unsupported I/O or mutation form "
                    f"[syscall=72 role={role} phase={phase} owned=yes op=fcntl command=2 flags=0x1]",
                )
                self.assertEqual(model.policy.observation_bytes - before, 4096)
                self.assertEqual(model.tracker.phase, phase)
                self.assertFalse(model.policy.accessed)
                self.assertLessEqual(len(str(refused.exception)), 256)

    def test_related_terminal_families_report_only_closed_discriminators(self):
        cases = (
            (72, 4, os.O_APPEND | os.O_NONBLOCK, "fcntl command=4 flags=0xc00"),
            (72, 5, 0x1234567812345678, "fcntl command=5 flags=not-recorded"),
            (72, 6, 0x2345678923456789, "fcntl command=6 flags=not-recorded"),
            (72, 1031, 0x1234567812345678, "fcntl command=1031 flags=not-recorded"),
            (74, 0x1234567812345678, 0, "other"),
            (75, 0, 0x1234567812345678, "other"),
            (73, 0x1234567812345678, 0, "other"),
            (16, 0x5401, 0x1234567812345678, "other"),
        )
        for number, b, c, operation in cases:
            with self.subTest(number=number, operation=operation), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                with self.assertRaises(syscall_guard.Violation) as refused:
                    model.syscall(writer, number, 7, b, c)
                message = str(refused.exception)
                self.assertIn(f"syscall={number} role=writer phase=writer-open owned=yes op={operation}", message)
                self.assertNotIn(str(0x1234567812345678), message)
                self.assertNotIn("/work/", message)
                self.assertLessEqual(len(message.encode("ascii")), 256)
                self.assertEqual(model.trace_fds[writer[0], 7][0], 0)

    def test_path_operation_reports_no_owned_descriptor_without_exposing_pointer(self):
        with _IntermediateModel() as model:
            driver = model.actor(1)
            with self.assertRaises(syscall_guard.Violation) as refused:
                model.syscall(driver, 85, 0x1234567812345678, 0o600)
            self.assertIn("syscall=85 role=driver phase=armed owned=no op=other", str(refused.exception))
            self.assertNotIn(str(0x1234567812345678), str(refused.exception))
            self.assertFalse(model.exists)

    def test_unknown_flags_and_phase_never_format_arbitrary_values(self):
        class ForeignPhase(str):
            def __str__(self):
                raise AssertionError("untrusted phase stringified")
            def __format__(self, spec):
                raise AssertionError("untrusted phase formatted")
        for command, flags in ((2, 3), (2, (1 << 64) - 1), (4, 1 << 63)):
            with self.subTest(command=command), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                model.tracker.phase = ForeignPhase("/private/path secret environment content")
                with self.assertRaises(syscall_guard.Violation) as refused:
                    model.syscall(writer, 72, 7, command, flags)
                message = str(refused.exception)
                self.assertIn("phase=unknown", message)
                self.assertIn(f"command={command} flags=unknown", message)
                self.assertNotIn("private", message)
                self.assertNotIn(str(flags), message)
        with _IntermediateModel() as model:
            error = model.tracker._unsupported_operation(
                (1 << 64) - 1, 0, None, SimpleNamespace(rsi=object(), rdx=object()),
            )
            self.assertIn("syscall=18446744073709551615 role=unknown", str(error))
            self.assertLessEqual(len(str(error)), 256)
            error = model.tracker._unsupported_operation(
                72, 2, True, SimpleNamespace(rsi=1 << 100, rdx=object()),
            )
            self.assertIn("op=fcntl command=unknown", str(error))

    def test_attribution_admission_and_allocation_failure_preserve_original_refusal(self):
        for failure in (syscall_guard.Violation("inert diagnostic admission"), MemoryError("inert diagnostic allocation")):
            with self.subTest(failure=type(failure).__name__), _IntermediateModel() as model:
                model.created()
                writer = model.actor(2)
                model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                with patch.object(model.tracker, "reserve", side_effect=failure) as reserve:
                    with self.assertRaises(syscall_guard.Violation) as refused:
                        model.syscall(writer, 72, 7, 2, 1)
                reserve.assert_called_once_with(4096)
                self.assertEqual(str(refused.exception), "toolchain intermediate used an unsupported I/O or mutation form")
                self.assertIs(refused.exception.__cause__, failure)
                self.assertEqual(model.tracker.phase, "writer-open")
                self.assertFalse(model.policy.accessed)
                model.tracker.close()
                self.assertFalse(model.handles)
        with _IntermediateModel() as model:
            model.created()
            writer = model.actor(2)
            model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
            before = model.policy.observation_bytes
            model.policy.config["observation_limit"] = before
            with self.assertRaises(syscall_guard.Violation) as refused:
                model.syscall(writer, 72, 7, 2, 1)
            self.assertEqual(str(refused.exception), "toolchain intermediate used an unsupported I/O or mutation form")
            self.assertIsInstance(refused.exception.__cause__, syscall_guard.Violation)
            self.assertEqual(model.policy.observation_bytes, before + 4096)

    def test_diagnostic_workspace_and_wire_bounds_ignore_unbounded_phase_text(self):
        with _IntermediateModel() as model:
            model.tracker.phase = "private-path-environment-content" * 4096
            registers = SimpleNamespace(rsi=4, rdx=os.O_APPEND | os.O_NONBLOCK)
            tracemalloc.start()
            try:
                error = model.tracker._unsupported_operation(72, 3, True, registers)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLessEqual(peak, 4096)
            self.assertLessEqual(len(str(error).encode("ascii")), 256)
            self.assertIn("phase=unknown", str(error))
            self.assertNotIn("private", str(error))

    def test_earlier_refusals_and_accepted_operations_do_not_enter_attribution(self):
        with _IntermediateModel() as model:
            model.created()
            writer = model.actor(2)
            model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
            with patch.object(model.tracker, "_unsupported_operation", side_effect=AssertionError("attribution reached")):
                with self.assertRaisesRegex(syscall_guard.Violation, "^toolchain intermediate used an unsupported I/O or descriptor alias$"):
                    model.syscall(writer, 8, 7, 0, 0)
                with self.assertRaisesRegex(syscall_guard.Violation, "^unknown fcntl operation$"):
                    model.syscall(writer, 72, 7, 9999, 0)
                model.syscall(writer, 72, 7, 1, 0)
                model.syscall(writer, 72, 7, 3, 0)
        with _IntermediateModel() as model, patch.object(
            model.tracker, "_unsupported_operation", side_effect=AssertionError("attribution reached"),
        ):
            proof = json.loads(model.finish()[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
            self.assertTrue(proof["complete"])
            self.assertEqual(proof["writer"]["completed"]["sha256"], hashlib.sha256(model.expected).hexdigest())

    def test_tracker_admission_refuses_before_allocating_the_unarmed_state(self):
        with _IntermediateModel() as model:
            model.policy.config["observation_limit"] = model.policy.observation_bytes
            with patch.object(syscall_guard, "_ToolchainIntermediate") as allocate:
                with self.assertRaises(syscall_guard.Violation):
                    model.policy.reserve_toolchain_intermediate()
                allocate.assert_not_called()
            self.assertFalse(model.handles)

    def test_each_pin_and_fdinfo_failure_retains_only_owned_cleanup_obligations(self):
        failures = (
            "workspace-open", "workspace-identity", "file-open", "file-identity",
            "fdinfo-open", "fdinfo-read", "fdinfo-flags", "fdinfo-close", "pread",
        )
        for defect in failures:
            with self.subTest(defect=defect), _IntermediateModel() as model:
                if defect.startswith("fdinfo") or defect == "pread":
                    model.created()
                    writer = model.actor(2)
                elif defect.startswith("file"):
                    model.driver = model.actor(1)
                events = []
                original_open, original_fstat = model.open, model.fstat
                original_read, original_close, original_pread = model.read, model.close, model.pread
                def opening(path, flags, **kwargs):
                    selected = (
                        defect == "workspace-open" and str(path).endswith("/work")
                        or defect == "file-open" and kwargs.get("dir_fd") is not None
                        or defect == "fdinfo-open" and "/fdinfo/" in str(path)
                    )
                    if selected:
                        events.append("open")
                        raise OSError("inert acquisition failure")
                    return original_open(path, flags, **kwargs)
                def identity(descriptor):
                    info = original_fstat(descriptor)
                    kind = model.handles[descriptor][0]
                    if (defect, kind) in {("workspace-identity", "workspace"), ("file-identity", "file")}:
                        events.append("identity")
                        info.st_ino += 1
                    return info
                def reading(descriptor, count):
                    if defect == "fdinfo-read":
                        events.append("read")
                        raise OSError("inert fdinfo failure")
                    if defect == "fdinfo-flags":
                        events.append("flags")
                        return b"pos:\t0\nflags:\t2000\n"
                    return original_read(descriptor, count)
                def closing(descriptor):
                    if defect == "fdinfo-close" and model.handles.get(descriptor, (None,))[0] == "fdinfo":
                        events.append(("uncertain-close", descriptor))
                        raise OSError("inert uncertain fdinfo close")
                    return original_close(descriptor)
                def content(descriptor, count, offset):
                    if defect == "pread":
                        events.append("pread")
                        raise OSError("inert pinned read failure")
                    return original_pread(descriptor, count, offset)
                with (
                    patch.object(syscall_guard.os, "open", opening),
                    patch.object(syscall_guard.os, "fstat", identity),
                    patch.object(syscall_guard.os, "read", reading),
                    patch.object(syscall_guard.os, "close", closing),
                    patch.object(syscall_guard.os, "pread", content),
                    self.assertRaises((OSError, syscall_guard.Violation)),
                ):
                    if defect.startswith("workspace"):
                        model.actor(1)
                    elif defect.startswith("file"):
                        model.open_actor(model.driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    else:
                        model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                        if defect == "pread":
                            model.write(writer)
                            model.close_actor(writer)
                            model.exit_actor(writer)
                self.assertTrue(events)
                self.assertLessEqual(model.peak, 3)
                with self.assertRaises(syscall_guard.Violation):
                    model.tracker.emit(0)
                uncertain = [event[1] for event in events if type(event) is tuple]
                before_cleanup = list(model.closed)
                model.tracker.close()
                self.assertEqual((model.tracker.workspace_fd, model.tracker.file_fd, model.tracker.fdinfo_fd), (-1, -1, -1))
                self.assertTrue(all(
                    model.closed.count(descriptor) == before_cleanup.count(descriptor)
                    for descriptor in uncertain
                ))
                self.assertFalse(model.policy.accessed)

    def test_actual_policy_hooks_complete_one_pinned_object_with_peak_three(self):
        with _IntermediateModel() as model:
            value = model.finish()
            receipt = json.loads(value[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
            self.assertEqual(receipt["writer"]["completed"]["sha256"], hashlib.sha256(model.expected).hexdigest())
            self.assertEqual(receipt["reader"]["completed"]["read_bytes"], len(model.expected))
            self.assertEqual(receipt["retirement"]["after_identity"][6], 0)
            self.assertEqual([row["birth_sequence"] for row in receipt["actors"]], [1, 2, 3])
            self.assertTrue(receipt["complete"])
            self.assertEqual(model.peak, 3)
            self.assertFalse(model.handles)
            self.assertEqual(len(model.preads), 2)
            self.assertTrue(model.peeks)
            self.assertTrue(all(address % 8 == 0 for address in model.peeks))
            self.assertFalse(model.policy.processes)

    def test_owned_actor_identity_and_inherited_descriptor_aliases_refuse(self):
        with _IntermediateModel() as model:
            driver = model.actor(1)
            state = copy.copy(driver[1])
            model.policy.processes[driver[0]] = state
            with self.assertRaises(syscall_guard.Violation):
                model.tracker.actor(driver[0], state)
        with _IntermediateModel() as model:
            driver = model.actor(1)
            model.open_actor(driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            registers = syscall_guard.Registers()
            registers.orig_rax = 57
            with self.assertRaises(syscall_guard.Violation):
                model.tracker.enter(driver[0], driver[1], registers)
            self.assertIsNone(driver[1].clone().toolchain_birth_sequence)
            self.assertIsNone(driver[1].clone().toolchain_exec_sequence)

    def test_creation_object_identity_mode_and_link_failures_do_not_complete(self):
        for field, value in (("inode", 99), ("mode", stat.S_IFREG | 0o644), ("links", 2)):
            with self.subTest(field=field), _IntermediateModel() as model:
                model.created()
                setattr(model, field, value)
                with self.assertRaises(syscall_guard.Violation):
                    model.tracker.object_identity()
                with self.assertRaises(syscall_guard.Violation):
                    model.tracker.emit(0)
        with _IntermediateModel() as model:
            driver = model.actor(1)
            with self.assertRaises(syscall_guard.Violation):
                model.syscall(driver, 2, 0x2000, os.O_RDWR | os.O_CREAT, 0o600, result=7)
            self.assertFalse(model.exists)

    def test_reader_content_offset_and_terminal_barriers_are_independent(self):
        for defect in ("content", "offset", "partial", "reader-before-writer-exit"):
            with self.subTest(defect=defect), _IntermediateModel() as model:
                if defect == "reader-before-writer-exit":
                    model.created()
                    writer = model.actor(2)
                    model.open_actor(writer, os.O_WRONLY | os.O_TRUNC, 0)
                    model.write(writer)
                    model.close_actor(writer)
                    with self.assertRaises(syscall_guard.Violation):
                        model.actor(3)
                    continue
                model.sealed()
                reader = model.actor(3)
                model.open_actor(reader, os.O_RDONLY, 0)
                if defect == "offset":
                    model.trace_fds[reader[0], 7][0] = 1
                    with self.assertRaises(syscall_guard.Violation):
                        model.read_actor(reader)
                else:
                    data = b"x" * len(model.body) if defect == "content" else model.body[:-1]
                    model.read_actor(reader, data)
                    with self.assertRaises(syscall_guard.Violation):
                        model.close_actor(reader)

    def test_stopped_memory_hash_uses_returned_words_and_preadmission(self):
        with _IntermediateModel() as model:
            model.memory = b"abc" + b"payload" + b"\0" * 8
            digest = hashlib.sha256()
            before = model.policy.observation_bytes
            count = syscall_guard._toolchain_memory_digest(model.tracker, 401, 0x1003, 64, 7, digest)
            self.assertEqual(count, 7)
            self.assertEqual(digest.digest(), hashlib.sha256(b"payload").digest())
            self.assertEqual(len(model.peeks), 2)
            self.assertGreaterEqual(model.policy.observation_bytes - before, 64 + 72)
            model.peeks.clear()
            syscall_guard._toolchain_memory_digest(model.tracker, 401, 0x1003, 64, 0, digest)
            self.assertFalse(model.peeks)
            model.policy.config["observation_limit"] = model.policy.observation_bytes
            with self.assertRaises(syscall_guard.Violation):
                syscall_guard._toolchain_memory_digest(model.tracker, 401, 0x1003, 64, 7, digest)
            self.assertFalse(model.peeks)

    def test_stopped_memory_workspace_stays_fixed_at_the_maximum_request(self):
        data = b"A" * syscall_guard.SYSCALL_MEMORY_LIMIT
        expected = hashlib.sha256(data).digest()
        with _IntermediateModel(data) as model:
            digest = hashlib.sha256()
            word = int.from_bytes(b"A" * 8, "little")
            with patch.object(syscall_guard, "ptrace", new=lambda *args: word):
                tracemalloc.start()
                try:
                    returned = syscall_guard._toolchain_memory_digest(
                        model.tracker, 401, 0x1000, len(data), len(data), digest,
                    )
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
            self.assertEqual(returned, len(data))
            self.assertEqual(digest.digest(), expected)
            self.assertLessEqual(peak, syscall_guard._TOOLCHAIN_WORD_SCRATCH)

    def test_unsupported_io_refuses_before_vector_capture_or_descriptor_change(self):
        for number, a, b, c in (
            (17, 7, 0x1003, 4), (19, 7, 0x1003, 1),
            (18, 7, 0x1003, 4), (20, 7, 0x1003, 1),
            (8, 7, 0, 0), (32, 7, 0, 0), (33, 0, 7, 0),
            (72, 7, 2, 0), (16, 7, 0x5401, 0),
        ):
            with self.subTest(number=number), _IntermediateModel() as model:
                model.sealed()
                reader = model.actor(3)
                model.open_actor(reader, os.O_RDONLY, 0)
                with self.assertRaises(syscall_guard.Violation):
                    model.syscall(reader, number, a, b, c)
                self.assertEqual(model.trace_fds[reader[0], 7][0], 0)

    def test_readonly_pin_hash_is_chunked_and_detects_changed_content(self):
        with _IntermediateModel(b"a" * (65536 + 7)) as model:
            model.sealed()
            self.assertEqual(model.preads, [(65536, 0), (7, 65536)])
            model.body = b"b" * len(model.body)
            reader = model.actor(3)
            model.open_actor(reader, os.O_RDONLY, 0)
            for data in (model.body[:65536], model.body[65536:]):
                model.read_actor(reader, data)
            with self.assertRaises(syscall_guard.Violation):
                model.close_actor(reader)

    def test_missing_failed_or_false_retirement_never_emits_a_success_receipt(self):
        for defect in ("missing", "failed", "still-present", "still-linked"):
            with self.subTest(defect=defect), _IntermediateModel() as model:
                model.consumed()
                if defect == "missing":
                    with self.assertRaises(syscall_guard.Violation):
                        model.exit_actor(model.driver)
                elif defect == "failed":
                    model.syscall(model.driver, 87, 0x2000, result=-1)
                    with self.assertRaises(syscall_guard.Violation):
                        model.exit_actor(model.driver)
                else:
                    def unlink():
                        model.exists = defect == "still-present"
                        model.links = 1 if defect == "still-linked" else 0
                        model.ctime += 1
                    with self.assertRaises(syscall_guard.Violation):
                        model.syscall(model.driver, 87, 0x2000, kernel=unlink)
                with self.assertRaises(syscall_guard.Violation):
                    model.tracker.emit(0)
                self.assertFalse(model.policy.accessed)

    def test_cleanup_attempts_all_owned_pins_once_without_retry_or_unlink(self):
        with _IntermediateModel() as model:
            model.created()
            file_fd, workspace_fd = model.tracker.file_fd, model.tracker.workspace_fd
            attempts = []
            original = model.close
            def failing(descriptor):
                attempts.append(descriptor)
                if descriptor == file_fd:
                    raise OSError("uncertain close")
                original(descriptor)
            with patch.object(syscall_guard.os, "close", failing), self.assertRaises(OSError):
                model.tracker.close()
            self.assertEqual(attempts, [file_fd, workspace_fd])
            model.tracker.close()
            self.assertEqual(attempts, [file_fd, workspace_fd])
            self.assertTrue(model.exists)
            self.assertEqual((model.tracker.file_fd, model.tracker.workspace_fd), (-1, -1))


class _CustodyModel:
    """Actual custody APIs with explicitly synthetic issued launch/native facts."""

    def __init__(self, path="/work/ccL3VdjV.s"):
        self.model = ToolchainProtocolDataTests().model(path)
        self.session = make_probe.ProbeSession.__new__(make_probe.ProbeSession)
        session = self.session
        session.budget = ProbeBudget()
        session.base, session.tree = Path("/inert/session"), Path("/inert/source")
        session.snapshot = SimpleNamespace(digest="c" * 64)
        session.serial = 0
        session.owner_thread = threading.get_ident()
        session._namespace_epoch = 1
        session._namespace_issued, session._namespace_tokens = {}, {}
        session._native_issue_owner, session._native_returns = None, {}
        session._native_context_commands = {}
        session._toolchain_receipt_archive = {}
        session._toolchain = toolchain_runtime.Controller(session)
        self.controller = session._toolchain
        self.tool = make_probe.RuntimeTool(self.model["driver"][0], self.model["driver"][0], 0o755, "d" * 64)
        self.command = make_probe.Command((self.tool.path, "--version"), code=("include/global.h",), runtime_tool=self.tool)
        session.source_owners = lambda paths: [
            (path, "100644", "c" * 64) for path in sorted(paths)
        ]
        self.context = make_probe._LiveDispatch(
            "session/make-root-1", 1, ("/bin/sh", "-c", "model checker"), "/repo",
            tuple(sorted(ENVIRONMENT.items())), False,
            ("recipe", toolchain_runtime.TARGET, 1), session.snapshot, session.tree, 1,
        )
        session._live_dispatches = [self.context]
        session._issued_dispatches = weakref.WeakSet((self.context,))
        session._command_dispatches = [(self.command, self.context)]
        recipe = toolchain_runtime.Recipe(
            "arm-none-eabi-gcc", (),
            (*self.model["driver"][1:6], "-std=gnu11", "-fsyntax-only", "-x", "c", "-"),
            tuple(self.model["driver"][1:]), "release", "aapcs", ("include",),
            ("/usr/include/newlib",),
        )
        self.grant = toolchain_runtime._RecipeGrant(
            weakref.ref(self.command), recipe, self.context, self.controller.bind(self.command),
            tuple(session.source_owners(self.command.code)),
        )
        self.grant.driver_identity = tuple(self.model["profile"]["driver_identity"])
        self.controller.commands[id(self.command)] = self.grant
        self.controller.issued.add(self.grant)
        self.controller.active = self.grant
        roots = toolchain_runtime.arm_headers.sdk_roots(self.model["profile"]["images"][1][0], True, ())
        self.sdk_row = ["/usr/include/newlib/stdint.h", 0o444, 1, "e" * 64]
        self.sdk = {
            "version": 1, "roots": [[root, root == "/usr/include/newlib"] for root in roots],
            "entries": [["/usr/include/newlib", "directory"], [self.sdk_row[0], "file"]],
            "files": [self.sdk_row], "excluded": [], "aliases": [],
        }
        self.grant.sdk = Path("/inert/sdk"), self.sdk
        self.grant.assembler = self.model["profile"]["images"][2][0]
        self.grant.assembler_spelling = "/usr/bin/arm-none-eabi-as"
        self.runtime_aliases = []
        self.stack = ExitStack()

    def __enter__(self):
        self.stack.enter_context(patch.object(
            self.controller, "driver_identity", return_value=self.grant.driver_identity,
        ))
        self.stack.enter_context(patch.object(toolchain_runtime, "verify_workspace", return_value=None))
        aliases = {
            row["argv"][0]: row["path"] for row in self.model["executions"]
        }
        self.stack.enter_context(patch.object(
            Path, "resolve", lambda path, **kwargs: Path(aliases[str(path)]),
        ))
        return self

    def __exit__(self, kind, value, traceback):
        self.controller.close()
        self.session._native_returns.clear()
        self.stack.close()

    def launch(self, stage=4, *, command=None):
        session, controller = self.session, self.controller
        if command is None:
            self.grant.stage = stage
        profile = copy.deepcopy(self.model["profile"])
        profile["stage"], profile["stdin"] = stage, toolchain_runtime.INPUTS[stage]
        profile["images"] = profile["images"][:1 if stage < 3 else 2 if stage == 3 else 3]
        profile["inputs"] = [["include/global.h", 0o644, 1, "c" * 64]] if stage == 3 else []
        argv = tuple(self.model["driver"]) if stage >= 3 else (self.tool.path, toolchain_runtime.QUERIES[stage])
        if stage == 3:
            argv = (
                self.tool.path, "-isystem", "/usr/include/newlib", "-mcpu=arm7tdmi",
                "-mthumb", "-mthumb-interwork", "-std=gnu11", "-fsyntax-only", "-x", "c", "-",
            )
        if command is None:
            command = make_probe.Command(
                argv, code=("include/global.h",) if stage == 3 else (), runtime_tool=self.tool,
            )
            step = toolchain_runtime._Step(command, self.grant, controller.bind(command), stage)
            controller.steps[id(command)] = step
            controller.issued.add(step)
        else:
            step = controller.steps[id(command)]
            argv = command.argv
        dependency = {
            "executables": [row[0] for row in profile["images"]],
            "runtime_aliases": self.runtime_aliases, "toolchain_probe": profile,
        }
        if stage >= 3:
            dependency["header_search"] = self.sdk
        step.dependency = toolchain_runtime.encoded(dependency)
        output = session.base / f"command-{session.serial + 1}" / "output"
        mounts = [
            session._mount(session.tree, "/repo"),
            session._mount(Path("/usr"), "/usr", executable=True),
        ]
        if stage >= 3:
            mounts.extend(
                session._mount(Path("/inert/sdk") / str(index), root)
                for index, (root, present) in enumerate(self.sdk["roots"]) if present
            )
        mounts.extend((
            session._mount(output, "/work", writable=True),
            session._mount(Path("/dev/null"), "/dev/null", writable=True),
        ))
        config = {
            "root": str(session.base / f"command-root-{session.serial + 1}"),
            "mode": "compile", "argv": list(argv), "environment": session._command_environment(self.command),
            "mounts": mounts, "code": list(command.code), "sources": [], "enumerations": [],
            "executables": dependency["executables"], "dependency": dependency,
        }
        token = controller.launch(command, config)
        session.serial += 1
        config.update({
            **{name: getattr(self.model["limits"], name) for name in (
                "file_limit", "observation_count", "observation_limit", "write_limit",
                "creation_limit", "process_limit", "memory_limit", "syscall_limit",
            )},
            "file_limit": 1024 * 1024, "observation_limit": 1024 * 1024,
            "deadline": session.budget.deadline,
            "report": str(session.base / f"report-{session.serial}.json"),
        })
        config["toolchain_runtime"] = controller.consume_launch(token, config)
        return command, step, token, config

    def native(self, stage=4, *, command=None, status=0, stdout=None, mutate_report=None):
        command, step, token, config = self.launch(stage, command=command)
        rows = copy.deepcopy(self.model["executions"][:len(config["executables"])])
        for row in rows:
            row["stage"] = toolchain_runtime.STAGES[stage]
            row["environment"] = dict(config["environment"])
        rows[0]["argv"] = list(command.argv)
        if stage == 3:
            rows[1]["argv"] = [rows[1]["path"], "-fsyntax-only", "-"]
        accessed = [toolchain_runtime.EXEC_PREFIX + toolchain_runtime.encoded(row).decode("ascii") for row in rows]
        if stage >= 3:
            accessed.append(toolchain_runtime.INPUT_PREFIX + toolchain_runtime.encoded({
                "stage": toolchain_runtime.STAGES[stage], "stdin": toolchain_runtime.INPUTS[stage], "eof": True,
            }).decode("ascii"))
            accessed.append(toolchain_runtime.arm_headers.PREFIX + toolchain_runtime.encoded(self.sdk_row).decode("ascii"))
        if stage == 4 and status == 0:
            receipt = copy.deepcopy(self.model["receipt"])
            receipt["scope"], receipt["binding"] = step.facts.scope, step.facts.binding
            for row, actor in zip(rows, receipt["actors"]):
                actor["exec_record_sha256"] = hashlib.sha256(toolchain_runtime.encoded(row)).hexdigest()
            accessed.append(toolchain_runtime.INTERMEDIATE_PREFIX + toolchain_runtime.encoded(receipt).decode("ascii"))
        outputs = (b"model GCC\n", b"arm-none-eabi\n", b"/usr/bin/arm-none-eabi-as\n", b"", b"")
        completed = subprocess.CompletedProcess(
            tuple(command.argv), status, outputs[stage] if stdout is None else stdout,
            b"inert compiler failure\n" if status else b"",
        )
        observed = {
            "ok": True, "returncode": status, "accessed": accessed, "metadata": (),
            "consumed": [], "code_consumed": list(command.code), "executed": list(config["executables"]),
        }
        if mutate_report is not None:
            mutate_report(observed)
        payload = self.controller.prepare_native(token, completed, observed, config)
        self.session._native_issue_owner = token
        self.session._issue_native_return(toolchain_runtime.NATIVE_PURPOSE, token, completed, observed, payload)
        return command, step, token, config, completed, observed

    def claim(self, native):
        command, step, token, config, completed, observed = native
        claimed = self.session._claim_native_return(
            toolchain_runtime.NATIVE_PURPOSE, token, completed, observed,
        )
        probes = tuple(toolchain_runtime._envelope_decode(self.session, claimed.payload.probes))
        result = make_probe.ProcessOutput(
            claimed.stdout, claimed.stderr, claimed.consumed, claimed.code_consumed,
            metadata=claimed.metadata,
            input_identities=tuple(self.session.source_owners(claimed.code_consumed)),
            executed=claimed.executed, runtime_receipt=tuple(config["dependency"]["runtime_aliases"]),
            runtime_sources=claimed.payload.runtime_sources,
            runtime_probes=probes, returncode=claimed.returncode,
        )
        return claimed, result

    def stage(self, number, *, status=0):
        native = self.native(number, status=status)
        claimed, result = self.claim(native)
        sealed = self.controller.seal_step_result(native[1], result, native_return=claimed)
        accepted = self.controller.consume_step_result(native[1], sealed)
        self.controller.steps.pop(id(native[0]))
        self.controller.issued.discard(native[1])
        self.grant.stage += 1
        return accepted

    def recipe(self, *, failed=False):
        stages = [self.stage(index, status=1 if failed and index == 4 else 0) for index in range(5)]
        stdout, stderr, status = self.controller._recipe_output(self.grant)
        result = make_probe.ProcessOutput(
            stdout, stderr, (),
            tuple(sorted({name for stage in stages for name in stage.result.code_consumed})),
            input_identities=tuple(sorted({row for stage in stages for row in stage.result.input_identities})),
            executed=tuple(name for stage in stages for name in stage.result.executed),
            runtime_sources=tuple(sorted({row for stage in stages for row in stage.result.runtime_sources})),
            runtime_probes=tuple(row for stage in stages for row in stage.result.runtime_probes),
            returncode=status,
        )
        sealed = self.controller.seal_recipe_result(self.grant, self.command, result)
        self.controller.active = None
        self.controller.commands.pop(id(self.command))
        self.controller.issued.discard(self.grant)
        return sealed

    def next_recipe(self, path, sequence):
        previous = self.grant
        self.model = ToolchainProtocolDataTests().model(path)
        self.command = replace(self.command)
        self.context = replace(self.context, sequence=sequence)
        self.session._live_dispatches[:] = [self.context]
        self.session._issued_dispatches.add(self.context)
        self.session._command_dispatches[:] = [(self.command, self.context)]
        self.grant = toolchain_runtime._RecipeGrant(
            weakref.ref(self.command), previous.recipe, self.context,
            self.controller.bind(self.command), previous.inputs,
        )
        self.grant.driver_identity = previous.driver_identity
        self.grant.sdk = previous.sdk
        self.grant.assembler, self.grant.assembler_spelling = previous.assembler, previous.assembler_spelling
        self.controller.commands[id(self.command)] = self.grant
        self.controller.issued.add(self.grant)
        self.controller.active = self.grant


class ToolchainCustodyInertTests(unittest.TestCase):
    def test_actual_alias_row_representation_is_preserved_and_remains_bound(self):
        with _CustodyModel() as model:
            model.runtime_aliases = [["/model/alias", "target", "/model/target"]]
            native = model.native()
            claimed, result = model.claim(native)
            sealed = model.controller.seal_step_result(native[1], result, native_return=claimed)
            self.assertIs(type(sealed.runtime_receipt[0]), list)
            self.assertEqual(sealed.runtime_receipt, tuple(model.runtime_aliases))
            sealed.runtime_receipt[0][1] = "changed"
            with self.assertRaises(MakeProbeError):
                model.controller.consume_step_result(native[1], sealed)

    def test_failed_compile_retains_original_status_and_raw_evidence_without_projection(self):
        with _CustodyModel() as model:
            result = model.recipe(failed=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"inert compiler failure\n" + toolchain_runtime.COMPILE_FAILURE.encode())
            evidence = model.controller.consume_recipe_result(model.command, result)
            self.assertIsNone(evidence.projection)
            stage = json.loads(result.toolchain_receipts[-1])
            self.assertIsNone(stage["intermediate"])
            self.assertEqual(stage["result"]["returncode"], 1)

    def test_execute_consumes_real_step_capabilities_before_issuing_a_recipe(self):
        with _CustodyModel() as model:
            model.controller.active = None
            model.grant.stage = 0
            def command(subcommand):
                stage = model.controller.steps[id(subcommand)].stage
                native = model.native(stage, command=subcommand)
                claimed, result = model.claim(native)
                return model.controller.seal_step_result(native[1], result, native_return=claimed)
            with patch.object(model.session, "_command", command), patch.object(
                model.session, "_verify_runtime_tool", return_value=None,
            ), patch.object(
                toolchain_runtime, "assembler_path",
                return_value=(model.grant.assembler_spelling, model.grant.assembler),
            ):
                result = model.controller.execute(model.command)
            self.assertEqual(len(result.toolchain_receipts), 5)
            self.assertFalse(model.controller.steps)
            self.assertFalse(model.controller._step_results)
            evidence = model.controller.consume_recipe_result(model.command, result)
            self.assertIsNotNone(evidence.projection)

    def test_execute_refuses_shaped_unissued_stage_results(self):
        with _CustodyModel() as model:
            model.controller.active = None
            model.grant.stage = 0
            outputs = (b"model GCC\n", b"arm-none-eabi\n", b"/usr/bin/arm-none-eabi-as\n", b"", b"")
            def unissued(subcommand):
                stage = model.controller.steps[id(subcommand)].stage
                return make_probe.ProcessOutput(outputs[stage], b"", (), ())
            with patch.object(model.session, "_command", unissued), patch.object(
                model.session, "_verify_runtime_tool", return_value=None,
            ), patch.object(
                toolchain_runtime, "assembler_path",
                return_value=(model.grant.assembler_spelling, model.grant.assembler),
            ), self.assertRaises(MakeProbeError):
                model.controller.execute(model.command)
            self.assertFalse(model.controller._recipe_results)

    def test_version_two_launch_and_consumed_identity_are_atomic(self):
        with _CustodyModel() as model:
            _, _, token, config = model.launch()
            self.assertEqual(toolchain_runtime.validate_launch(config)["version"], 2)
            changed = copy.deepcopy(config)
            changed["dependency"]["toolchain_probe"]["version"] = 1
            changed["toolchain_runtime"]["version"] = 1
            with self.assertRaises(toolchain_runtime.ChannelError):
                toolchain_runtime.validate_launch(changed)
            with self.assertRaises(MakeProbeError):
                model.controller.consume_launch(token, config)

    def test_original_native_facts_seal_one_immutable_stage_and_refuse_copy_replay(self):
        with _CustodyModel() as model:
            native = model.native()
            claimed, result = model.claim(native)
            self.assertFalse(model.session._native_returns)
            with self.assertRaises(MakeProbeError):
                model.controller.seal_step_result(copy.copy(native[1]), result, native_return=claimed)
            sealed = model.controller.seal_step_result(native[1], result, native_return=claimed)
            self.assertIs(sealed.stdout, claimed.stdout)
            self.assertIs(sealed.stderr, claimed.stderr)
            self.assertIs(type(sealed.toolchain_receipts[0]), bytes)
            with self.assertRaises(MakeProbeError):
                model.controller.consume_step_result(native[1], replace(sealed))
            accepted = model.controller.consume_step_result(native[1], sealed)
            self.assertIsNone(accepted.native.original)
            with self.assertRaises(MakeProbeError):
                model.controller.consume_step_result(native[1], sealed)

    def test_native_value_mutations_before_claim_and_between_claim_seal_refuse(self):
        for phase in ("before-claim", "before-seal"):
            for field in ("stdout", "stderr", "status", "report", "equal-stdout"):
                with self.subTest(phase=phase, field=field), _CustodyModel() as model:
                    native = model.native(0 if field == "equal-stdout" else 4)
                    completed, observed = native[-2:]
                    if phase == "before-seal":
                        claimed, result = model.claim(native)
                    if field == "stdout":
                        completed.stdout = b"changed"
                    elif field == "stderr":
                        completed.stderr = b"changed"
                    elif field == "status":
                        completed.returncode = 1
                    elif field == "report":
                        observed["accessed"].append("changed")
                    else:
                        replacement = bytes(bytearray(completed.stdout))
                        self.assertEqual(replacement, completed.stdout)
                        self.assertIsNot(replacement, completed.stdout)
                        completed.stdout = replacement
                    with self.assertRaises(MakeProbeError):
                        if phase == "before-claim":
                            model.claim(native)
                        else:
                            model.controller.seal_step_result(native[1], result, native_return=claimed)

    def test_recipe_capability_projects_only_two_roles_and_survives_removed_registration(self):
        with _CustodyModel() as model:
            result = model.recipe()
            raw = copy.deepcopy(result.runtime_probes)
            self.assertEqual(len(result.toolchain_receipts), 5)
            evidence = model.controller.consume_recipe_result(model.command, result)
            projection = toolchain_runtime._envelope_decode(model.session, evidence.projection)
            replacements = [
                (row["sequence"], index)
                for row in projection["runtime_probes"] if row.get("stage") == "compile" and "argv" in row
                for index, value in enumerate(row["argv"]) if type(value) is dict
            ]
            self.assertEqual(replacements, [(2, 20), (3, 7)])
            self.assertEqual(result.runtime_probes, raw)
            self.assertFalse(model.controller.commands)
            with self.assertRaises(MakeProbeError):
                model.controller.consume_recipe_result(model.command, result)

    def test_recipe_copies_mutations_and_view_expiry_cannot_acquire_projection(self):
        for defect in ("copy", "output", "environment", "epoch", "expired"):
            with self.subTest(defect=defect), _CustodyModel() as model:
                result = model.recipe()
                if defect == "copy":
                    result = replace(result)
                elif defect == "output":
                    object.__setattr__(result, "stdout", b"changed")
                elif defect == "environment":
                    result.runtime_probes[0]["environment"]["LANG"] = "changed"
                elif defect == "epoch":
                    model.session._namespace_epoch += 1
                else:
                    model.session._expire_namespaces()
                with self.assertRaises(MakeProbeError):
                    model.controller.consume_recipe_result(model.command, result)

    def test_acknowledged_occurrences_remain_distinct_and_bind_execution_not_semantics(self):
        values = []
        for path in ("/work/ccL3VdjV.s", "/work/ccBRFQFs.s"):
            with _CustodyModel(path) as model:
                result = model.recipe()
                evidence = model.controller.consume_recipe_result(model.command, result)
                projection = toolchain_runtime._envelope_decode(model.session, evidence.projection)
                record = {"command": projection, "output_sha256": hashlib.sha256(result.stdout).hexdigest()}
                pending = model.session._prepare_toolchain_occurrence(evidence, model.context, 0, record)
                model.session._acknowledge_toolchain_occurrence(model.context.scope, 0, pending, record)
                receipts = model.session.toolchain_receipts(model.context.scope)
                with self.assertRaises(MakeProbeError):
                    model.session._acknowledge_toolchain_occurrence(model.context.scope, 0, pending, record)
                self.assertEqual(model.session.toolchain_receipts(model.context.scope), receipts)
                semantic = {
                    "domains": {}, "assignments": [], "published_sources": [], "dynamic_commands": [record],
                }
                observation = make_probe.MakeObservation(
                    "target", semantic, "execution", "semantic", b"", b"", (),
                    toolchain_receipts=receipts,
                )
                original = model.session._namespace_observation_context(observation)
                changed = replace(observation, toolchain_receipts=(b"[]",))
                self.assertEqual(changed.semantics, observation.semantics)
                self.assertEqual(changed.semantic_digest, observation.semantic_digest)
                self.assertNotEqual(model.session._namespace_observation_context(changed), original)
                model.session._expire_namespaces()
                self.assertEqual(model.session.toolchain_receipts(model.context.scope), receipts)
                values.append((record, receipts))
        self.assertEqual(values[0][0], values[1][0])
        self.assertNotEqual(values[0][1], values[1][1])

    def test_actual_acknowledgement_branch_retains_two_occurrences_and_ignores_repeats(self):
        with _CustodyModel() as model:
            receipts, confirmations = {}, []
            for slot, path in enumerate(("/work/ccL3VdjV.s", "/work/ccBRFQFs.s")):
                if slot:
                    model.next_recipe(path, slot + 1)
                result = model.recipe()
                evidence = model.controller.consume_recipe_result(model.command, result)
                record = {
                    "command": toolchain_runtime._envelope_decode(model.session, evidence.projection),
                    "output_sha256": hashlib.sha256(result.stdout).hexdigest(),
                }
                pending = model.session._prepare_toolchain_occurrence(evidence, model.context, slot, record)
                owner = str(slot + 1) * 64
                receipts[slot] = ("model checker", record, result, owner, "replace", {}, pending)
                confirmations.append({"slot": slot, "owner": owner, "policy": "replace", "outputs": []})
            module = ast.parse(Path(make_probe.__file__).read_text())
            session_class, = [
                node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "ProbeSession"
            ]
            actual_ack, = [
                node for node in ast.walk(session_class)
                if isinstance(node, ast.FunctionDef) and node.name == "acknowledge"
            ]
            wrapper = ast.parse(
                "def replay(self, receipts, confirmations, dispatch_scope):\n"
                "    confirmed = 0\n"
                "    last_confirmation = None\n"
                "    command_results = {}\n"
                "    header_steps = {}\n"
                "    receipt_directories = {0: (), 1: ()}\n"
                "    generated_paths, generated_directories = set(), set()\n"
                "    file_owner = None\n"
            )
            wrapper.body[0].body.append(actual_ack)
            wrapper.body[0].body.extend(ast.parse(
                "for number, confirmation in enumerate(confirmations, 1):\n"
                "    acknowledge(number, confirmation)\n"
                "    acknowledge(number, confirmation)\n"
                "return confirmed, command_results\n"
            ).body)
            ast.fix_missing_locations(wrapper)
            namespace = dict(make_probe.__dict__)
            exec(compile(wrapper, "<actual-inert-acknowledgement>", "exec"), namespace)
            original = model.session._acknowledge_toolchain_occurrence
            with patch.object(model.session, "_acknowledge_toolchain_occurrence", wraps=original) as append:
                count, records = namespace["replay"](
                    model.session, receipts, confirmations, model.context.scope,
                )
                self.assertEqual(append.call_count, 2)
            self.assertEqual(count, 2)
            self.assertEqual(len(records), 1)
            archived = model.session.toolchain_receipts(model.context.scope)
            self.assertEqual(len(archived), 2)
            self.assertEqual([json.loads(value)["producer_slot"] for value in archived], [0, 1])
            self.assertNotEqual(archived[0], archived[1])

    def test_admission_failure_never_publishes_native_or_step_capability(self):
        with _CustodyModel() as model:
            native = model.native()
            claimed, result = model.claim(native)
            before = dict(model.session.budget.bytes)
            with patch.object(model.session.budget, "charge", side_effect=MakeProbeError("admission refused")):
                with self.assertRaises(MakeProbeError):
                    model.controller.seal_step_result(native[1], result, native_return=claimed)
            self.assertFalse(model.controller._step_results)
            self.assertFalse(model.session._native_returns)
            self.assertEqual(model.session.budget.bytes, before)
            model.controller.retire_step(native[1])
            with self.assertRaises(MakeProbeError):
                model.controller.seal_step_result(native[1], result, native_return=claimed)


class ToolchainCorrectionInertTests(unittest.TestCase):
    def test_supervisor_root_fd_spelling_completes_without_rewriting_guest_receipt(self):
        with _IntermediateModel() as model:
            try:
                value = model.finish()
            except syscall_guard.Violation as error:
                self.fail("valid supervisor-root descriptor was rejected: " + str(error))
            proof = json.loads(value[len(toolchain_runtime.INTERMEDIATE_PREFIX):])
            self.assertEqual(proof["path"], "/work/ccL3VdjV.s")
            self.assertTrue(proof["complete"])
            self.assertEqual(model.peak, 3)
            self.assertFalse(model.handles)

    def test_foreign_escaped_alias_and_replaced_descriptor_paths_refuse(self):
        paths = (
            "/work/ccL3VdjV.s",
            "/foreign/work/ccL3VdjV.s",
            "/inert/command-root-10/work/ccL3VdjV.s",
            "/inert/command-root-1/work/../work/ccL3VdjV.s",
            "/inert/command-root-1/work/other.s",
            "/inert/command-root-1/work/ccL3VdjV.s (deleted)",
        )
        for path in paths:
            with self.subTest(path=path), _IntermediateModel() as model:
                driver = model.actor(1)
                with patch.object(syscall_guard.os, "readlink", return_value=path):
                    with self.assertRaisesRegex(syscall_guard.Violation, "descriptor no longer names"):
                        model.open_actor(driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        with _IntermediateModel() as model:
            driver = model.actor(1)
            original = model.stat
            def replaced(path, **options):
                info = original(path, **options)
                if str(path).startswith("/proc/"):
                    info.st_ino += 1
                return info
            with patch.object(syscall_guard.os, "stat", replaced):
                with self.assertRaisesRegex(syscall_guard.Violation, "descriptor no longer names"):
                    model.open_actor(driver, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)

    def test_launch_mutant_removes_only_membership_and_keeps_current_valid_protocol(self):
        mutant = _function_mutant(toolchain_runtime.Controller.consume_launch, _remove_launch_membership)
        for removed in (False, True, False):
            with self.subTest(removed=removed), _CustodyModel() as model:
                original = model.controller.consume_launch
                def unregistered(token, config):
                    model.controller.issued.discard(token)
                    return mutant(model.controller, token, config) if removed else original(token, config)
                with patch.object(model.controller, "consume_launch", unregistered):
                    if removed:
                        _, step, token, config = model.launch(0)
                        self.assertEqual(config["toolchain_runtime"]["version"], 2)
                        self.assertEqual(toolchain_runtime.validate_launch(config)["stage"], 0)
                        self.assertIs(step.facts.token, token)
                        self.assertEqual(step.phase, "launched")
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "missing, copied, forged, expired"):
                            model.launch(0)
                self.assertEqual(model.session.budget.runs, 0)
        for replace_token in (lambda token: None, copy.copy, lambda token: object()):
            with _CustodyModel() as model:
                def foreign(token, config):
                    return mutant(model.controller, replace_token(token), config)
                with patch.object(model.controller, "consume_launch", foreign), self.assertRaises(MakeProbeError):
                    model.launch(0)

    def test_record_mutant_preserves_rows_and_exposes_only_query_identity_mismatch(self):
        mutant = _function_mutant(toolchain_runtime.records, _remove_record_identity)
        def changed(report):
            index = next(i for i, value in enumerate(report["accessed"]) if value.startswith(toolchain_runtime.EXEC_PREFIX))
            row = json.loads(report["accessed"][index][len(toolchain_runtime.EXEC_PREFIX):])
            row["identity"][1] += 1
            report["accessed"][index] = toolchain_runtime.EXEC_PREFIX + toolchain_runtime.encoded(row).decode("ascii")
        for removed in (False, True, False):
            with self.subTest(removed=removed), _CustodyModel() as model:
                selected = mutant if removed else toolchain_runtime.records
                with patch.object(toolchain_runtime, "records", selected):
                    if removed:
                        native = model.native(0, mutate_report=changed)
                        claimed, result = model.claim(native)
                        self.assertEqual(len(result.runtime_probes), 1)
                        self.assertEqual(result.runtime_probes[0]["identity"][1], 12)
                        sealed = model.controller.seal_step_result(native[1], result, native_return=claimed)
                        model.controller.consume_step_result(native[1], sealed)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unbound actual toolchain executable"):
                            model.native(0, mutate_report=changed)
        with _CustodyModel() as model, patch.object(toolchain_runtime, "records", mutant):
            with self.assertRaisesRegex(MakeProbeError, "actor differs from its authenticated execution row"):
                model.native(4, mutate_report=changed)

    def target_case(self, *, post_seal=False, remove_target=False):
        mutant = _function_mutant(
            toolchain_runtime.Controller.execute,
            lambda tree: _remove_mutation_condition(tree, "execute", constant=b"arm-none-eabi"),
        ) if remove_target else None
        with _CustodyModel() as model:
            model.controller.active = None
            model.grant.stage = 0
            stages, originals, aggregates = [], [], []
            def command(subcommand):
                stage = model.controller.steps[id(subcommand)].stage
                stages.append(stage)
                if stage > 1:
                    raise _UnexpectedTargetStage("bound foreign target attempted a later stage")
                native = model.native(
                    stage, command=subcommand,
                    stdout=b"foreign-target\n" if stage == 1 and not post_seal else None,
                )
                claimed, result = model.claim(native)
                sealed = model.controller.seal_step_result(native[1], result, native_return=claimed)
                if stage == 1:
                    originals.append(sealed.stdout)
                    if post_seal:
                        return replace(sealed, stdout=b"foreign-target\n")
                return sealed
            def execute():
                result = (
                    mutant(model.controller, model.command) if mutant is not None
                    else model.controller.execute(model.command)
                )
                aggregates.append(result)
                return result
            with patch.object(model.session, "_command", command), patch.object(
                model.session, "_verify_runtime_tool", return_value=None,
            ):
                if post_seal:
                    with self.assertRaisesRegex(MakeProbeError, "stage is unissued, copied, stale or replayed"):
                        execute()
                    self.assertEqual(originals, [b"arm-none-eabi\n"])
                    self.assertEqual(aggregates, [])
                elif remove_target:
                    with self.assertRaisesRegex(_UnexpectedTargetStage, "attempted a later stage"):
                        execute()
                    self.assertEqual(stages, [0, 1, 2])
                    self.assertEqual(aggregates, [])
                else:
                    result = execute()
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, b"error: modern compiler targets 'foreign-target'; expected 'arm-none-eabi'\n")
                    self.assertEqual(stages, [0, 1])
                    self.assertIsNone(model.controller.consume_recipe_result(model.command, result).projection)

    def test_post_seal_target_substitution_is_custody_rejection_not_an_aggregate(self):
        self.target_case(post_seal=True)

    def test_original_bound_target_stops_before_later_dispatch_and_removal_exposes_it(self):
        self.target_case()
        self.target_case(remove_target=True)
        self.target_case()

    def test_stdin_transform_selects_policy_leave_and_exposes_actual_byte_guard(self):
        tree = ast.parse(Path(syscall_guard.__file__).read_text())
        before = ast.dump(_mutation_function(tree, "_ToolchainIntermediate.leave"))
        _remove_policy_stdin_guard(tree)
        self.assertEqual(ast.dump(_mutation_function(tree, "_ToolchainIntermediate.leave")), before)
        compile(tree, "<prepared-stdin-guard>", "exec")
        node = _mutation_function(tree, "Policy.leave")
        namespace = dict(syscall_guard.__dict__)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<inert-policy-leave>", "exec"), namespace)
        altered = FunctionType(namespace["leave"].__code__, syscall_guard.__dict__)
        changed = toolchain_runtime.SYNTAX_INPUT.replace("#include ", "#include\t").encode()
        for removed in (False, True, False):
            policy = syscall_guard.Policy.__new__(syscall_guard.Policy)
            policy.config = {"observation_limit": 65536}
            policy.observation_bytes = 0
            policy.toolchain = {"stage": 3, "stdin": toolchain_runtime.SYNTAX_INPUT}
            policy.toolchain_stdin, policy.toolchain_eof = bytearray(), False
            state = syscall_guard.Process("compiler")
            state.pending = ("toolchain-stdin", (0x1000, len(changed)))
            registers = syscall_guard.Registers()
            registers.orig_rax, registers.rax = 0, len(changed)
            with patch.object(syscall_guard, "memory", return_value=changed):
                if removed:
                    altered(policy, 401, state, registers)
                    self.assertEqual(bytes(policy.toolchain_stdin), changed)
                else:
                    with self.assertRaisesRegex(syscall_guard.Violation, "stdin differs from its actual issued bytes"):
                        policy.leave(401, state, registers)
                    self.assertFalse(policy.toolchain_stdin)

    def test_all_existing_enforcement_transforms_preflight_and_preparation_cannot_count_as_regression(self):
        prepared = _prepare_enforcement_mutations()
        self.assertEqual(set(prepared), {
            "grammar", "launch", "target", "records", "workspace", "sdk", "image", "stdin", "ignored-status",
        })
        for name in ("sdk", "image", "stdin"):
            for path, source in prepared[name].items():
                compile(source, path, "exec")
        for path, source in prepared["workspace"][0].items():
            compile(source, path, "exec")
        def broken(tree):
            raise AssertionError("inert setup failure")
        with self.assertRaises(_MutationPreparationError):
            _prepare_native_runtime({"syscall_guard.py": broken})
        with self.assertRaises(_MutationPreparationError):
            _function_mutant(toolchain_runtime.records, broken)
        tree = ast.parse(Path(syscall_guard.__file__).read_text())
        with self.assertRaises(_MutationPreparationError):
            _mutation_function(tree, "leave")

    def test_all_prepared_stop_fault_and_removal_transforms_compile_before_regressions(self):
        tree = ast.parse(Path(__file__).read_text())
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ModernToolchainTests")
        function = copy.deepcopy(_mutation_function(owner, "intermediate_fault"))
        function.decorator_list = []
        namespace = {"ast": ast}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<pure-stop-fault-preflight>", "exec"), namespace)
        prepared = []
        for kind in ("creation-status", "actor", "object", "reader", "writer-barrier", "retirement"):
            for remove in (False, True):
                def change(source):
                    namespace["intermediate_fault"](source, kind, remove=remove)
                sources = _prepare_native_runtime({"syscall_guard.py": change})
                compile(sources["syscall_guard.py"], "<prepared-stop-fault>", "exec")
                prepared.append((kind, remove))
        self.assertEqual(len(prepared), 12)


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
        self.assertFalse(session._native_returns)
        self.assertFalse(session._toolchain._step_results)
        self.assertFalse(session._toolchain._recipe_results)
        self.assertFalse(session._toolchain_receipt_archive)

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

    def test_one_make_two_checker_typed_intermediate_component(self):
        with self.session() as session:
            observed, _ = self.capture(session)
            self.assertEqual(len(self.results), 2)
            self.assertEqual(len(observed.toolchain_receipts), 2)
            raw = [json.loads(value) for value in observed.toolchain_receipts]
            self.assertTrue(all(
                type(value) is bytes for value in observed.toolchain_receipts
            ))
            scope, = {record["make_scope"] for record in raw}
            self.assertEqual(session.toolchain_receipts(scope), observed.toolchain_receipts)
            dispatches = [
                row for row in observed.semantics["native_dispatches"]
                if row["job"]["target"] == toolchain_runtime.TARGET
            ]
            self.assertEqual(
                [record["native_dispatch_sequence"] for record in raw],
                [row["sequence"] for row in dispatches],
            )
            self.assertLess(raw[0]["producer_slot"], raw[1]["producer_slot"])
            bindings, contents = [], []
            for record, result in zip(raw, self.results):
                self.assertEqual(record["version"], 1)
                self.assertEqual(record["kind"], "toolchain-recipe")
                stages = record["stages"]
                self.assertEqual([stage["stage"] for stage in stages], list(toolchain_runtime.STAGES))
                self.assertEqual(
                    tuple(toolchain_runtime.encoded(stage) for stage in stages),
                    result.toolchain_receipts,
                )
                stage = stages[-1]
                proof = stage["intermediate"]
                profile = {
                    "version": 2, "stage": 4, "stdin": toolchain_runtime.COMPILE_INPUT,
                    "inputs": [], "driver_identity": stage["images"][0][1:],
                    "images": stage["images"], "workspace": stage["workspace"],
                }
                rows = stage["executions"]
                roles = toolchain_runtime.compile_operand_roles(
                    rows, profile, rows[0]["argv"], complete=True,
                )
                self.assertEqual(proof["path"], roles.output.value)
                self.assertEqual(proof["path"], roles.input.value)
                toolchain_runtime.intermediate_record(
                    [toolchain_runtime.INTERMEDIATE_PREFIX + toolchain_runtime.encoded(proof).decode("ascii")],
                    profile=profile,
                    launch={"version": 2, "scope": stage["launch_scope"], "binding": stage["launch_binding"]},
                    executions=rows, returncode=0,
                    limits=toolchain_runtime.IntermediateLimits(**stage["admission"]),
                    reserve=lambda size: session.budget.charge("control", size),
                )
                bindings.append((stage["launch_scope"], stage["launch_binding"], tuple(stage["workspace"])))
                contents.append((proof["writer"]["completed"]["extent"], proof["writer"]["completed"]["sha256"]))
                self.assertTrue(proof["complete"])
                self.assertTrue(proof["retirement"]["path_absent"])
                self.assertEqual(proof["retirement"]["after_identity"][6], 0)
            self.assertNotEqual(bindings[0], bindings[1])
            self.assertNotEqual(observed.toolchain_receipts[0], observed.toolchain_receipts[1])
            self.assertEqual(contents[0], contents[1], "actual assembly differs; do not normalize its digest")
            semantic = [
                row for row in observed.semantics["dynamic_commands"] if row["command"].get("toolchain_check")
            ]
            self.assertEqual(len(semantic), 1)
            self.assertEqual(
                {record["semantic_record_sha256"] for record in raw},
                {hashlib.sha256(toolchain_runtime.encoded(semantic[0])).hexdigest()},
            )
            refs = [
                value
                for row in semantic[0]["command"]["runtime_probes"] if "argv" in row
                for value in row["argv"] if type(value) is dict
            ]
            self.assertEqual(refs, [{
                "kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly",
            }] * 2)
        self.assert_clean(session)

    @staticmethod
    def intermediate_fault(tree, kind, *, remove=False):
        tracker, = [
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_ToolchainIntermediate"
        ]
        method = {
            "actor": "actor", "object": "object_identity", "reader": "leave", "retirement": "leave",
            "creation-status": "leave", "writer-barrier": "exited",
        }[kind]
        owner, = [node for node in tracker.body if isinstance(node, ast.FunctionDef) and node.name == method]
        messages = {
            "actor": "toolchain operation has a foreign process/exec/birth owner",
            "object": "toolchain object changed identity, mode, link or extent",
            "reader": "toolchain reader did not consume exactly the sealed content",
            "retirement": "toolchain unlink did not retire the exact pinned object",
            "creation-status": "toolchain transition is outside its closed completion order",
            "writer-barrier": "toolchain transition is outside its closed completion order",
        }
        if remove:
            guarded_owner = owner
            if kind == "writer-barrier":
                guarded_owner, = [
                    node for node in tracker.body if isinstance(node, ast.FunctionDef) and node.name == "phase_is"
                ]
            guards = [
                node for node in ast.walk(guarded_owner) if isinstance(node, ast.If)
                and any(
                    isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call)
                    and statement.exc.args and isinstance(statement.exc.args[0], ast.Constant)
                    and statement.exc.args[0].value == messages[kind]
                    for statement in node.body
                )
            ]
            if kind == "creation-status":
                guards = [
                    node for node in owner.body if isinstance(node, ast.If)
                    and ast.dump(node.test) == ast.dump(ast.parse("result < 0", mode="eval").body)
                ]
            guard, = guards
            guard.test = ast.Constant(False)
        if kind == "actor":
            owner.body[1:1] = ast.parse(
                "if self.path is not None and type(sequence) is int and 1 <= sequence <= len(self.actors):\n"
                "    self.actors[sequence - 1] = (pid, state.clone(), state.pidfd, state.toolchain_birth_sequence)\n"
            ).body
        elif kind == "object":
            index, = [
                index for index, node in enumerate(owner.body)
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "identity"
            ]
            owner.body[index + 1:index + 1] = ast.parse(
                "if not absent:\n    identity = (*identity[:6], 2)\n"
            ).body
        elif kind == "reader":
            digest, = [
                node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_toolchain_memory_digest"
            ]
            digest.body[-1:-1] = ast.parse('digest.update(b"fault")').body
        elif kind == "retirement":
            assignments = [
                (node, index) for node in ast.walk(owner) if isinstance(node, ast.If)
                for index, item in enumerate(node.body)
                if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id == "after"
            ]
            parent, index = assignments[0]
            parent.body[index + 1:index + 1] = ast.parse("after = (*after[:6], 1)").body
        elif kind == "creation-status":
            index, = [
                index for index, node in enumerate(owner.body) if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "result"
            ]
            owner.body[index + 1:index + 1] = ast.parse(
                'if pending[0] == "open" and pending[1] == 1:\n    result = -1\n'
            ).body
        else:
            index, = [
                index for index, node in enumerate(owner.body) if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "sequence"
            ]
            owner.body[index + 1:index + 1] = ast.parse(
                'if sequence == 2:\n    self.phase = "writer-open"\n'
            ).body
        ast.fix_missing_locations(tree)
        return messages[kind]

    def prepare_native_intermediate_fault(self, kind, *, remove=False):
        message = {}
        def mutate(tree):
            message["expected"] = self.intermediate_fault(tree, kind, remove=remove)
        prepared = _prepare_native_runtime({"syscall_guard.py": mutate})
        return prepared, message["expected"]

    def assert_native_intermediate_result(self, message):
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, re.escape(message)):
                self.make(session)
        self.assert_clean(session)

    def assert_native_intermediate_fault(self, kind):
        prepared, message = self.prepare_native_intermediate_fault(kind)
        with self.native_runtime(prepared):
            self.assert_native_intermediate_result(message)

    def test_native_intermediate_actor_fault_reaches_its_owned_stop_guard(self):
        self.assert_native_intermediate_fault("actor")

    def test_native_intermediate_creation_status_fault_cannot_complete(self):
        self.assert_native_intermediate_fault("creation-status")

    def test_native_intermediate_writer_terminal_barrier_cannot_be_skipped(self):
        self.assert_native_intermediate_fault("writer-barrier")

    def test_native_intermediate_object_fault_reaches_its_pin_guard(self):
        self.assert_native_intermediate_fault("object")

    def test_native_intermediate_returned_content_fault_reaches_its_reader_guard(self):
        self.assert_native_intermediate_fault("reader")

    def test_native_intermediate_unlink_fault_reaches_its_retirement_guard(self):
        self.assert_native_intermediate_fault("retirement")

    def test_native_intermediate_guard_removal_and_restoration(self):
        for kind in ("creation-status", "actor", "object", "reader", "writer-barrier", "retirement"):
            prepared, message = self.prepare_native_intermediate_fault(kind, remove=True)
            with self.native_runtime(prepared), self.subTest(kind=kind), self.assertRaises(AssertionError):
                self.assert_native_intermediate_result(message)
            self.assert_native_intermediate_fault(kind)

    def test_native_copied_step_result_is_not_completion_authority(self):
        with self.session() as session:
            original = session._command
            def copied(command, **options):
                result = original(command, **options)
                return replace(result) if id(command) in session._toolchain.steps else result
            with patch.object(session, "_command", copied), self.assertRaisesRegex(
                MakeProbeError, "toolchain stage is unissued, copied, stale or replayed",
            ):
                self.make(session)
        self.assert_clean(session)

    def test_native_changed_non_role_result_cannot_reach_semantic_projection(self):
        with self.session() as session:
            original = session._toolchain.consume_recipe_result
            def changed(command, result):
                result.runtime_probes[0]["environment"]["LANG"] = "changed-after-sealing"
                return original(command, result)
            with patch.object(session._toolchain, "consume_recipe_result", changed), self.assertRaisesRegex(
                MakeProbeError, "toolchain aggregate changed after sealing",
            ):
                self.make(session)
        self.assert_clean(session)

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
            "missing", "unregistered", "copied", "forged", "replay", "closed", "epoch", "argv", "environment",
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
                    elif defect == "unregistered":
                        session._toolchain.issued.discard(options["toolchain_launch"])
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
                    try:
                        with self.assertRaises(MakeProbeError):
                            run(root, **options)
                    finally:
                        if defect == "unregistered":
                            self.launch_mutation_runs = session.budget.runs - before
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
    def native_runtime(self, prepared):
        from scripts.validation_ownership import make_probe

        if (
            type(prepared) is not dict
            or any(type(name) is not str or type(data) is not bytes for name, data in prepared.items())
        ):
            raise _MutationPreparationError("native runtime requires preflighted source bytes")
        root = self.fixture.directory / ("native-runtime-" + str(len(list(self.fixture.directory.iterdir()))))
        root.mkdir()
        for path in make_probe.TRUSTED_ROOT.iterdir():
            if path.is_file() and path.suffix in {".py", ".c", ".h"}:
                data = path.read_bytes()
                if path.name in prepared:
                    data = prepared[path.name]
                (root / path.name).write_bytes(data)
        with patch.object(make_probe, "TRUSTED_ROOT", root):
            yield

    replace_function = staticmethod(_replace_mutation_function)
    remove_condition = staticmethod(_remove_mutation_condition)
    function_mutant = staticmethod(_function_mutant)

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

    def assert_native_stdin_control(self):
        with self.session() as session:
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
        prepared = _prepare_native_runtime({"toolchain_runtime.py": _changed_stdin_input})
        with self.native_runtime(prepared):
            self.assert_native_stdin_control()

    def test_independent_enforcement_mutations_fail_the_corresponding_regressions(self):
        prepared = _prepare_enforcement_mutations()
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
            with patch.object(toolchain_runtime, "parse_recipe", prepared["grammar"]):
                yield
        run_control("complete-original-grammar",
                    lambda case: case.test_complete_original_recipe_and_effective_flags_fail_closed_before_toolchain_launch(),
                    recipe_patch)

        @contextmanager
        def capability_patch(case):
            case.launch_controls = ("unregistered",)
            case.launch_mutation_runs = 0
            with patch.object(toolchain_runtime.Controller, "consume_launch", prepared["launch"]):
                yield
            self.assertGreater(case.launch_mutation_runs, 0, "mutation never crossed the valid v2 launch boundary")
        run_control("actual-one-shot-launch-authority",
                    lambda case: case.test_only_issued_actual_launches_accept_original_arguments_inputs_and_lifetime(),
                    capability_patch)

        @contextmanager
        def target_patch(case):
            case.target_mutation_witness = None
            with patch.object(toolchain_runtime.Controller, "execute", prepared["target"]):
                yield
            self.assertEqual(case.target_mutation_witness, (0, 1, 2))
        run_control("actual-target-result", lambda case: case.test_original_bound_foreign_target_stops_before_later_stages(),
                    target_patch)

        @contextmanager
        def receipt_patch(case):
            with patch.object(toolchain_runtime, "records", prepared["records"]):
                yield
        run_control("authenticated-executable-input-results", lambda case: case.assert_receipt_control("identity"), receipt_patch)

        @contextmanager
        def workspace_patch(case):
            case.launch_controls = ("directory-object",)
            source, parent = prepared["workspace"]
            with case.native_runtime(source), patch.object(toolchain_runtime, "verify_workspace", parent):
                yield
        run_control("actual-owned-workspace", lambda case: case.test_only_issued_actual_launches_accept_original_arguments_inputs_and_lifetime(),
                    workspace_patch)

        @contextmanager
        def sdk_patch(case):
            case.input_controls = ("sdk",)
            with case.native_runtime(prepared["sdk"]):
                yield
        run_control("actual-immutable-C-SDK", lambda case: case.test_native_compiler_rejects_changed_sdk_and_source_content_before_consumption(),
                    sdk_patch)

        @contextmanager
        def image_patch(case):
            with case.native_runtime(prepared["image"]):
                yield
        run_control("actual-native-executable-image", lambda case: case.assert_native_image_control(), image_patch)

        @contextmanager
        def stdin_patch(case):
            with case.native_runtime(prepared["stdin"]):
                yield
        run_control("stdin-before-frontend-effects", lambda case: case.assert_native_stdin_control(), stdin_patch)

        @contextmanager
        def ignored_status_patch(case):
            with patch.object(make_probe.ProbeSession, "_make", prepared["ignored-status"]):
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
                with self.assertRaisesRegex(MakeProbeError, "toolchain stage is unissued, copied, stale or replayed"):
                    self.make(session)
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0].stdout, b"arm-none-eabi\n")
            self.assertEqual(results, [])
        self.assert_clean(session)

    def test_original_bound_foreign_target_stops_before_later_stages(self):
        with self.session() as session:
            command, execute, issue = session._command, session._toolchain.execute, session._issue_native_return
            results, actual, stages = [], [], []
            def original_target(purpose, owner, completed, observed, payload):
                if purpose == toolchain_runtime.NATIVE_PURPOSE and payload.step.stage == 1:
                    actual.append(completed.stdout)
                    completed.stdout = b"foreign-target\n"
                return issue(purpose, owner, completed, observed, payload)
            def track(value, **options):
                step = session._toolchain.steps.get(id(value))
                if step is not None:
                    stages.append(step.stage)
                    if step.stage > 1:
                        self.target_mutation_witness = tuple(stages)
                        raise _UnexpectedTargetStage("target-value enforcement attempted a later stage")
                return command(value, **options)
            def record(value):
                result = execute(value)
                results.append(result)
                return result
            with (
                patch.object(session, "_issue_native_return", original_target),
                patch.object(session, "_command", track),
                patch.object(session._toolchain, "execute", record),
                self.assertRaisesRegex(MakeProbeError, "GNU Make failed after live producers"),
            ):
                self.make(session)
            self.assertEqual(actual, [b"arm-none-eabi\n"])
            self.assertEqual(stages, [0, 1])
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
                if defect == "identity" and row["stage"] != "version":
                    return data
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
