"""Typed C escape-hatch convention.

Some schema fields need to reference existing hand-written C code (a
callback function pointer, a shared constant, etc.) rather than embedding
a value the generator invents. This module implements that as a reusable,
generic mechanism:

* the schema declares the field references a symbol that must be
  *declared* in a specific header (an allowlist by construction: only
  symbols the header's author chose to declare are acceptable);
* the validator rejects malformed identifiers, symbols missing from the
  header, and duplicate/ambiguous matches;
* the generator emits the symbol name **unquoted** (a bare C token, not a
  JSON string) so it compiles as a real reference (e.g. a function
  pointer), not a string literal.

``SupportData`` does not need this (no callback fields), but the
mechanism is exercised end-to-end by
``scripts/generated_data/tests/test_escape_hatch.py`` against header/JSON
fixtures so the later Chapter 2 slice can reuse it for tables that do
(e.g. AI script hooks, event callbacks).
"""

from __future__ import annotations

import re

from .diagnostics import GeneratedDataError

_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Hand-written function declaration/definition, e.g.:
#   void MyCallback(struct Unit* unit);
#   int SomeHelper(int a, int b)
_FUNCTION_DECL_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_ \t\*]*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{)]*\)\s*[;{]",
    re.MULTILINE,
)

# Hand-written extern variable/object declaration, e.g.:
#   extern CONST_DATA struct Foo gBar;
#   extern const u8 gTable[16];
_EXTERN_DECL_RE = re.compile(
    r"^extern\b[^(;{}]*?\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*;",
    re.MULTILINE,
)


class CSymbolRefField:
    """A schema field whose value must be a symbol declared in a C header."""

    def __init__(self, header_path, description="symbol"):
        self.header_path = header_path
        self.description = description

    def declared_symbols(self):
        """Return ``{name: line_number}`` for every declaration found."""
        with open(self.header_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        declared = {}
        for pattern in (_FUNCTION_DECL_RE, _EXTERN_DECL_RE):
            for match in pattern.finditer(text):
                name = match.group(1)
                line_number = text.count("\n", 0, match.start()) + 1
                declared.setdefault(name, line_number)
        return declared

    def validate(self, symbol, loc, reference_path):
        """Validate ``symbol`` (a plain string). Returns a list of errors.

        On success the list is empty and ``symbol`` is safe to emit
        unquoted by the generator.
        """
        errors = []
        if not isinstance(symbol, str) or not _C_IDENTIFIER_RE.match(symbol):
            errors.append(
                GeneratedDataError(
                    "'{}' is not a valid C identifier for a {} reference".format(
                        symbol, self.description
                    ),
                    loc,
                    reference_path,
                )
            )
            return errors
        declared = self.declared_symbols()
        if symbol not in declared:
            errors.append(
                GeneratedDataError(
                    "{} '{}' is not declared in {} (allowed: {})".format(
                        self.description, symbol, self.header_path,
                        ", ".join(sorted(declared)) or "<none found>",
                    ),
                    loc,
                    reference_path,
                )
            )
        return errors

    @staticmethod
    def emit(symbol):
        """Return the C source token for ``symbol`` -- unquoted, as-is."""
        return symbol
