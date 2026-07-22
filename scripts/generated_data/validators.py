"""Reusable validation helpers shared across generated-data tables.

Each helper returns/raises :class:`GeneratedDataError` diagnostics carrying
a :class:`SourceLocation` and a ``reference_path`` breadcrumb, so callers
can keep collecting errors with a :class:`DiagnosticCollector` instead of
stopping at the first problem.
"""

from __future__ import annotations

import re

from .diagnostics import GeneratedDataError

_ENUM_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?0[xX][0-9A-Fa-f]+|-?\d+)\s*,?\s*(//.*)?$"
)


def extract_enum_constants(header_path, name_prefix=None):
    """Scan a C header for ``NAME = <value>,`` enum entries.

    Returns an ordered ``dict`` of ``name -> (value, line_number)``. This
    is intentionally simple (regex, not a real C parser) -- sufficient for
    this project's enum-style constant headers, which always assign an
    explicit numeric value per entry.

    ``name_prefix``, if given, filters to symbols starting with it (e.g.
    ``"CHARACTER_"``), letting the same header be reused for multiple
    constant families without cross-contamination.
    """
    constants = {}
    with open(header_path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            match = _ENUM_ENTRY_RE.match(line)
            if not match:
                continue
            name = match.group(1)
            if name_prefix is not None and not name.startswith(name_prefix):
                continue
            value_text = match.group(2)
            value = int(value_text, 16) if value_text.lower().startswith(("0x", "-0x")) else int(value_text)
            constants[name] = (value, line_number)
    return constants


def validate_unique(entries, message_fmt, reference_path_fmt):
    """Detect duplicate keys among ``entries``.

    ``entries`` is an iterable of ``(key, location)`` pairs (in the order
    encountered). Returns a list of :class:`GeneratedDataError`, one per
    duplicate occurrence, referencing the first definition's location.
    """
    errors = []
    seen = {}
    for key, loc in entries:
        if key in seen:
            errors.append(
                GeneratedDataError(
                    message_fmt.format(key=key, first_loc=seen[key]),
                    loc,
                    reference_path_fmt.format(key=key),
                )
            )
        else:
            seen[key] = loc
    return errors


def validate_reference(symbol, allowed, loc, reference_path, kind="symbol"):
    """Check ``symbol`` is a member of the ``allowed`` set/dict.

    Returns a single-element error list if invalid, else ``[]``.
    """
    if symbol not in allowed:
        return [
            GeneratedDataError(
                "undefined {} reference '{}'".format(kind, symbol),
                loc,
                reference_path,
            )
        ]
    return []


def validate_range(value, minimum, maximum, loc, reference_path, field_name="value"):
    """Check ``minimum <= value <= maximum``."""
    if not (minimum <= value <= maximum):
        return [
            GeneratedDataError(
                "{} {} out of range [{}, {}]".format(field_name, value, minimum, maximum),
                loc,
                reference_path,
            )
        ]
    return []


def validate_fixed_capacity(count, capacity, loc, reference_path, what="entries"):
    """Check ``0 <= count <= capacity`` (a fixed-size C array bound)."""
    if not (0 <= count <= capacity):
        return [
            GeneratedDataError(
                "{} count {} exceeds fixed capacity {}".format(what, count, capacity),
                loc,
                reference_path,
            )
        ]
    return []


def validate_parallel_arrays(lengths, loc, reference_path, names):
    """Check that all parallel arrays (e.g. characters[]/expBase[]/...) agree in length."""
    errors = []
    if len(set(lengths)) > 1:
        detail = ", ".join("{}={}".format(name, length) for name, length in zip(names, lengths))
        errors.append(
            GeneratedDataError(
                "parallel arrays have mismatched lengths: {}".format(detail),
                loc,
                reference_path,
            )
        )
    return errors
