"""C89 generation for the Chapter 2 ``EventListScr_Ch2_*``/``Ch2Events``
event-list composition (build/generated/data only -- see
``docs/generated_data.md``: as of Issue #5 Batch 3d this table's generated
output is canonically linked into the ROM, in place of the guarded
``#include "events/ch2-eventinfo.h"`` in ``src/events_info.c``; the hand
header itself is left in place, verbatim, purely as this generator's
round-trip reference).

Renders the exact same macro-call C89 text the hand-written
``src/events/ch2-eventinfo.h`` uses (see ``schema.MACRO_SPECS``) -- no
macro body is expanded or reimplemented, only the call syntax itself.
"""

from __future__ import annotations

from ..cgen import render_banner
from .schema import END_MAIN, HelperCall, NULL_TOKEN, _lower_helper


def render_arg(arg):
    """Render one :class:`~.schema.MacroArg` as C89 source text."""
    if arg.kind == "int":
        return str(arg.value)
    if arg.kind == "symbol":
        return arg.value
    if arg.kind == "null":
        return NULL_TOKEN
    if arg.kind == "call":
        call = arg.value
        inner = ", ".join(render_arg(a) for a in call.args)
        return "{}({})".format(call.macro, inner) if call.args else call.macro
    raise AssertionError("unknown MacroArg kind '{}'".format(arg.kind))


def render_call(call, context="list"):
    """Render one :class:`~.schema.MacroCall` list entry as C89 source text."""
    if isinstance(call, HelperCall):
        call, errors = _lower_helper(call, context)
        if errors:
            raise ValueError(str(errors[0]))
    if not call.args:
        return call.macro
    inner = ", ".join(render_arg(a) for a in call.args)
    return "{}({})".format(call.macro, inner)


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="eventlists")]
    parts.append('#include "global.h"\n')
    parts.append('#include "event.h"\n')
    parts.append('#include "eventinfo.h"\n')
    parts.append('#include "eventcall.h"\n')
    parts.append('#include "EAstdlib.h"\n')
    parts.append('#include "bmtrap.h"\n')
    parts.append('#include "chapterdata.h"\n')
    parts.append('#include "constants/event-flags.h"\n')
    parts.append('#include "constants/characters.h"\n')
    parts.append('#include "constants/songs.h"\n\n')

    if records.helper_scripts:
        for script in records.helper_scripts:
            parts.append("extern EventListScr {}[];\n".format(script.symbol))
        parts.append("\n")

    for lst in records.lists:
        parts.append("CONST_DATA EventListScr {}[] = {{\n".format(lst.symbol))
        for call in lst.entries:
            parts.append("    {}\n".format(render_call(call)))
        parts.append("    {}\n".format(END_MAIN))
        parts.append("};\n\n")

    for script in records.helper_scripts:
        parts.append("CONST_DATA EventListScr {}[] = {{\n".format(script.symbol))
        for call in script.entries:
            parts.append("    {}\n".format(render_call(call, context="script")))
        parts.append("    ENDA\n")
        parts.append("};\n\n")

    tutorial = records.tutorial
    parts.append("CONST_DATA EventListScr * {}[] = {{\n".format(tutorial.symbol))
    for symbol in tutorial.entries:
        parts.append("    {},\n".format(symbol))
    parts.append("    {}\n".format(NULL_TOKEN))
    parts.append("};\n\n")

    manifest = records.manifest
    parts.append("CONST_DATA struct ChapterEventGroup {} = {{\n".format(manifest.symbol))
    for field_name in manifest.fields:
        value = manifest.fields[field_name].value
        parts.append("    .{} = {},\n".format(field_name, value if value is not None else NULL_TOKEN))
    parts.append("};\n")

    return "".join(parts)
