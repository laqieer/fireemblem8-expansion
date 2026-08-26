"""Bounded typed helper operations for generated event scripts/lists.

The helpers in this module are intentionally only a catalog of existing
``EAstdlib.h``/``eventscript.h`` macros.  They do not define a second event
language or an alternate dispatcher: validation lowers each helper to one
established macro call before C generation.
"""

from __future__ import annotations

from collections import namedtuple


HelperSpec = namedtuple("HelperSpec", ("macro", "args"))


LIST_HELPERS = {
    "shop": {
        "armory": HelperSpec("Armory", (("list", "shop_symbol"), ("x", "int"), ("y", "int"))),
        "vendor": HelperSpec("Vendor", (("list", "shop_symbol"), ("x", "int"), ("y", "int"))),
        "secret_shop": HelperSpec(
            "SecretShop", (("list", "shop_symbol"), ("x", "int"), ("y", "int"))
        ),
    },
    "flag": {
        "event": HelperSpec(
            "AFEV",
            (("ent_flag", "flag"), ("scr", "event_scr"), ("trigger_flag", "flag")),
        ),
    },
    "turn": {
        "event": HelperSpec(
            "TURN",
            (
                ("ent_flag", "flag"),
                ("scr", "event_scr"),
                ("turn", "int"),
                ("turn_max", "int"),
                ("faction", "faction"),
            ),
        ),
    },
    "escape": {
        "area": HelperSpec(
            "AREA",
            (
                ("ent_flag", "flag"),
                ("scr", "event_scr"),
                ("x1", "int"),
                ("y1", "int"),
                ("x2", "int"),
                ("y2", "int"),
            ),
        ),
    },
}


SCRIPT_HELPERS = {
    "flag": {
        "set": HelperSpec("ENUT", (("flag", "flag"),)),
        "clear": HelperSpec("ENUF", (("flag", "flag"),)),
    },
    "unit": {
        "spawn_ally": HelperSpec(
            "SPAWN_ALLY", (("character", "character"), ("x", "coord"), ("y", "coord"))
        ),
        "spawn_npc": HelperSpec(
            "SPAWN_NPC", (("character", "character"), ("x", "coord"), ("y", "coord"))
        ),
        "spawn_enemy": HelperSpec(
            "SPAWN_ENEMY", (("character", "character"), ("x", "coord"), ("y", "coord"))
        ),
        "load1": HelperSpec("LOAD1", (("restriction", "u32"), ("units", "unit_symbol"))),
        "load2": HelperSpec("LOAD2", (("restriction", "u32"), ("units", "unit_symbol"))),
        "load3": HelperSpec("LOAD3", (("restriction", "u32"), ("units", "unit_symbol"))),
        "load4": HelperSpec("LOAD4", (("restriction", "u32"), ("units", "unit_symbol"))),
    },
    "bgm": {
        "start": HelperSpec("MUSC", (("song", "song"),)),
        "fade_in": HelperSpec("EvtBgmFadeIn", (("song", "song"), ("speed", "speed"))),
        "override": HelperSpec("MUSS", (("song", "song"),)),
        "restore": HelperSpec("MURE", (("speed", "speed"),)),
    },
    "recovery": {
        # SET_HP consumes event slot 1 by design; the helper keeps that
        # engine contract explicit rather than inventing a healing opcode.
        "set_hp": HelperSpec("SET_HP", (("character", "character"),)),
    },
    "escape": {
        "warp_out": HelperSpec("WARP_OUT", (("x", "coord"), ("y", "coord"))),
    },
    "strategy": {
        "activate": HelperSpec(
            "AUTOPLAY_STRATEGY_ACTIVATE",
            (("strategy", "strategy_id"), ("flag", "flag")),
        ),
    },
}


def get_spec(context, family, operation):
    """Return a stable :class:`HelperSpec`, or ``None`` if unsupported."""
    table = LIST_HELPERS if context == "list" else SCRIPT_HELPERS
    return table.get(family, {}).get(operation)


def supported_operations(context):
    """Return a deterministic ``family -> operations`` description."""
    table = LIST_HELPERS if context == "list" else SCRIPT_HELPERS
    return {
        family: tuple(sorted(operations))
        for family, operations in sorted(table.items())
    }
