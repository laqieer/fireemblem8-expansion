"""Use the selected native compiler inputs for retained message references."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import TypedDict

from scripts.localization.game_locales.structural_completion import (
    StructuralCompletionError,
    _MESSAGE_CALL_ARGUMENTS,
    _iter_message_fields,
    _iter_named_calls,
    _mask_c_comments_and_strings,
    _message_designator_start,
    _scan_balanced,
)

from .model import GameCatalogError


class CompiledInputError(GameCatalogError):
    """The selected native input set cannot establish its actual source contract."""


class CompileUnit(TypedDict):
    logical_source: str
    selected_source: str
    compiler_input: str
    object: str
    compiler_argv: list[str]
    preprocessor_argv: list[str]


class CompileContext(TypedDict):
    source_root: str
    linker: str
    units: list[CompileUnit]


def load_compile_context(path: Path) -> CompileContext:
    try:
        context = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompiledInputError(
            f"{path}: selected compilation context is unreadable or malformed: {error}"
        ) from error
    if (not isinstance(context, dict) or type(context.get("schema_version")) is not int
            or context["schema_version"] != 1 or context.get("kind") != "fe8-selected-text-inputs"
            or not isinstance(context.get("source_root"), str)
            or not Path(context["source_root"]).is_absolute()
            or not isinstance(context.get("linker"), str) or not context["linker"]
            or not isinstance(context.get("units"), list) or not context["units"]):
        raise CompiledInputError(f"{path}: invalid selected compilation context")
    units: list[CompileUnit] = []
    names = set()
    for unit in context["units"]:
        if (not isinstance(unit, dict) or any(
                not isinstance(unit.get(field), str) or not unit[field]
                for field in ("logical_source", "selected_source", "compiler_input", "object"))
                or not isinstance(unit.get("compiler_argv"), list)
                or any(not isinstance(argument, str) for argument in unit["compiler_argv"])
                or not isinstance(unit.get("preprocessor_argv", []), list)
                or any(not isinstance(argument, str)
                       for argument in unit.get("preprocessor_argv", []))):
            raise CompiledInputError(f"{path}: invalid selected compilation unit")
        name = unit["logical_source"]
        argv = unit["compiler_argv"]
        if (name in names or argv.count("-c") != 1 or len(argv) < 5
                or argv[-2:] != ["-o", unit["object"]]
                or argv[argv.index("-c") + 1] != unit["compiler_input"]):
            raise CompiledInputError(f"{path}: ambiguous selected compilation unit {name}")
        names.add(name)
        units.append({
            "logical_source": name, "selected_source": unit["selected_source"],
            "compiler_input": unit["compiler_input"], "object": unit["object"],
            "compiler_argv": argv, "preprocessor_argv": unit.get("preprocessor_argv", []),
        })
    return {
        "source_root": context["source_root"], "linker": context["linker"], "units": units,
    }


def _native_command(
    unit: CompileUnit, output: Path, *, source: Path | None = None, preprocess: bool = False,
) -> list[str]:
    command = []
    arguments = iter(unit["compiler_argv"])
    for argument in arguments:
        if argument in ("-MF", "-MQ", "-MT"):
            next(arguments, None)
        elif argument not in ("-MMD", "-MD", "-MP"):
            command.append(argument)
    if source is not None:
        command[command.index("-c") + 1] = str(source)
    if preprocess:
        command[command.index("-c")] = "-E"
    command[-1] = str(output)
    return command


def _scalar_initializer_operand(value: str) -> str | None:
    while True:
        masked = _mask_c_comments_and_strings(value)
        literals = _mask_c_comments_and_strings(value, mask_strings=False)
        opening = len(literals) - len(literals.lstrip())
        if opening >= len(masked) or masked[opening] != "{":
            return value
        _, closing = _scan_balanced(masked, opening)
        if masked[closing + 1:].strip():
            return None
        body = masked[opening + 1:closing]
        end = len(literals[opening + 1:closing].rstrip())
        if end and body[end - 1] == ",":
            end -= 1
        index = 0
        while index < end:
            if body[index] in "([{":
                _, nested = _scan_balanced(body, index)
                index = nested + 1
            elif body[index] == ",":
                return None
            else:
                index += 1
        if body.lstrip().startswith((".", "[")):
            return None
        value = value[opening + 1:opening + 1 + end].strip()
        if not value:
            return "0"


def _initializer_declaration_end(masked: str, closing: int) -> int:
    index = closing + 1
    while index < len(masked):
        if masked[index] in "([{":
            _, nested = _scan_balanced(masked, index)
            index = nested + 1
        elif masked[index] == ";":
            return index + 1
        else:
            index += 1
    raise CompiledInputError("native initializer declaration has no statement terminator")


def _checked_initializers(source: str, target_count: int) -> str:
    masked = _mask_c_comments_and_strings(source)
    references = []
    owners = []
    for match in re.finditer(
        r"(?<![\w.])\b([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)"
        r"(?:__attribute__\([^=;]*\)\s*)?=\s*\{", masked,
    ):
        opening = masked.index("{", match.start())
        _, closing = _scan_balanced(masked, opening)
        owners.append((
            opening, closing, match.group(1), match.group(2).count("["),
            _initializer_declaration_end(masked, closing),
        ))
    for kind, start, end in _iter_message_fields(masked):
        containing = [(closing - opening, owner, rank, after)
                      for opening, closing, owner, rank, after in owners
                      if opening < start < closing]
        if not containing:
            raise CompiledInputError(
                f"cannot bind native initializer field {kind}({source[start:end].strip()})"
            )
        _, owner, rank, after = min(containing)
        dot = masked.rfind(".", 0, start)
        designator = _message_designator_start(masked, dot)
        if designator is None:
            raise CompiledInputError("native message field lost its initializer designator")
        prefix = source[designator:dot].strip()
        for _ in range(rank):
            if not prefix.startswith("["):
                break
            _, closing = _scan_balanced(_mask_c_comments_and_strings(prefix), 0)
            prefix = prefix[closing + 1:].lstrip()
        member = owner + "[0]" * rank + prefix + "." + kind
        references.append((source[start:end].strip(), member, after))
    insertions = []
    for index, (value, member, after) in enumerate(references):
        integer = f"(__builtin_classify_type(({member})) == 1)"
        operand = _scalar_initializer_operand(value)
        condition = f"!{integer}"
        if operand is not None:
            destination = f"__typeof__(__builtin_choose_expr({integer}, ({member}), 0))"
            converted = (
                f"({destination})(__builtin_choose_expr({integer}, ({operand}), 0))"
            )
            condition = (
                f"__builtin_choose_expr({integer} && __builtin_constant_p(({converted})), "
                f"(unsigned long long)({converted}) < {target_count}ULL, 1)"
            )
        insertions.append((
            after, "\n__extension__ typedef char "
            f"__fe8_message_reference_outside_active_set_{index}[({condition}) ? 1 : -1];\n",
        ))
    for offset, text in sorted(insertions, reverse=True):
        source = source[:offset] + text + source[offset:]
    return source


def _validate_native_calls(source: str, target_count: int, selected_source: str) -> None:
    for kind, _, _, operands in _iter_named_calls(
        _mask_c_comments_and_strings(source), _MESSAGE_CALL_ARGUMENTS,
    ):
        for argument in _MESSAGE_CALL_ARGUMENTS[kind]:
            if argument >= len(operands):
                continue
            operand = operands[argument].strip()
            if re.fullmatch(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)[uUlL]*", operand):
                value = int(operand.rstrip("uUlL"), 16 if "x" in operand.lower() else 10)
                if not 0 <= value < target_count:
                    raise CompiledInputError(
                        f"{selected_source}: native {kind}({operand}) is outside the active "
                        f"message set of {target_count} targets"
                    )


def validate_compiled_references(context_path: Path, *, target_count: int) -> None:
    context = load_compile_context(context_path)
    root = Path(context["source_root"])
    with TemporaryDirectory(prefix="selected-text-cpp-") as directory:
        for index, unit in enumerate(context["units"]):
            output = Path(directory) / f"{index}.i"
            try:
                result = subprocess.run(
                    _native_command(unit, output, preprocess=True),
                    cwd=root, capture_output=True, text=True, timeout=90,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise CompiledInputError(
                    f"{context_path}: {unit['selected_source']}: native preprocessing failed: {error}"
                ) from error
            if result.returncode:
                raise CompiledInputError(
                    f"{context_path}: {unit['selected_source']}: native preprocessing failed:\n"
                    + result.stdout + result.stderr
                )
            try:
                native_source = output.read_text(encoding="utf-8")
                checked = _checked_initializers(native_source, target_count)
                checked_path = Path(directory) / f"{index}.c"
                checked_path.write_text(checked, encoding="utf-8")
                calls_path = Path(directory) / f"{index}.calls"
                command = _native_command(
                    unit, Path(directory) / f"{index}.o", source=checked_path,
                )
                command[command.index("-c"):command.index("-c")] = [
                    "-fpreprocessed", "-fdump-tree-original=" + str(calls_path),
                ]
                compiled = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, timeout=90, check=False,
                )
                if compiled.returncode:
                    raise CompiledInputError(
                        f"{unit['selected_source']}: native message reference outside the active "
                        f"message set of {target_count} targets or invalid checked input:\n"
                        + compiled.stdout + compiled.stderr
                    )
                _validate_native_calls(
                    calls_path.read_text(encoding="utf-8"), target_count, unit["selected_source"],
                )
            except (
                OSError, UnicodeDecodeError, StructuralCompletionError,
                subprocess.TimeoutExpired,
            ) as error:
                raise CompiledInputError(
                    f"{context_path}: {unit['selected_source']}: native source is invalid: {error}"
                ) from error
