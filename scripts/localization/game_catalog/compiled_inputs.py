"""Typed inputs captured from the selected native Make recipes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from .model import GameCatalogError


class CompiledInputError(GameCatalogError):
    """The native context cannot establish its selected compiler inputs."""


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
    objects = set()
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
        if (name in names or unit["object"] in objects or argv.count("-c") != 1 or len(argv) < 5
                or not argv[0]
                or argv[-2:] != ["-o", unit["object"]]
                or argv.index("-c") >= len(argv) - 2
                or argv[argv.index("-c") + 1] != unit["compiler_input"]):
            raise CompiledInputError(f"{path}: ambiguous selected compilation unit {name}")
        names.add(name)
        objects.add(unit["object"])
        units.append({
            "logical_source": name, "selected_source": unit["selected_source"],
            "compiler_input": unit["compiler_input"], "object": unit["object"],
            "compiler_argv": argv, "preprocessor_argv": unit.get("preprocessor_argv", []),
        })
    return {
        "source_root": context["source_root"], "linker": context["linker"], "units": units,
    }
