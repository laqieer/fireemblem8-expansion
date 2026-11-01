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
    _MESSAGE_FIELD_RE,
    _iter_named_calls,
    _mask_c_comments_and_strings,
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


def validate_compiled_references(context_path: Path, *, target_count: int) -> None:
    context = load_compile_context(context_path)
    root = Path(context["source_root"])
    with TemporaryDirectory(prefix="selected-text-cpp-") as directory:
        for index, unit in enumerate(context["units"]):
            command = []
            arguments = iter(unit["compiler_argv"])
            for argument in arguments:
                if argument in ("-MF", "-MQ", "-MT"):
                    next(arguments, None)
                elif argument not in ("-MMD", "-MD", "-MP"):
                    command.append("-E" if argument == "-c" else argument)
            output = Path(directory) / f"{index}.i"
            command[-1] = str(output)
            try:
                result = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, timeout=90,
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
                source = _mask_c_comments_and_strings(output.read_text(encoding="utf-8"))
                references = [
                    (kind, operands[0]) for kind, _, _, operands
                    in _iter_named_calls(source, ("GetStringFromIndex",)) if len(operands) == 1
                ]
                references.extend(
                    (match.group(1), match.group(2)) for match in _MESSAGE_FIELD_RE.finditer(source)
                )
            except (OSError, UnicodeDecodeError, StructuralCompletionError) as error:
                raise CompiledInputError(
                    f"{context_path}: {unit['selected_source']}: native source is invalid: {error}"
                ) from error
            for kind, value in references:
                operand = value.strip()
                if not re.fullmatch(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)[uUlL]*", operand):
                    continue
                literal = operand.rstrip("uUlL")
                digits = literal.lstrip("-")
                base = 16 if digits.lower().startswith("0x") else (
                    8 if len(digits) > 1 and digits.startswith("0") else 10
                )
                try:
                    target = int(literal, base)
                except ValueError as error:
                    raise CompiledInputError(
                        f"{unit['selected_source']}: invalid native message literal {operand}"
                    ) from error
                if not 0 <= target < target_count:
                    raise CompiledInputError(
                        f"{unit['selected_source']}: live {kind}({operand}) is outside "
                        f"the active message set of {target_count} targets"
                    )
