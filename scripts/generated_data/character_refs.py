"""Shared reader for the ``CHARACTER_*`` character-reference namespace
(``include/constants/characters.h``).

Every table that references a character by symbol -- ``characters``
(its own record designator), ``units`` (placed-unit ``charIndex``),
``supports`` (``owner``/partner references), ``eventlists`` (event
script ``character`` arguments), and ``chapterbundle`` (dependency-set
existence checks) -- needs the same underlying namespace, so this
reader lives here once rather than being duplicated per table.

``include/constants/characters.h`` declares the real, primary
character-ID enum anonymously (``enum { CHARACTER_NONE = 0x00, ...
CHARACTER_SNAG = 0xFF };``), then immediately afterwards declares a
wholly separate, *named* sibling enum, ``event_autoload_pid_idx``
(``CHARACTER_EVT_LEADER = 0``, ``CHARACTER_EVT_ACTIVE = -1``,
``CHARACTER_EVT_SLOTB = -2``, ``CHARACTER_EVT_SLOT2 = -3``). Those four
are event-engine "active unit slot" indices -- never valid character
references anywhere in this project's generated data -- but they share
the same ``CHARACTER_`` textual prefix as the real designators (two are
even negative). A plain prefix scan
(``extract_enum_constants(header, name_prefix="CHARACTER_")``) cannot
tell the two enums apart and would wrongly admit all four sentinels as
if they were real character references; :func:`read_character_designators`
instead scopes its regex to the primary block's own text (bounded by
its own opening ``CHARACTER_NONE =`` and closing ``};``), so the
sibling enum's members -- or any other same-prefix collision that might
ever be added elsewhere in the file -- are excluded structurally, not
merely filtered by name.

``CHARACTER_NONE`` itself **is** included in the returned mapping (it is
a genuine member of the primary block, value ``0``) -- callers that need
to reject it as their own field's semantics require (e.g. ``characters``
rejecting it as a record's own designator, since its implied array index
-1 is never a valid table entry) do so themselves with an extra,
field-specific check; this reader only draws the CHARACTER_EVT_*
sibling-enum line, not per-table nullability policy.
"""

from __future__ import annotations

import os
import re

from .diagnostics import GeneratedDataError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHARACTERS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "characters.h")

CHARACTER_NONE_NAME = "CHARACTER_NONE"

_CHARACTER_DESIGNATOR_ENUM_RE = re.compile(r"enum\s*\{\s*(CHARACTER_NONE\s*=.*?)\n\};", re.S)
_CHARACTER_DESIGNATOR_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?0[xX][0-9A-Fa-f]+|-?\d+)\s*,?\s*(//.*)?$"
)


def read_character_designators(header=CHARACTERS_HEADER):
    """Read the ``CHARACTER_*`` constants from the *primary*, anonymous
    character-ID enum in ``include/constants/characters.h`` -- the block
    that starts with ``CHARACTER_NONE = 0x00`` and ends with
    ``CHARACTER_SNAG = 0xFF``. See the module docstring for why this is
    not a plain :func:`~.validators.extract_enum_constants` prefix scan.

    Returns an ordered ``{name: (value, line_number)}`` dict, matching
    :func:`~.validators.extract_enum_constants`'s return shape so
    existing callers (``validate_reference``, per-table designator/number
    resolution, etc.) don't need their own special-casing.
    """
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = _CHARACTER_DESIGNATOR_ENUM_RE.search(text)
    if not match:
        raise GeneratedDataError(
            "could not find the primary CHARACTER_NONE..CHARACTER_SNAG enum block in {}".format(header)
        )
    prefix_line_count = text.count("\n", 0, match.start(1))
    constants = {}
    for line_number, line in enumerate(match.group(1).splitlines(), start=1):
        entry_match = _CHARACTER_DESIGNATOR_ENTRY_RE.match(line)
        if not entry_match:
            continue
        name = entry_match.group(1)
        value_text = entry_match.group(2)
        value = int(value_text, 16) if value_text.lower().startswith(("0x", "-0x")) else int(value_text)
        constants[name] = (value, prefix_line_count + line_number)
    return constants
