"""Source location tracking and actionable diagnostics.

Every error raised by this package should be a :class:`GeneratedDataError`
carrying a :class:`SourceLocation` so failures point straight at
``file:line:column`` instead of a bare Python traceback.
"""

from __future__ import annotations


class SourceLocation:
    """A single point (or the start of a token) in a source file."""

    __slots__ = ("path", "line", "column")

    def __init__(self, path, line, column):
        self.path = path
        self.line = line
        self.column = column

    def __str__(self):
        return "{}:{}:{}".format(self.path, self.line, self.column)

    def __repr__(self):
        return "SourceLocation({!r}, {!r}, {!r})".format(self.path, self.line, self.column)

    def __eq__(self, other):
        if not isinstance(other, SourceLocation):
            return NotImplemented
        return (self.path, self.line, self.column) == (other.path, other.line, other.column)

    def __hash__(self):
        return hash((self.path, self.line, self.column))


class GeneratedDataError(Exception):
    """Raised for any diagnosable failure: parse, validation, or drift.

    ``reference_path`` is a human-readable breadcrumb (e.g.
    ``SupportData_Eirika.characters[2]``) identifying *what* failed, in
    addition to *where* (``location``).
    """

    def __init__(self, message, location=None, reference_path=None):
        self.message = message
        self.location = location
        self.reference_path = reference_path
        super().__init__(self.format())

    def format(self):
        prefix = "{}: ".format(self.location) if self.location is not None else ""
        suffix = " (at {})".format(self.reference_path) if self.reference_path else ""
        return "{}{}{}".format(prefix, self.message, suffix)

    def __str__(self):
        return self.format()


class DiagnosticCollector:
    """Accumulates errors so a full validation pass can report every issue.

    Used by ``validate``/``--check`` so a single invocation reports *all*
    duplicate/invalid records rather than stopping at the first one.
    """

    def __init__(self):
        self.errors = []

    def add(self, error):
        assert isinstance(error, GeneratedDataError)
        self.errors.append(error)

    def extend(self, errors):
        for error in errors:
            self.add(error)

    @property
    def ok(self):
        return not self.errors

    def raise_if_any(self):
        if self.errors:
            raise GeneratedDataValidationError(self.errors)

    def render(self):
        return "\n".join(str(error) for error in self.errors)


class GeneratedDataValidationError(Exception):
    """Wraps 1+ GeneratedDataError diagnostics raised together."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(str(error) for error in self.errors))
