"""Closed ARM header input/profile data; execution still needs an issued job."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
import re

if __package__:
    from .authority import relative_path
    from .budget import MakeProbeError
    from .producer_channel import ChannelError
else:
    from authority import relative_path
    from budget import MakeProbeError
    from producer_channel import ChannelError


PREFIX = "arm-header:"
NEWLIB = "/usr/include/newlib"
TARGET_INCLUDE = "/usr/lib/arm-none-eabi/include"
TARGET_SYS_INCLUDE = "/usr/lib/arm-none-eabi/sys-include"
INCLUDE_ALTERNATIVE = "/etc/alternatives/gcc-arm-none-eabi-include"
ARCH_FLAGS = ("-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork")
FLAGS = frozenset({
    *ARCH_FLAGS, "-std=gnu11", "-fgnu89-inline", "-ffreestanding",
    "-fno-builtin", "-fno-common", "-fno-pic", "-fno-pie",
    "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
    "-fno-function-sections", "-fno-data-sections", "-fno-merge-constants",
    "-fno-merge-all-constants", "-fno-toplevel-reorder",
    "-Wall", "-Wextra", "-Werror=strict-prototypes",
    "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
    "-Og", "-O2", "-g0", "-g3", "-mabi=apcs-gnu", "-mabi=aapcs",
})
ENVIRONMENT_INPUTS = frozenset({
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
    "GCC_EXEC_PREFIX", "COMPILER_PATH", "LIBRARY_PATH", "DEPENDENCIES_OUTPUT",
    "SUNPRO_DEPENDENCIES",
})
FILTER_PREFIX = "s/(^|[[:space:]])("
FILTER_SUFFIX = r")([[:space:]]|$)/\1\3/g"


def environment(values):
    if any(values.get(name) for name in ENVIRONMENT_INPUTS):
        raise MakeProbeError("ARM dependency environment changes its closed search/output profile")
    if values.get("LC_ALL", values.get("LC_CTYPE", values.get("LANG", "C"))) != "C":
        raise MakeProbeError("header pipeline requires its actual C locale")


def options(command, sources, outputs):
    if (
        command.runtime_tool is None or command.native_tool is not None
        or command.argv[0] != command.runtime_tool.path
        or PurePosixPath(command.argv[0]).name not in {"arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"}
        or len(outputs) != 1 or not outputs[0].endswith(".headers.d.tmp")
    ):
        raise MakeProbeError("ARM dependency requires its issued driver and exact header temporary")
    modes, flags, includes, system, binutils = set(), set(), [], [], []
    target = source = None
    arguments = iter(command.argv[1:])
    for argument in arguments:
        if argument in {"-MM", "-MG"}:
            if argument in modes:
                raise MakeProbeError("duplicate ARM dependency mode")
            modes.add(argument)
            continue
        if argument in FLAGS:
            flags.add(argument)
            continue
        if argument.startswith("-B"):
            if argument not in {"-B/bin/", "-B/usr/bin/"} or binutils:
                raise MakeProbeError("ARM dependency has an unsupported binutils search")
            binutils.append(argument)
            continue
        option = next(
            (name for name in ("-isystem", "-iquote", "-MT", "-I", "-D", "-U")
             if argument.startswith(name)), None,
        )
        if option is not None:
            value = argument[len(option):] if argument != option else next(arguments, "")
            if not value or value.startswith(("@", "-")) or any(char in value for char in "\n\r\0"):
                raise MakeProbeError("invalid ARM dependency option value")
            if option in {"-I", "-iquote"}:
                if value.startswith(("=", "$SYSROOT")):
                    raise MakeProbeError("sysroot-special ARM include is unsupported")
                includes.append(value if value == "." else relative_path(value))
            elif option == "-isystem":
                if value != NEWLIB or system:
                    raise MakeProbeError("ARM dependency requires the exact supported C SDK root")
                system.append(value)
            elif option in {"-D", "-U"}:
                name, separator, _ = value.partition("=")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or option == "-U" and separator:
                    raise MakeProbeError("ARM dependency macro is not a symbolic declaration")
            else:
                if target is not None:
                    raise MakeProbeError("duplicate ARM dependency target")
                target = relative_path(value)
            continue
        if argument.startswith(("-", "@")) or source is not None or not argument.endswith(".c"):
            raise MakeProbeError("unsupported ARM dependency flag or source")
        source = relative_path(argument)
    if (
        modes != {"-MM", "-MG"} or not set(ARCH_FLAGS) <= flags or source not in sources
        or target != outputs[0][:-len(".headers.d.tmp")] + ".o"
    ):
        raise MakeProbeError("ARM dependency lost its exact architecture, source or target")
    return tuple(dict.fromkeys(includes)), tuple(system), tuple(binutils)


def filter_expression(value):
    if not isinstance(value, str) or not value.startswith(FILTER_PREFIX) or not value.endswith(FILTER_SUFFIX):
        raise MakeProbeError("header filter differs from its original substitution")
    alternatives = value[len(FILTER_PREFIX):-len(FILTER_SUFFIX)].split("|")
    if not 1 <= len(alternatives) <= 32 or any(
        len(item) > 128 or not re.fullmatch(r"(?:[A-Za-z0-9_-]|\\\.)+\\\.h", item)
        for item in alternatives
    ):
        raise MakeProbeError("header filter requires literal escaped header basenames")
    return value


def _path(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise ChannelError("ARM SDK path is not absolute")
    try:
        relative_path(value[1:])
    except MakeProbeError as error:
        raise ChannelError(str(error)) from error
    return value


def validate_aliases(value):
    if not isinstance(value, list) or len(value) not in {0, 1, 2} or any(
        not isinstance(row, list) or len(row) != 3 or any(not isinstance(item, str) for item in row)
        for row in value
    ):
        raise ChannelError("malformed ARM include alias chain")
    if not value:
        return ()
    if value[0][0] != TARGET_INCLUDE or value[0][2] != NEWLIB:
        raise ChannelError("ARM include alias escaped its exact SDK root")
    if len(value) == 1:
        if value[0][1] not in {NEWLIB, "../../include/newlib"}:
            raise ChannelError("unsupported direct ARM include alias")
    elif value[0][1] != INCLUDE_ALTERNATIVE or value[1] != [INCLUDE_ALTERNATIVE, NEWLIB, NEWLIB]:
        raise ChannelError("unsupported ARM include alternatives chain")
    return tuple(tuple(row) for row in value)


def sdk_roots(frontend, newlib, aliases=()):
    frontend = PurePosixPath(_path(frontend))
    if frontend.name != "cc1" or not re.fullmatch(
        r"/usr/lib/gcc/arm-none-eabi/[A-Za-z0-9_.+-]+", str(frontend.parent),
    ):
        raise ChannelError("ARM SDK lacks its resolved supported cc1 location")
    return tuple(dict.fromkeys((
        str(frontend.parent / "include"), str(frontend.parent / "include-fixed"),
        NEWLIB if aliases else TARGET_INCLUDE, TARGET_SYS_INCLUDE, *((NEWLIB,) if newlib else ()),
    )))


def validate_search(value, executables, *, count_limit, file_limit):
    if (
        not isinstance(value, dict) or set(value) != {"version", "roots", "entries", "excluded", "files", "aliases"}
        or type(value["version"]) is not int or value["version"] != 1
        or any(not isinstance(value[name], list) for name in ("roots", "entries", "excluded", "files"))
        or len(executables) != 2 or PurePosixPath(executables[0]).name not in {
            "arm-none-eabi-gcc", "arm-none-eabi-gcc.exe",
        }
        or not 4 <= len(value["roots"]) <= 5 or not 1 <= len(value["entries"]) <= count_limit
        or len(value["files"]) > count_limit or len(value["excluded"]) > 1
    ):
        raise ChannelError("malformed ARM SDK search profile")
    aliases = validate_aliases(value["aliases"])
    roots = {}
    for row in value["roots"]:
        if not isinstance(row, list) or len(row) != 2 or type(row[1]) is not bool:
            raise ChannelError("malformed ARM SDK root")
        path = _path(row[0])
        if path in roots:
            raise ChannelError("duplicate ARM SDK root")
        roots[path] = row[1]
    if tuple(roots) != sdk_roots(executables[1], NEWLIB in roots, aliases):
        raise ChannelError("ARM SDK roots differ from their real compiler profile")
    entries = {}
    for row in value["entries"]:
        if not isinstance(row, list) or len(row) != 2 or type(row[1]) is not str or row[1] not in {"directory", "file"}:
            raise ChannelError("malformed ARM SDK entry")
        path = _path(row[0])
        if path in entries or not any(
            present and (path == root or path.startswith(root + "/")) for root, present in roots.items()
        ):
            raise ChannelError("duplicate or escaping ARM SDK entry")
        entries[path] = row[1]
    for root, present in roots.items():
        if present and entries.get(root) != "directory" or not present and root in entries:
            raise ChannelError("ARM SDK root presence contradicts its namespace")
    for path in entries:
        if path not in roots and entries.get(str(PurePosixPath(path).parent)) != "directory":
            raise ChannelError("ARM SDK namespace omits an original parent")
    excluded = value["excluded"]
    expected_excluded = [NEWLIB + "/c++"] if entries.get(NEWLIB + "/c++") == "directory" else []
    if excluded != expected_excluded or (
        excluded and any(path.startswith(excluded[0] + "/") for path in entries)
    ):
        raise ChannelError("ARM SDK has an unproven excluded namespace")
    files = {}
    for row in value["files"]:
        if (
            not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str)
            or type(row[1]) is not int or not 0 <= row[1] <= 0o777
            or type(row[2]) is not int or not 0 <= row[2] <= file_limit
            or not isinstance(row[3], str) or not re.fullmatch("[0-9a-f]{64}", row[3])
            or row[0] in files
        ):
            raise ChannelError("malformed or repeated ARM SDK input")
        files[row[0]] = tuple(row)
    if set(files) != {path for path, kind in entries.items() if kind == "file" and path.endswith(".h")}:
        raise ChannelError("ARM SDK input set differs from its captured C headers")
    return roots, entries, files


def records(values, profile, executables, *, count_limit, file_limit):
    _, _, expected = validate_search(profile, executables, count_limit=count_limit, file_limit=file_limit)
    result = {}
    for value in values:
        if not isinstance(value, str):
            raise ChannelError("native ARM header input tag is not text")
        if not value.startswith(PREFIX):
            continue
        try:
            row = json.loads(value[len(PREFIX):])
        except (ValueError, RecursionError) as error:
            raise ChannelError("malformed native ARM header input") from error
        if (
            not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str)
            or type(row[1]) is not int or type(row[2]) is not int
            or not isinstance(row[3], str)
            or expected.get(row[0]) != tuple(row) or row[0] in result
        ):
            raise ChannelError("native ARM header input is foreign or repeated")
        result[row[0]] = tuple(row)
    return tuple(result[path] for path in sorted(result))
