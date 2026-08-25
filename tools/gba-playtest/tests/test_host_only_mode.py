"""Regression teeth for the GBA_PLAYTEST_HOST_ONLY host-only contract.

Issue #10 / #13 integrated-harness defect: the host lane used to decide *live
emulator run vs. skip* from whether a git-ignored build artifact happened to
exist in the working tree, so `python3 -m unittest discover -s
tools/gba-playtest/tests` passed on a clean checkout and failed on a worktree
holding a stale (or concurrently rebuilding) ROM -- six release save-compat
fingerprint failures that say nothing about the commit under test. The fix is
tests/host_mode.py: an explicit mode, never artifact timing.

What is proven here:

* strict, explicit env parsing (default off, unknown value refused);
* every Category B (live) TestCase is registered AND guarded, and no
  unregistered live entry point exists in the live modules;
* in host-only mode a live class skips BEFORE any existence probe -- even
  when the artifact materializes exactly when it would be probed, and even
  when it appears between two runs (concurrent build);
* normal mode is unchanged: the same class still probes and still captures;
* a hermetic staged worktree (TemporaryDirectory) holding stale, mismatched
  debug/release/legacy ROM-shaped, ELF and save artifacts at the exact paths
  the suite looks at: host-only skips all nine live classes, exits 0, and
  leaves every staged file byte-, size- and mtime-identical (nothing deleted,
  nothing rewritten), while the same tree in normal mode really opens the
  stale ROM and fails loudly.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PLAYTEST_DIR = TESTS_DIR.parent
for _extra in (str(PLAYTEST_DIR), str(TESTS_DIR)):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

import gba_playtest  # noqa: E402
import host_mode  # noqa: E402

REPO_ROOT = host_mode.REPO_ROOT

# Stale artifacts staged into the hermetic worktree copy: exactly the paths
# this suite (and modern.mk) look at, including the git-ignored ones a local
# `verify --jobs 2` build gate rewrites underneath a running host gate.
_STAGED_ARTIFACT_RELATIVE_PATHS = (
    "fireemblem8.gba",
    "build/expansion-modern/debug/aapcs/fireemblem8.gba",
    "build/expansion-modern/release/aapcs/fireemblem8.gba",
    "build/expansion-modern/debug/aapcs/fireemblem8.elf",
    "build/expansion-modern/release/aapcs/fireemblem8.elf",
    "build/expansion-modern/debug/aapcs/debugtools-fixtures/debugtools-current.sav",
)

_HOST_MODE_ARTIFACT_APIS = frozenset({"capture_live_or_skip", "modern_elf", "modern_rom", "require_built_rom"})
_HOST_MODE_ARTIFACT_ATTRIBUTES = frozenset({"LIVE_ARTIFACTS", "LIVE_ROMS"})
_ARTIFACT_ACCESSORS = frozenset({"exists", "is_file", "open", "read_bytes", "read_text", "stat"})
_ARTIFACT_SUFFIXES = (".elf", ".gba")
def _literal_string_tail(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_string_tail(node.right)
    if isinstance(node, ast.JoinedStr):
        for value in reversed(node.values):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None
def _has_artifact_suffix(node):
    value = _literal_string_tail(node)
    return value is not None and value.lower().endswith(_ARTIFACT_SUFFIXES)
class _ArtifactDiscovery(ast.NodeVisitor):
    """Classify repository artifacts without relying on source spelling."""

    def __init__(self):
        self.class_stack, self.function_stack = [], []
        self.function_depth, self.capture_mock_depth = 0, 0
        self.path_origins, self.generated_paths = {}, set()
        self.repository_owners, self.direct_capture_owners = {}, set()
        self.module_io = set()
        self.module_helpers, self.helper_ios, self.helper_callers = set(), {}, {}
        self.direct_capture_records, self.method_calls = [], []
        self.function_parameters = {}
        self.host_mode_modules, self.gba_playtest_modules = set(), set()
        self.homebrew_modules, self.temporary_modules, self.temporary_symbols = set(), set(), set()
        self.host_mode_symbols, self.gba_playtest_symbols = {}, {}
        self.homebrew_builders, self.temporary_factories = set(), set()
    def _owner(self):
        return self.class_stack[-1] if self.class_stack else "<module>"
    def _mark_repository(self, reason):
        self.repository_owners.setdefault(self._owner(), set()).add(reason)
    def _mark_io(self, reason):
        if self.function_depth and not self.class_stack:
            self.helper_ios.setdefault(self.function_stack[-1], set()).add(reason)
        else:
            self._mark_repository(reason)
        if self._owner() == "<module>" and self.function_depth == 0:
            self.module_io.add(reason)
    def _origin_key(self, node):
        if isinstance(node, ast.Name):
            scope = self.function_stack[-1] if self.function_stack else (self._owner(), None)
            return (*scope, node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            return (self._owner(), "self", node.attr)
        return None
    def _origin_for_key(self, key):
        if key is None:
            return (False, False, False, False)
        if key[1] == "self":
            return self.path_origins.get(key, (False, False, False, False))
        return self.path_origins.get(
            key,
            self.path_origins.get(("<module>", None, key[2]), (False, False, False, False)),
        )
    @staticmethod
    def _qualified_symbol(node, modules, symbols):
        if isinstance(node, ast.Name):
            return symbols.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            return node.attr
        return None
    def _host_mode_symbol(self, node):
        return self._qualified_symbol(node, self.host_mode_modules, self.host_mode_symbols)
    def _is_temporary_factory(self, node):
        symbol = self._qualified_symbol(node.func, self.temporary_modules, {}) if isinstance(node, ast.Call) else None
        return (
            symbol == "TemporaryDirectory"
            or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "tempfile"
                and node.func.attr == "TemporaryDirectory")
            or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in self.temporary_factories | self.temporary_symbols)
        )
    def _is_homebrew_builder(self, node):
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name) and node.func.id in self.homebrew_builders:
            return True
        return (
            isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.homebrew_modules
            and node.func.attr == "build_homebrew_rom"
        )
    def _is_capture_mock(self, node):
        if not isinstance(node, ast.Call):
            return False
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "object" and len(node.args) >= 2):
            return False
        return (
            self._qualified_symbol(node.args[0], self.gba_playtest_modules, self.gba_playtest_symbols) is None
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in self.gba_playtest_modules
            and _literal_string_tail(node.args[1])
            in {"_compiler_command", "build_backend", "capture", "subprocess"}
        )
    def _is_temporary_generated_path(self, node):
        key = self._origin_key(node)
        if key in self.generated_paths:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id == "Path" and any(
                self._is_temporary_generated_path(argument)
                for argument in node.args
            )
        return False
    def _contains_generated_path(self, node):
        return any(
            self._is_temporary_generated_path(candidate)
            for candidate in ast.walk(node)
            if isinstance(candidate, (ast.Name, ast.Call))
        )
    def _path_origin(self, node):
        if isinstance(node, ast.Name):
            if node.id == "REPO_ROOT":
                return (True, False, False, False)
            host_symbol = self._host_mode_symbol(node)
            if host_symbol == "REPO_ROOT":
                return (True, False, False, False)
            if host_symbol in _HOST_MODE_ARTIFACT_ATTRIBUTES:
                return (True, True, False, False)
            return self._origin_for_key(self._origin_key(node))

        if isinstance(node, ast.Attribute):
            host_symbol = self._host_mode_symbol(node)
            if host_symbol == "REPO_ROOT":
                return (True, False, False, False)
            if host_symbol in _HOST_MODE_ARTIFACT_ATTRIBUTES:
                return (True, True, False, False)
            origin = self._origin_for_key(self._origin_key(node))
            if origin != (False, False, False, False):
                return origin
            return self._path_origin(node.value)

        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            rooted, artifact, temporary, dynamic = self._path_origin(node.left)
            return (
                rooted,
                artifact or ((rooted or temporary) and _has_artifact_suffix(node.right)),
                temporary,
                dynamic,
            )

        if isinstance(node, ast.Call):
            if self._is_temporary_factory(node):
                return (False, False, True, False)

            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "Path"
                and node.args
            ):
                origins = [self._path_origin(argument) for argument in node.args]
                rooted = any(origin[0] for origin in origins)
                temporary = any(origin[2] for origin in origins)
                dynamic = any(origin[3] for origin in origins)
                artifact = any(origin[1] for origin in origins) or (
                    (rooted or temporary)
                    and any(_has_artifact_suffix(argument) for argument in node.args)
                )
                return (rooted, artifact, temporary, dynamic)

            if isinstance(node.func, ast.Attribute):
                rooted, artifact, temporary, dynamic = self._path_origin(node.func.value)
                if node.func.attr in {"joinpath", "with_name", "with_suffix"}:
                    artifact = artifact or (
                        (rooted or temporary)
                        and any(_has_artifact_suffix(argument) for argument in node.args)
                    )
                    return (rooted, artifact, temporary, dynamic)

            if self._host_mode_symbol(node.func) in {"modern_elf", "modern_rom"}:
                return (True, True, False, False)

            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "os"
                and node.func.value.attr == "environ"
                and node.func.attr == "get"
            ):
                return (False, False, False, True)

        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
        ):
            return (False, False, False, True)

        return (False, False, False, False)
    def _remember_assignment(self, targets, value):
        origin = self._path_origin(value)
        for target in targets:
            key = self._origin_key(target)
            if key is not None:
                self.path_origins[key] = origin
                if self._contains_generated_path(value):
                    self.generated_paths.add(key)
    def visit_Module(self, node):
        self.module_helpers = {
            ("<module>", statement.name)
            for statement in node.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for statement in node.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                self.visit(statement)
        self.temporary_factories |= {
            function.name
            for function in node.body
            if isinstance(function, ast.FunctionDef)
            and any(
                self._is_temporary_factory(call)
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
            )
        }
        for statement in node.body:
            if not isinstance(statement, (ast.Import, ast.ImportFrom)):
                self.visit(statement)
    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            if alias.name == "host_mode":
                self.host_mode_modules.add(local)
            elif alias.name == "gba_playtest":
                self.gba_playtest_modules.add(local)
            elif alias.name == "homebrew_fixture":
                self.homebrew_modules.add(local)
            elif alias.name == "tempfile":
                self.temporary_modules.add(local)
    def visit_ImportFrom(self, node):
        for alias in node.names:
            local = alias.asname or alias.name
            if node.module == "host_mode":
                self.host_mode_symbols[local] = alias.name
            elif node.module == "gba_playtest":
                self.gba_playtest_symbols[local] = alias.name
            elif node.module == "homebrew_fixture" and alias.name == "build_homebrew_rom":
                self.homebrew_builders.add(local)
            elif node.module == "tempfile" and alias.name == "TemporaryDirectory":
                self.temporary_symbols.add(local)
    def visit_ClassDef(self, node):
        if self.function_depth:
            self.generic_visit(node)
            return
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()
    def _visit_function(self, node):
        key = (self._owner(), node.name)
        parameters = tuple(
            argument.arg
            for argument in (*node.args.posonlyargs, *node.args.args)
        )
        self.function_parameters[key] = (
            parameters[1:]
            if self.class_stack and parameters and parameters[0] == "self"
            else parameters
        )
        self.function_stack.append(key)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1
        self.function_stack.pop()
    def visit_FunctionDef(self, node):
        self._visit_function(node)
    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)
    def visit_Assign(self, node):
        self._remember_assignment(node.targets, node.value)
        self.generic_visit(node)
    def visit_AnnAssign(self, node):
        if node.value is not None:
            self._remember_assignment((node.target,), node.value)
        self.generic_visit(node)
    def visit_BinOp(self, node):
        rooted, artifact, _temporary, _dynamic = self._path_origin(node)
        if rooted and artifact:
            self._mark_repository("repository-path construction")
        self.generic_visit(node)
    def visit_Name(self, node):
        host_symbol = self._host_mode_symbol(node)
        if host_symbol in _HOST_MODE_ARTIFACT_ATTRIBUTES:
            self._mark_repository(f"host_mode.{host_symbol}")
    def visit_Attribute(self, node):
        host_symbol = self._host_mode_symbol(node)
        if host_symbol in _HOST_MODE_ARTIFACT_ATTRIBUTES:
            self._mark_repository(f"host_mode.{host_symbol}")
        self.generic_visit(node)
    def visit_Call(self, node):
        func = node.func
        host_symbol = self._host_mode_symbol(func)
        if host_symbol in _HOST_MODE_ARTIFACT_APIS:
            if host_symbol in {"capture_live_or_skip", "require_built_rom"}:
                self._mark_io(f"host_mode.{host_symbol}")
            else:
                self._mark_repository(f"host_mode.{host_symbol}")

        helper = ("<module>", func.id) if isinstance(func, ast.Name) else None
        if helper in self.module_helpers:
            caller = self.function_stack[-1] if self.function_depth and not self.class_stack else self._owner()
            self.helper_callers.setdefault(helper, set()).add(caller)

        if self._is_homebrew_builder(node) and node.args:
            _rooted, _artifact, temporary, _dynamic = self._path_origin(node.args[0])
            key = self._origin_key(node.args[0])
            if temporary and key is not None:
                self.generated_paths.add(key)

        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            self.method_calls.append(
                (
                    self._owner(),
                    func.attr,
                    tuple(
                        (
                            self._path_origin(argument),
                            self._is_temporary_generated_path(argument),
                        )
                        for argument in node.args
                    ),
                )
            )

        if self._qualified_symbol(
            func,
            self.gba_playtest_modules,
            self.gba_playtest_symbols,
        ) == "capture":
            _rooted, _artifact, temporary, dynamic = (
                self._path_origin(node.args[0])
                if node.args
                else (False, False, False, False)
            )
            if not (
                self.capture_mock_depth
                or (temporary and node.args and self._is_temporary_generated_path(node.args[0]))
            ):
                if self.function_depth and not self.class_stack:
                    pass
                else:
                    self.direct_capture_owners.add(self._owner())
                    self.direct_capture_records.append(
                        (
                            self._owner(),
                            self.function_stack[-1] if self.function_stack else None,
                            node.args[0] if node.args else None,
                        )
                    )
                self._mark_io(
                    "environment-derived live capture"
                    if dynamic
                    else "direct gba_playtest.capture"
                )

        if (
            isinstance(func, ast.Name)
            and func.id == "open"
            and node.args
            and self._path_origin(node.args[0])[1]
        ):
            self._mark_io("repository artifact open")

        if (
            isinstance(func, ast.Attribute)
            and func.attr in _ARTIFACT_ACCESSORS
        ):
            rooted, artifact, temporary, _dynamic = self._path_origin(func.value)
            if rooted and artifact and not temporary:
                self._mark_io(f"repository artifact {func.attr}")

        if (
            isinstance(func, ast.Attribute)
            and func.attr == "write_bytes"
            and node.args
            and self._path_origin(func.value)[2]
            and self._contains_generated_path(node.args[0])
        ):
            key = self._origin_key(func.value)
            if key is not None:
                self.generated_paths.add(key)

        rooted, artifact, _temporary, _dynamic = self._path_origin(node)
        is_host_mode_factory = host_symbol in {"modern_elf", "modern_rom"}
        if rooted and artifact and not is_host_mode_factory:
            self._mark_repository("repository-path construction")
        self.generic_visit(node)
    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None and self._is_temporary_factory(
                item.context_expr
            ):
                key = self._origin_key(item.optional_vars)
                if key is not None:
                    self.path_origins[key] = (False, False, True, False)
        mocked = sum(self._is_capture_mock(item.context_expr) for item in node.items)
        self.capture_mock_depth += mocked
        for statement in node.body:
            self.visit(statement)
        self.capture_mock_depth -= mocked
    @property
    def live_capture_owners(self):
        owners = set()
        for owner, function, argument in self.direct_capture_records:
            if (
                argument is not None
                and self._path_origin(argument)[2]
                and self._is_temporary_generated_path(argument)
            ):
                continue
            if (
                function is not None
                and isinstance(argument, ast.Name)
                and argument.id in self.function_parameters[function]
            ):
                argument_index = self.function_parameters[function].index(argument.id)
                callers = [
                    arguments
                    for call_owner, method, arguments in self.method_calls
                    if (call_owner, method) == function
                ]
                if callers and all(
                    len(arguments) > argument_index
                    and arguments[argument_index][0][2]
                    and arguments[argument_index][1]
                    for arguments in callers
                ):
                    continue
            owners.add(owner)
        return owners

    @property
    def helper_owners(self):
        def callers(helper, seen):
            if helper in seen:
                return {"<module>"}
            return set().union(*(callers(caller, seen | {helper}) if isinstance(caller, tuple) else {caller}
                                 for caller in self.helper_callers.get(helper, {"<module>"})))
        return set().union(*(callers(helper, set()) for helper in self.helper_ios)) if self.helper_ios else set()
def _discover_artifacts(path, source=None):
    if source is None:
        source = path.read_text(encoding="utf-8")
    visitor = _ArtifactDiscovery()
    visitor.visit(ast.parse(source, filename=str(path)))
    return visitor
def _registered_owner_errors(module, owners, registered, guarded, module_io=frozenset()):
    errors = []
    module_classes = {
        class_name for module_name, class_name in registered if module_name == module
    }
    if owners and not module_classes:
        errors.append(f"{module}: repository artifact owner is not registered")
    for owner in owners:
        if owner == "<module>":
            if module_io:
                errors.append(f"{module}: module-level artifact I/O is not guardable")
            elif any((module, class_name) not in guarded for class_name in module_classes):
                errors.append(f"{module}: module artifact owner lacks a central guard")
        elif (module, owner) not in registered:
            module_object = None
            try:
                module_object = importlib.import_module(module)
            except ModuleNotFoundError:
                pass
            owner_class = getattr(module_object, owner, None)
            if not isinstance(owner_class, type) or not any(
                issubclass(
                    getattr(importlib.import_module(module), class_name),
                    owner_class,
                )
                and (module, class_name) in guarded
                for class_name in module_classes
            ):
                errors.append(f"{module}.{owner}: artifact owner is not registered")
        elif (module, owner) not in guarded:
            errors.append(f"{module}.{owner}: artifact owner lacks a central guard")
    return errors
def _run_case(test_class) -> unittest.TestResult:
    """Run one TestCase class in-process and return its raw result."""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_class)
    result = unittest.TestResult()
    suite.run(result)
    return result


def _host_only(value="1"):
    return mock.patch.dict(os.environ, {host_mode.ENV_VAR: value})


def _normal_mode():
    environ = dict(os.environ)
    environ.pop(host_mode.ENV_VAR, None)
    return mock.patch.dict(os.environ, environ, clear=True)


def _poisoned_capture():
    """Any live capture attempt becomes a loud, attributable failure."""
    return mock.patch.object(
        gba_playtest,
        "capture",
        side_effect=AssertionError(
            "gba_playtest.capture() was reached in host-only mode: a live "
            "ROM integration test is not guarded by tests/host_mode.py"
        ),
    )


def _fingerprint(path: Path):
    stat = path.stat()
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        stat.st_size,
        stat.st_mtime_ns,
    )


class HostOnlyEnvContractTests(unittest.TestCase):
    """The mode is a strict, explicit boolean -- never inferred."""

    def test_unset_defaults_to_normal_mode(self):
        self.assertFalse(host_mode.host_only_enabled({}))

    def test_true_vocabulary_covers_case_and_surrounding_whitespace(self):
        for raw in ("1", "true", "TRUE", "Yes", "on", "  1  "):
            with self.subTest(raw=raw):
                self.assertTrue(
                    host_mode.host_only_enabled({host_mode.ENV_VAR: raw})
                )

    def test_false_vocabulary_keeps_normal_mode(self):
        for raw in ("", "0", "false", "No", "OFF"):
            with self.subTest(raw=raw):
                self.assertFalse(
                    host_mode.host_only_enabled({host_mode.ENV_VAR: raw})
                )

    def test_unrecognized_value_is_refused_never_guessed(self):
        for raw in ("maybe", "2", "host-only", "y"):
            with self.subTest(raw=raw):
                with self.assertRaises(RuntimeError) as ctx:
                    host_mode.host_only_enabled({host_mode.ENV_VAR: raw})
                self.assertIn(host_mode.ENV_VAR, str(ctx.exception))

    def test_guard_skips_only_in_host_only_mode(self):
        with _host_only():
            with self.assertRaises(unittest.SkipTest):
                host_mode.guard("probe")
        with _normal_mode():
            self.assertIsNone(host_mode.guard("probe"))

    def test_skip_reason_names_the_gates_that_own_live_coverage(self):
        reason = host_mode.skip_reason("probe")
        self.assertIn("host-only mode", reason)
        self.assertIn("expansion-modern-linker-check", reason)
        self.assertIn("expansion-modern-itemexpansion-check", reason)


class HostOnlyClassificationTests(unittest.TestCase):
    """Category A vs. Category B is explicit, complete and enforced."""

    def test_every_registered_live_class_exists_and_is_guarded(self):
        for module_name, class_name in host_mode.LIVE_TEST_CLASSES:
            with self.subTest(module=module_name, cls=class_name):
                module = importlib.import_module(module_name)
                test_class = getattr(module, class_name)
                self.assertTrue(issubclass(test_class, unittest.TestCase))
                self.assertTrue(
                    getattr(test_class, "is_live_artifact_testcase", False),
                    f"{module_name}.{class_name} is registered as live but is "
                    f"not decorated with host_mode.live_artifact_testcase",
                )

    def test_registered_live_classes_skip_in_host_only_mode(self):
        for module_name, class_name in host_mode.LIVE_TEST_CLASSES:
            with self.subTest(module=module_name, cls=class_name):
                module = importlib.import_module(module_name)
                test_class = getattr(module, class_name)
                with _host_only(), _poisoned_capture():
                    result = _run_case(test_class)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual(result.testsRun, 0)
                self.assertTrue(result.skipped)
                for _test, reason in result.skipped:
                    self.assertIn("host-only mode", reason)

    def test_live_modules_have_no_unregistered_live_entry_point(self):
        """Run EVERY class of the live modules in host-only mode with a
        poisoned capture: an unregistered/unguarded live test would reach
        libmGBA and fail loudly instead of hiding behind an artifact check."""
        registered = {f"{m}.{c}" for m, c in host_mode.LIVE_TEST_CLASSES}
        for module_name in host_mode.LIVE_TEST_MODULES:
            module = importlib.import_module(module_name)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            with _host_only(), _poisoned_capture():
                result = unittest.TestResult()
                suite.run(result)
            with self.subTest(module=module_name):
                # Attribute precisely: only a *poisoned capture* traceback is
                # this contract failing (an unrelated red test in the same
                # module must not be reported here).
                for test, trace in result.errors + result.failures:
                    self.assertNotIn(
                        "was reached in host-only mode", trace,
                        f"{test} reached a live capture in host-only mode: it "
                        f"is a live test that is not guarded by host_mode",
                    )
                for test, reason in result.skipped:
                    if "host-only mode" in reason:
                        self.assertTrue(
                            any(name in str(test) for name in registered),
                            f"{test} skipped as live but is not registered in "
                            f"host_mode.LIVE_TEST_CLASSES",
                        )

    def test_category_a_tests_still_run_in_host_only_mode(self):
        """Pure host/schema classes inside live modules keep running: the fix
        must not degrade into a blanket suite-level skip."""
        cases = (
            ("test_new_game_scenario", "NewGameScenarioFilesTests"),
            ("test_save_load_scenario", "SaveLoadScenarioFilesTests"),
            ("test_tools_scenario", "ToolsReleaseNegativeFilesTests"),
            ("test_savesuspend_resume_scenario", "SavesuspendResumeScenarioFilesTests"),
        )
        for module_name, class_name in cases:
            with self.subTest(module=module_name, cls=class_name):
                module = importlib.import_module(module_name)
                with _host_only(), _poisoned_capture():
                    result = _run_case(getattr(module, class_name))
                self.assertGreater(result.testsRun, 0)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual(result.skipped, [])

    def test_repository_rom_paths_are_only_built_in_host_mode(self):
        offenders = {}
        for path in sorted(TESTS_DIR.glob("*.py")):
            if path.name in ("host_mode.py", Path(__file__).name):
                continue
            discovery = _discover_artifacts(path)
            direct = {
                owner: reasons
                for owner, reasons in discovery.repository_owners.items()
                if "repository-path construction" in reasons
            }
            if direct:
                offenders[path.name] = direct
        self.assertEqual(
            offenders, {},
            "repository ROM/ELF paths must come from tests/host_mode.py so a "
            "single contract owns every artifact-dependent test",
        )

    def test_modules_using_repository_roms_are_registered_as_live(self):
        registered = set(host_mode.LIVE_TEST_CLASSES)
        guarded = set()
        for module_name, class_name in registered:
            test_class = getattr(importlib.import_module(module_name), class_name)
            if getattr(test_class, "is_live_artifact_testcase", False):
                guarded.add((module_name, class_name))

        for path in sorted(TESTS_DIR.glob("test_*.py")):
            if path.name == Path(__file__).name:
                continue
            discovery = _discover_artifacts(path)
            safe_capture_owners = discovery.direct_capture_owners - discovery.live_capture_owners
            owners = {
                owner for owner, reasons in discovery.repository_owners.items()
                if reasons - ({"direct gba_playtest.capture"} if owner in safe_capture_owners else set())
            } | discovery.live_capture_owners | discovery.helper_owners
            errors = _registered_owner_errors(path.stem, owners, registered, guarded, discovery.module_io)
            with self.subTest(module=path.stem):
                self.assertEqual(errors, [])

        fixture = _discover_artifacts(
            Path("fixture.py"),
            """
import os, tempfile as tf, gba_playtest, host_mode as hm
from homebrew_fixture import build_homebrew_rom
from host_mode import LIVE_ROMS as configured, modern_rom as build_rom
from pathlib import Path
REPO_ROOT = Path("/repository")
hm.modern_rom("release").read_bytes()
def read_live(): return hm.modern_rom("release").read_bytes()
class Registered:
    def run(self): return build_rom("release")
class Alias:
    def run(self): return hm.modern_elf("release")
LIVE = configured
class Escaped:
    def run(self): gba_playtest.capture(Path(os.environ["ROM"]), object())
class Fstring:
    def run(self): (REPO_ROOT / f"{profile}.gba").read_bytes()
class Concat:
    def run(self): (REPO_ROOT / (name + ".elf")).stat()
class Unguarded:
    def run(self): read_live()
class Guarded:
    def run(self): read_live()
class Cross:
    def live(self): rom = hm.modern_rom("release"); gba_playtest.capture(rom, object())
    def temporary(self):
        with tf.TemporaryDirectory() as directory:
            rom = Path(directory) / "fixture.gba"; build_homebrew_rom(rom); gba_playtest.capture(rom, object())
    def bind(self): self.rom = hm.modern_elf("release")
    def use(self): self.rom.read_bytes()
""",
        )
        guarded = {("fixture", "Registered"), ("fixture", "Alias"), ("fixture", "Guarded")}
        self.assertEqual(_registered_owner_errors("fixture", {"Registered", "Alias"}, guarded, guarded), [])
        self.assertEqual(fixture.helper_owners, {"Unguarded", "Guarded"})
        self.assertTrue(_registered_owner_errors("fixture", fixture.helper_owners, guarded, guarded))
        self.assertEqual(fixture.live_capture_owners, {"Escaped", "Cross"})
        self.assertTrue({"Fstring", "Concat", "Cross"} <= set(fixture.repository_owners))


class HostOnlyCentralGateTests(unittest.TestCase):
    """The central choke points behave identically for every live test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="gba-playtest-host-only-")
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)

    def _appearing_rom(self, target: Path):
        """A ROM path whose very first existence probe materializes the file,
        modelling a concurrent build finishing mid-run."""
        rom = mock.MagicMock(spec=Path)

        def _exists():
            target.write_bytes(b"CONCURRENTLY-BUILT-ROM")
            return True

        rom.exists.side_effect = _exists
        rom.__str__.return_value = str(target)
        return rom

    def test_require_built_rom_skips_before_probing_in_host_only_mode(self):
        target = self.tmp / "appears.gba"
        rom = self._appearing_rom(target)
        with _host_only():
            with self.assertRaises(unittest.SkipTest):
                host_mode.require_built_rom(rom, "modern debug ROM")
        rom.exists.assert_not_called()
        self.assertFalse(
            target.exists(),
            "host-only mode must not probe the artifact at all",
        )

    def test_require_built_rom_probes_normally_in_normal_mode(self):
        target = self.tmp / "appears.gba"
        rom = self._appearing_rom(target)
        with _normal_mode():
            host_mode.require_built_rom(rom, "modern debug ROM")
        rom.exists.assert_called_once()
        self.assertTrue(target.exists())

    def test_require_built_rom_still_skips_when_absent_in_normal_mode(self):
        missing = self.tmp / "not-built.gba"
        with _normal_mode():
            with self.assertRaises(unittest.SkipTest) as ctx:
                host_mode.require_built_rom(missing, "modern debug ROM")
        self.assertIn("not built", str(ctx.exception))

    def test_capture_live_or_skip_is_defense_in_depth(self):
        rom = self.tmp / "stale.gba"
        rom.write_bytes(b"STALE")
        with _host_only(), _poisoned_capture():
            with self.assertRaises(unittest.SkipTest):
                host_mode.capture_live_or_skip(rom, object(), label="probe")

    def test_backend_unavailable_still_skips_in_normal_mode(self):
        rom = self.tmp / "stale.gba"
        rom.write_bytes(b"STALE")
        error = gba_playtest.PlaytestError("cannot find -lmgba")
        with _normal_mode(), mock.patch.object(
            gba_playtest, "capture", side_effect=error
        ):
            with self.assertRaises(unittest.SkipTest):
                host_mode.capture_live_or_skip(rom, object(), label="probe")

    def test_other_playtest_errors_still_fail_loudly_in_normal_mode(self):
        rom = self.tmp / "stale.gba"
        rom.write_bytes(b"STALE")
        error = gba_playtest.PlaytestError("no mGBA core recognizes ROM")
        with _normal_mode(), mock.patch.object(
            gba_playtest, "capture", side_effect=error
        ):
            with self.assertRaises(gba_playtest.PlaytestError):
                host_mode.capture_live_or_skip(rom, object(), label="probe")


class HostOnlyLiveClassArtifactTimingTests(unittest.TestCase):
    """A real live TestCase family (the save-compat factory) against staged
    artifacts: host-only never probes, normal mode still does."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="gba-playtest-host-only-")
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)
        self.module = importlib.import_module("test_save_compat_scenarios")

    def test_live_class_skips_when_the_rom_appears_between_runs(self):
        rom = self.tmp / "fireemblem8.gba"
        test_class = self.module._make_test_class("modern-release", rom)

        with _host_only(), _poisoned_capture():
            absent = _run_case(test_class)
        self.assertEqual(absent.testsRun, 0)
        self.assertTrue(absent.skipped)

        # A concurrent build lands a stale ROM at the expected path mid-run.
        rom.write_bytes(b"STALE-CONCURRENTLY-BUILT-ROM")
        before = _fingerprint(rom)

        with _host_only(), _poisoned_capture():
            present = _run_case(test_class)
        self.assertEqual(present.errors, [])
        self.assertEqual(present.failures, [])
        self.assertEqual(present.testsRun, 0)
        for _test, reason in present.skipped:
            self.assertIn("host-only mode", reason)
        self.assertEqual(
            _fingerprint(rom), before,
            "host-only mode must leave a staged artifact byte-, size- and "
            "mtime-identical",
        )

    def test_normal_mode_still_captures_against_the_same_staged_rom(self):
        rom = self.tmp / "fireemblem8.gba"
        rom.write_bytes(b"STALE-CONCURRENTLY-BUILT-ROM")
        test_class = self.module._make_test_class("modern-release", rom)
        captured = []

        def _fake_capture(rom_path, scenario, sram_image=None, retries=0):
            captured.append(Path(str(rom_path)))
            raise gba_playtest.PlaytestError("no mGBA core recognizes ROM")

        with _normal_mode(), mock.patch.object(
            gba_playtest, "capture", side_effect=_fake_capture
        ):
            result = _run_case(test_class)

        self.assertTrue(captured, "normal mode must still attempt a live capture")
        self.assertEqual(captured[0], rom)
        self.assertTrue(result.errors or result.failures)
        self.assertEqual(
            [reason for _t, reason in result.skipped if "host-only" in reason], []
        )


class HostOnlyStagedWorktreeSubprocessTests(unittest.TestCase):
    """End-to-end: the exact CI host command in a hermetic worktree copy that
    is deliberately full of stale, mismatched artifacts."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="gba-playtest-staged-tree-")
        cls.tree = Path(cls._tmpdir.name)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(
            REPO_ROOT / "tools" / "gba-playtest",
            cls.tree / "tools" / "gba-playtest",
            ignore=ignore,
        )
        shutil.copytree(
            REPO_ROOT / "scripts" / "modernize",
            cls.tree / "scripts" / "modernize",
            ignore=ignore,
        )
        # Issue #18 integration: scripts/modernize/expansion_config.py (and,
        # transitively, save_format_tool.py/sram_fixture.py, which the
        # save/suspend-resume live test classes below import) now imports
        # scripts.localization.schema as the single source of truth for
        # locale ids/counts, so this hermetic staged tree must carry that
        # package too or every live class import fails with
        # ModuleNotFoundError before host-only mode even gets a chance to
        # skip it -- a staged-tree gap, not a real host-only-mode defect.
        shutil.copytree(
            REPO_ROOT / "scripts" / "localization",
            cls.tree / "scripts" / "localization",
            ignore=ignore,
        )
        shutil.copy2(REPO_ROOT / "config.mk", cls.tree / "config.mk")
        cls.staged = []
        for index, relative in enumerate(_STAGED_ARTIFACT_RELATIVE_PATHS):
            path = cls.tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # ROM-shaped (large enough to carry a GBA header) but
            # deliberately mismatched content: if any live test ever ran, it
            # would fail loudly instead of silently passing.
            path.write_bytes((b"STALE-MISMATCHED-ARTIFACT-" + bytes([index])) * 64)
            cls.staged.append(path)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def _run_live_classes(self, host_only: bool, targets=None):
        environment = dict(os.environ)
        if host_only:
            environment[host_mode.ENV_VAR] = "1"
        else:
            environment.pop(host_mode.ENV_VAR, None)
        if targets is None:
            targets = [f"{m}.{c}" for m, c in host_mode.LIVE_TEST_CLASSES]
        return subprocess.run(
            [sys.executable, "-m", "unittest", "-v"] + list(targets),
            cwd=str(self.tree / "tools" / "gba-playtest" / "tests"),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_host_only_skips_every_live_class_and_never_touches_an_artifact(self):
        before = {path: _fingerprint(path) for path in self.staged}
        result = self._run_live_classes(host_only=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(
            f"OK (skipped={len(host_mode.LIVE_TEST_CLASSES)})", result.stdout
        )
        self.assertIn("Ran 0 tests", result.stdout)
        for module_name, class_name in host_mode.LIVE_TEST_CLASSES:
            self.assertIn(f"{module_name}.{class_name}", result.stdout)
        self.assertNotIn("no mGBA core recognizes ROM", result.stdout)
        for path in self.staged:
            self.assertTrue(path.is_file(), f"host-only mode deleted {path}")
            self.assertEqual(
                _fingerprint(path), before[path],
                f"host-only mode modified {path}",
            )

    def test_normal_mode_in_the_same_tree_really_opens_the_stale_rom(self):
        before = {path: _fingerprint(path) for path in self.staged}
        result = self._run_live_classes(
            host_only=False, targets=["test_combat_scenario.CombatRuntimeTests"]
        )
        self.assertNotIn("host-only mode", result.stdout)
        self.assertNotIn("not built", result.stdout)
        # Either the live capture ran against the stale ROM and failed loudly
        # (the normal-mode contract: artifact present -> live run), or this
        # environment has no libmGBA at all and says so explicitly.
        if "libmGBA integration skipped explicitly" in result.stdout:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("capture_live_or_skip", result.stdout)
        for path in self.staged:
            self.assertEqual(_fingerprint(path), before[path])


if __name__ == "__main__":
    unittest.main()
