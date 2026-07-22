"""Deterministic, location-tracking JSON loader (stdlib only).

Python's ``json`` module discards source positions once a value is
decoded, which makes it impossible to point diagnostics at a specific
``file:line:column``. This module implements a small, dependency-free JSON
parser whose sole purpose is to preserve:

* insertion order (never re-sorted -- callers control determinism), and
* a :class:`~scripts.generated_data.diagnostics.SourceLocation` for every
  object, array, key, and scalar value.

Duplicate object keys are rejected at parse time with both locations
(first definition and the duplicate) so the diagnostic is actionable.

Only the JSON subset actually used by this project's data files is
supported: objects, arrays, strings, numbers, ``true``/``false``/``null``.
"""

from __future__ import annotations

from .diagnostics import GeneratedDataError, SourceLocation

_WHITESPACE = " \t\n\r"
_NUMBER_CHARS = "+-0123456789.eE"


class JsonNode:
    """A parsed JSON value plus its source location.

    ``value`` holds the "native" Python representation:

    * object  -> ``dict`` mapping ``str`` -> :class:`JsonNode` (ordered)
    * array   -> ``list`` of :class:`JsonNode`
    * scalar  -> ``str`` / ``int`` / ``float`` / ``bool`` / ``None``

    ``key_locations`` (objects only) maps key -> :class:`SourceLocation`
    of that key token, used for duplicate-key / unknown-key diagnostics.
    """

    __slots__ = ("kind", "value", "loc", "key_locations")

    def __init__(self, kind, value, loc, key_locations=None):
        self.kind = kind
        self.value = value
        self.loc = loc
        self.key_locations = key_locations or {}

    def is_object(self):
        return self.kind == "object"

    def is_array(self):
        return self.kind == "array"

    def is_scalar(self):
        return self.kind == "scalar"

    def require(self, key):
        """Return the child :class:`JsonNode` for ``key`` in an object."""
        if not self.is_object():
            raise GeneratedDataError("expected an object, found {}".format(self.kind), self.loc)
        if key not in self.value:
            raise GeneratedDataError("missing required field '{}'".format(key), self.loc)
        return self.value[key]

    def get(self, key, default=None):
        if not self.is_object():
            raise GeneratedDataError("expected an object, found {}".format(self.kind), self.loc)
        return self.value.get(key, default)

    def keys(self):
        if not self.is_object():
            raise GeneratedDataError("expected an object, found {}".format(self.kind), self.loc)
        return list(self.value.keys())

    def items(self):
        if not self.is_object():
            raise GeneratedDataError("expected an object, found {}".format(self.kind), self.loc)
        return list(self.value.items())

    def as_list(self):
        if not self.is_array():
            raise GeneratedDataError("expected an array, found {}".format(self.kind), self.loc)
        return list(self.value)

    def as_str(self):
        if not (self.is_scalar() and isinstance(self.value, str)):
            raise GeneratedDataError("expected a string, found {}".format(self._type_name()), self.loc)
        return self.value

    def as_int(self):
        if not (self.is_scalar() and isinstance(self.value, int) and not isinstance(self.value, bool)):
            raise GeneratedDataError("expected an integer, found {}".format(self._type_name()), self.loc)
        return self.value

    def _type_name(self):
        if self.is_object():
            return "object"
        if self.is_array():
            return "array"
        if self.value is None:
            return "null"
        return type(self.value).__name__

    def native(self):
        """Recursively strip location info, returning plain Python data."""
        if self.is_object():
            return {k: v.native() for k, v in self.value.items()}
        if self.is_array():
            return [v.native() for v in self.value]
        return self.value

    def __repr__(self):
        return "JsonNode({!r}, {!r})".format(self.kind, self.value)


class _Parser:
    def __init__(self, text, path):
        self.text = text
        self.path = path
        self.pos = 0
        self.line = 1
        self.col = 1
        self.length = len(text)

    def loc(self):
        return SourceLocation(self.path, self.line, self.col)

    def error(self, message, loc=None):
        raise GeneratedDataError(message, loc or self.loc())

    def peek(self):
        if self.pos >= self.length:
            return ""
        return self.text[self.pos]

    def advance(self):
        ch = self.text[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def skip_whitespace_and_comments(self):
        while self.pos < self.length:
            ch = self.peek()
            if ch in _WHITESPACE:
                self.advance()
            elif ch == "/" and self.text[self.pos:self.pos + 2] == "//":
                while self.pos < self.length and self.peek() != "\n":
                    self.advance()
            else:
                break

    def parse_document(self):
        self.skip_whitespace_and_comments()
        value = self.parse_value()
        self.skip_whitespace_and_comments()
        if self.pos != self.length:
            self.error("unexpected trailing content after top-level value")
        return value

    def parse_value(self):
        self.skip_whitespace_and_comments()
        ch = self.peek()
        if ch == "{":
            return self.parse_object()
        if ch == "[":
            return self.parse_array()
        if ch == '"':
            return self.parse_string_node()
        if ch == "-" or ch.isdigit():
            return self.parse_number()
        if self.text[self.pos:self.pos + 4] == "true":
            loc = self.loc()
            self.pos += 4
            self.col += 4
            return JsonNode("scalar", True, loc)
        if self.text[self.pos:self.pos + 5] == "false":
            loc = self.loc()
            self.pos += 5
            self.col += 5
            return JsonNode("scalar", False, loc)
        if self.text[self.pos:self.pos + 4] == "null":
            loc = self.loc()
            self.pos += 4
            self.col += 4
            return JsonNode("scalar", None, loc)
        if ch == "":
            self.error("unexpected end of file while parsing a value")
        self.error("unexpected character {!r} while parsing a value".format(ch))

    def parse_object(self):
        start = self.loc()
        self.advance()  # consume '{'
        members = {}
        key_locations = {}
        self.skip_whitespace_and_comments()
        if self.peek() == "}":
            self.advance()
            return JsonNode("object", members, start, key_locations)
        while True:
            self.skip_whitespace_and_comments()
            if self.peek() != '"':
                self.error("expected a quoted object key")
            key_loc = self.loc()
            key = self._parse_raw_string()
            if key in members:
                first_loc = key_locations[key]
                self.error(
                    "duplicate key '{}' (first defined at {})".format(key, first_loc),
                    key_loc,
                )
            self.skip_whitespace_and_comments()
            if self.peek() != ":":
                self.error("expected ':' after object key '{}'".format(key))
            self.advance()
            value = self.parse_value()
            members[key] = value
            key_locations[key] = key_loc
            self.skip_whitespace_and_comments()
            ch = self.peek()
            if ch == ",":
                self.advance()
                continue
            if ch == "}":
                self.advance()
                break
            self.error("expected ',' or '}}' in object, found {!r}".format(ch))
        return JsonNode("object", members, start, key_locations)

    def parse_array(self):
        start = self.loc()
        self.advance()  # consume '['
        items = []
        self.skip_whitespace_and_comments()
        if self.peek() == "]":
            self.advance()
            return JsonNode("array", items, start)
        while True:
            value = self.parse_value()
            items.append(value)
            self.skip_whitespace_and_comments()
            ch = self.peek()
            if ch == ",":
                self.advance()
                continue
            if ch == "]":
                self.advance()
                break
            self.error("expected ',' or ']' in array, found {!r}".format(ch))
        return JsonNode("array", items, start)

    def parse_string_node(self):
        loc = self.loc()
        value = self._parse_raw_string()
        return JsonNode("scalar", value, loc)

    def _parse_raw_string(self):
        assert self.peek() == '"'
        self.advance()
        chars = []
        while True:
            if self.pos >= self.length:
                self.error("unterminated string literal")
            ch = self.advance()
            if ch == '"':
                break
            if ch == "\\":
                if self.pos >= self.length:
                    self.error("unterminated escape sequence")
                esc = self.advance()
                mapping = {
                    '"': '"', "\\": "\\", "/": "/", "b": "\b",
                    "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                }
                if esc in mapping:
                    chars.append(mapping[esc])
                elif esc == "u":
                    hex_digits = ""
                    for _ in range(4):
                        hex_digits += self.advance()
                    chars.append(chr(int(hex_digits, 16)))
                else:
                    self.error("invalid escape sequence '\\{}'".format(esc))
            else:
                chars.append(ch)
        return "".join(chars)

    def parse_number(self):
        loc = self.loc()
        start = self.pos
        if self.peek() == "-":
            self.advance()
        while self.pos < self.length and self.peek() in _NUMBER_CHARS:
            self.advance()
        raw = self.text[start:self.pos]
        try:
            if any(c in raw for c in ".eE"):
                value = float(raw)
            else:
                value = int(raw)
        except ValueError:
            self.error("invalid numeric literal '{}'".format(raw), loc)
        return JsonNode("scalar", value, loc)


def parse_json_text(text, path="<string>"):
    """Parse ``text`` (from ``path``, used only for diagnostics) into a JsonNode tree."""
    return _Parser(text, path).parse_document()


def load_json_file(path):
    """Read and parse a JSON file from disk, returning the root :class:`JsonNode`."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return parse_json_text(text, path=str(path))
