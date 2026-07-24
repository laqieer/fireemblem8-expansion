"""``sWeaponTriangleRules[]`` schema: the 12 directed weapon-triangle
advantage/disadvantage rules consumed by
``BattleApplyWeaponTriangleEffect`` (``src/bmbattle.c``) -- the classic
Sword > Axe > Lance > Sword physical triangle and Anima > Light > Dark >
Anima magic triangle, each direction carrying its own hit/damage bonus
(and the reverse direction its exact negation).

Issue #5 mechanics Batch 3: the last clean vanilla mechanics *table* left
in scope (see docs/generated_data.md's "Remaining Issue #5 scope"
section prior to this batch, which explicitly called out "Weapon
triangle ... none of these are modeled, authored, or linked by any
schema in this repository yet"). ``BattleApplyWeaponTriangleEffect``
itself and ``BattleApplyReaverEffect`` (the reaver-weapon hit/damage
inversion procedural formula that consumes this table's *output*, not
its data) remain untouched, hand-written code -- only the table is
modeled here.

JSON source shape (see ``src/data/weapontriangle.json``)::

    {
      "$schema": "fe8.weapontriangle.v1",
      "rules": [
        { "attacker": "ITYPE_SWORD", "defender": "ITYPE_LANCE", "hitBonus": -15, "atkBonus": -1 },
        { "attacker": "ITYPE_SWORD", "defender": "ITYPE_AXE",   "hitBonus": 15,  "atkBonus": 1 },
        ...
      ]
    }

Each entry in ``rules`` is one directed advantage/disadvantage rule:
``attacker``/``defender`` are ``ITYPE_*`` weapon-type designators
(``include/bmitem.h``), ``hitBonus``/``atkBonus`` are the ``s8`` hit-rate/
attack-power modifiers ``BattleApplyWeaponTriangleEffect`` applies to the
attacker (the defender receives the exact negation of both, applied by
that same function -- not separately authored here). The generated
``sWeaponTriangleRules[]`` C array appends one final, implicit
``{ -1 }`` terminator record after the 12 authored rules -- never
present in the JSON itself (``BattleApplyWeaponTriangleEffect`` scans
the array until it finds ``attackerWeaponType < 0``).

``attacker``/``defender`` are restricted to exactly the 6 weapon types
that actually participate in a weapon triangle -- the 3 physical types
(``ITYPE_SWORD``/``ITYPE_LANCE``/``ITYPE_AXE``) and the 3 magic types
(``ITYPE_ANIMA``/``ITYPE_LIGHT``/``ITYPE_DARK``) -- not every ``ITYPE_*``
constant in ``include/bmitem.h`` (``ITYPE_BOW``/``ITYPE_STAFF``/
``ITYPE_BLLST``/etc. never participate in a weapon triangle and are
rejected, even though they are otherwise valid weapon-type constants).
This is a closed list (like ``movecost``'s ``EXPECTED_PROFILES``), not a
wildcard/prefix scan of the header, deliberately: only these 6 names are
in scope, and each forms one of exactly 2 closed, non-overlapping
3-type groups (the "physical" triangle and the "magic" triangle) --
every rule's ``attacker``/``defender`` pair must belong to the *same*
group (cross-group pairs, e.g. ``ITYPE_SWORD`` vs. ``ITYPE_ANIMA``, are
rejected), and every ordered non-self pair *within* a group must be
authored exactly once: 2 groups x 3 members x 2 directions (each member
beats one groupmate and loses to the other) = exactly 12 rules, matching
``sWeaponTriangleRules``' 12 real records (see the module docstring's
count cross-check against the vanilla ``src/bmbattle.c`` table via the
round-trip parser).

Every rule's reverse direction (``defender``/``attacker`` swapped) must
also be authored, with both bonuses exactly negated -- this mirrors
``BattleApplyWeaponTriangleEffect``'s own logic, which computes the
defender's bonuses as ``-hitBonus``/``-atkBonus`` of whichever rule
matched the attacker, so the *reverse* attacker/defender ordering must
independently author the same relationship explicitly (the vanilla
table itself already does this -- e.g. Sword-vs-Lance is ``-15``/``-1``
and Lance-vs-Sword is ``+15``/``+1``), not derive one from the other at
generation time.
"""

from __future__ import annotations

import os

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import extract_enum_constants, validate_range, validate_unique

SCHEMA_NAME = "weapontriangle"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.weapontriangle.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BMITEM_HEADER = os.path.join(REPO_ROOT, "include", "bmitem.h")

_S8_MIN, _S8_MAX = -128, 127

# The 2 closed, non-overlapping 3-type weapon-triangle groups, in the
# order src/bmbattle.c's hand table declares them (physical, then
# magic) -- see the module docstring for why this is a closed list
# rather than a wildcard scan of every ITYPE_* constant.
GROUPS = (
    ("ITYPE_SWORD", "ITYPE_LANCE", "ITYPE_AXE"),
    ("ITYPE_ANIMA", "ITYPE_LIGHT", "ITYPE_DARK"),
)

# Every weapon type in scope, flattened, for quick membership tests.
IN_SCOPE_TYPES = frozenset(name for group in GROUPS for name in group)

# The exact 12 expected records: every ordered non-self pair within each
# group (2 groups x 3 members x 2 directions).
EXPECTED_RULE_COUNT = sum(len(group) * (len(group) - 1) for group in GROUPS)


def _group_of(itype_name):
    """Return the group tuple ``itype_name`` belongs to, or ``None``."""
    for group in GROUPS:
        if itype_name in group:
            return group
    return None


def read_weapon_type_constants(header=BMITEM_HEADER):
    """Read the full ``ITYPE_*`` enum live from ``include/bmitem.h``."""
    return extract_enum_constants(header, name_prefix="ITYPE_")


class WeaponTriangleRule:
    """One directed weapon-triangle rule (a row of
    ``src/data/weapontriangle.json``'s ``rules`` list)."""

    def __init__(self, attacker, attacker_loc, defender, defender_loc,
                 hit_bonus, hit_bonus_loc, atk_bonus, atk_bonus_loc, loc):
        self.attacker = attacker
        self.attacker_loc = attacker_loc
        self.defender = defender
        self.defender_loc = defender_loc
        self.hit_bonus = hit_bonus
        self.hit_bonus_loc = hit_bonus_loc
        self.atk_bonus = atk_bonus
        self.atk_bonus_loc = atk_bonus_loc
        self.loc = loc

    @property
    def key(self):
        return (self.attacker, self.defender)


def load_records(source_path):
    """Parse the JSON source into a list of :class:`WeaponTriangleRule`.

    Raises :class:`GeneratedDataError` immediately for structural problems
    (wrong top-level shape, missing required fields, wrong types).
    Content-level problems (duplicates, bad references, out-of-range
    values, missing reciprocals) are left to :func:`validate`.
    """
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    rules_node = root.require("rules")
    records = []
    for rule_node in rules_node.as_list():
        attacker_node = rule_node.require("attacker")
        defender_node = rule_node.require("defender")
        hit_bonus_node = rule_node.require("hitBonus")
        atk_bonus_node = rule_node.require("atkBonus")
        records.append(
            WeaponTriangleRule(
                attacker=attacker_node.as_str(), attacker_loc=attacker_node.loc,
                defender=defender_node.as_str(), defender_loc=defender_node.loc,
                hit_bonus=hit_bonus_node.as_int(), hit_bonus_loc=hit_bonus_node.loc,
                atk_bonus=atk_bonus_node.as_int(), atk_bonus_loc=atk_bonus_node.loc,
                loc=rule_node.loc,
            )
        )
    return records


def validate(records, diagnostics, weapon_types_header=BMITEM_HEADER):
    """Run all weapontriangle validations, appending errors to ``diagnostics``.

    Checks: exactly ``EXPECTED_RULE_COUNT`` (12) records, every
    ``attacker``/``defender`` restricted to the 6 in-scope weapon types
    (rejecting both unknown ``ITYPE_*`` names and valid-but-out-of-scope
    ones such as ``ITYPE_BOW``), no self-pairs, every pair belonging to
    the same closed 3-type group (rejecting cross-group pairs), unique
    directed ``(attacker, defender)`` pairs, every rule's exact reverse
    also authored with both bonuses negated, and both bonuses fit ``s8``
    (-128..127).
    """
    weapon_types = read_weapon_type_constants(weapon_types_header)

    diagnostics.extend(
        validate_unique(
            (("{}->{}".format(*r.key), r.loc) for r in records),
            "duplicate weapontriangle rule for '{key}' (first defined at {first_loc})",
            "rules[pair={key}]",
        )
    )

    by_key = {}
    for record in records:
        ref = "rules[attacker={},defender={}]".format(record.attacker, record.defender)

        attacker_known = record.attacker in weapon_types
        defender_known = record.defender in weapon_types

        if not attacker_known:
            diagnostics.add(
                GeneratedDataError(
                    "undefined weapon-type reference '{}'".format(record.attacker),
                    record.attacker_loc, "{}.attacker".format(ref),
                )
            )
        elif record.attacker not in IN_SCOPE_TYPES:
            diagnostics.add(
                GeneratedDataError(
                    "weapon type '{}' does not participate in any weapon triangle -- must be one of {}".format(
                        record.attacker, sorted(IN_SCOPE_TYPES)
                    ),
                    record.attacker_loc, "{}.attacker".format(ref),
                )
            )

        if not defender_known:
            diagnostics.add(
                GeneratedDataError(
                    "undefined weapon-type reference '{}'".format(record.defender),
                    record.defender_loc, "{}.defender".format(ref),
                )
            )
        elif record.defender not in IN_SCOPE_TYPES:
            diagnostics.add(
                GeneratedDataError(
                    "weapon type '{}' does not participate in any weapon triangle -- must be one of {}".format(
                        record.defender, sorted(IN_SCOPE_TYPES)
                    ),
                    record.defender_loc, "{}.defender".format(ref),
                )
            )

        if attacker_known and defender_known and record.attacker in IN_SCOPE_TYPES and record.defender in IN_SCOPE_TYPES:
            if record.attacker == record.defender:
                diagnostics.add(
                    GeneratedDataError(
                        "self-pair rule: attacker and defender are both '{}'".format(record.attacker),
                        record.loc, ref,
                    )
                )
            else:
                attacker_group = _group_of(record.attacker)
                defender_group = _group_of(record.defender)
                if attacker_group != defender_group:
                    diagnostics.add(
                        GeneratedDataError(
                            "cross-group weapon-triangle pair: '{}' and '{}' belong to different weapon-triangle "
                            "groups (physical {} / magic {})".format(
                                record.attacker, record.defender, GROUPS[0], GROUPS[1]
                            ),
                            record.loc, ref,
                        )
                    )

        diagnostics.extend(
            validate_range(record.hit_bonus, _S8_MIN, _S8_MAX, record.hit_bonus_loc,
                            "{}.hitBonus".format(ref), field_name="hitBonus")
        )
        diagnostics.extend(
            validate_range(record.atk_bonus, _S8_MIN, _S8_MAX, record.atk_bonus_loc,
                            "{}.atkBonus".format(ref), field_name="atkBonus")
        )

        by_key.setdefault(record.key, record)

    if len(records) != EXPECTED_RULE_COUNT:
        diagnostics.add(
            GeneratedDataError(
                "expected exactly {} weapontriangle rules, found {}".format(
                    EXPECTED_RULE_COUNT, len(records)
                ),
                getattr(records, "loc", None), "rules",
            )
        )

    # Full reciprocal closure: every (A, B) rule must have a matching
    # (B, A) rule with both bonuses exactly negated.
    for record in records:
        if record.attacker == record.defender:
            continue  # already reported above; skip to avoid noise
        reverse_key = (record.defender, record.attacker)
        ref = "rules[attacker={},defender={}]".format(record.attacker, record.defender)
        reverse = by_key.get(reverse_key)
        if reverse is None:
            diagnostics.add(
                GeneratedDataError(
                    "missing reciprocal rule for ({}, {}) -- every rule's reverse direction must also be "
                    "authored".format(reverse_key[0], reverse_key[1]),
                    record.loc, ref,
                )
            )
            continue
        if reverse.hit_bonus != -record.hit_bonus:
            diagnostics.add(
                GeneratedDataError(
                    "reciprocal hitBonus mismatch: ({}, {}).hitBonus={} but ({}, {}).hitBonus={} (expected {})".format(
                        record.attacker, record.defender, record.hit_bonus,
                        reverse.attacker, reverse.defender, reverse.hit_bonus, -record.hit_bonus,
                    ),
                    reverse.hit_bonus_loc, "rules[attacker={},defender={}].hitBonus".format(
                        reverse.attacker, reverse.defender
                    ),
                )
            )
        if reverse.atk_bonus != -record.atk_bonus:
            diagnostics.add(
                GeneratedDataError(
                    "reciprocal atkBonus mismatch: ({}, {}).atkBonus={} but ({}, {}).atkBonus={} (expected {})".format(
                        record.attacker, record.defender, record.atk_bonus,
                        reverse.attacker, reverse.defender, reverse.atk_bonus, -record.atk_bonus,
                    ),
                    reverse.atk_bonus_loc, "rules[attacker={},defender={}].atkBonus".format(
                        reverse.attacker, reverse.defender
                    ),
                )
            )


class WeaponTriangleTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/weapontriangle.json"
    default_hand_source = "src/bmbattle.c"
    default_output_name = "data_weapontriangle.c"
    default_inventory_path = "reports/generated_data_weapontriangle_inventory.md"

    def dependencies(self):
        return ("bmitem.ITYPE",)

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as weapontriangle_generate
        return weapontriangle_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as weapontriangle_inventory
        return weapontriangle_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as weapontriangle_roundtrip
        hand_records, hand_errors = weapontriangle_roundtrip.parse_hand_written(hand_source)
        if hand_errors:
            return hand_errors
        return weapontriangle_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in WeaponTriangleTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
