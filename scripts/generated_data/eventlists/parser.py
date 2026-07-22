"""Round-trip checker: parse the hand-written Ch2 event-list macro-call
arrays, the ``EventListScr_Ch2_Tutorial`` pointer array, and the
``Ch2Events`` designated-initializer struct out of
``src/events/ch2-eventinfo.h``, and compare them structurally against the
generated model built from ``src/data/ch2_eventlists.json``.

The 7 event-list arrays are NOT plain comma-separated array literals --
each entry is a macro call (``TURN(...)``, ``CHAR(...)``, ...) whose
expansion already supplies its own trailing comma (see ``_EvtParams2``/
``_EvtParams4`` in ``include/eventscript.h``), so there is no top-level
comma between consecutive entries in the hand source. This module
implements a small paren-depth-aware macro-call tokenizer instead of
reusing ``cparse.split_top_level_entries`` (which assumes comma-separated
entries). The tutorial pointer array and the ``Ch2Events`` struct *are*
plain comma-separated/designated-initializer literals, so those reuse
``cparse`` helpers directly.
"""

from __future__ import annotations

import re

from ..cparse import extract_designated_fields, find_matching_brace, line_of
from ..diagnostics import GeneratedDataError, SourceLocation
from .schema import END_MAIN, MANIFEST_FIELD_NAMES, NULL_TOKEN, MacroArg, MacroCall

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_FULL_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
_INT_RE = re.compile(r"^[+-]?(0[xX][0-9a-fA-F]+|\d+)$")
_CALL_RE = re.compile(r"^([A-Za-z_]\w*)\s*\((.*)\)$", re.S)

_LIST_ARRAY_RE = re.compile(r"CONST_DATA\s+EventListScr\s+([A-Za-z_]\w*)\s*\[\s*\]\s*=\s*", re.M)
_TUTORIAL_ARRAY_RE = re.compile(r"CONST_DATA\s+EventListScr\s*\*\s+([A-Za-z_]\w*)\s*\[\s*\]\s*=\s*", re.M)
_MANIFEST_STRUCT_RE = re.compile(r"CONST_DATA\s+struct\s+ChapterEventGroup\s+([A-Za-z_]\w*)\s*=\s*", re.M)


def _parse_int_token(raw):
    if raw.lower().startswith("0x") or raw.lower().startswith("-0x"):
        return int(raw, 16)
    return int(raw, 10)


def _split_top_level_args(text):
    """Split a macro-call argument list on top-level commas, respecting
    ``(...)`` nesting (so a nested call like ``EVFLAG_TMP(9)`` isn't
    split)."""
    args = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[start:i])
            start = i + 1
    tail = text[start:]
    if tail.strip():
        args.append(tail)
    return [a.strip() for a in args]


def _parse_arg_text(text, path, loc):
    text = text.strip()
    if _INT_RE.match(text):
        return MacroArg("int", _parse_int_token(text), loc)
    call_match = _CALL_RE.match(text)
    if call_match:
        macro = call_match.group(1)
        inner_args = [
            _parse_arg_text(a, path, loc) for a in _split_top_level_args(call_match.group(2))
        ]
        return MacroArg("call", MacroCall(macro=macro, macro_loc=loc, args=inner_args, loc=loc), loc)
    if _FULL_IDENT_RE.match(text):
        return MacroArg("symbol", text, loc)
    raise GeneratedDataError("could not parse macro argument '{}' in {}".format(text, path), loc)


def _parse_macro_calls(body, path, body_start_line):
    """Tokenize an ``EventListScr_Ch2_*`` array body into an ordered list
    of :class:`~.schema.MacroCall` objects, stopping at (and not
    including) the bare ``END_MAIN`` terminator. Raises if the body does
    not end with exactly ``END_MAIN`` (plus trailing whitespace)."""
    calls = []
    i = 0
    n = len(body)
    while True:
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            raise GeneratedDataError(
                "event list body in {} ended without an {} terminator".format(path, END_MAIN),
                SourceLocation(path, body_start_line, 1),
            )
        m = _IDENT_RE.match(body, i)
        if not m:
            line = body_start_line + body.count("\n", 0, i)
            raise GeneratedDataError(
                "expected a macro call identifier in {}".format(path), SourceLocation(path, line, 1)
            )
        name = m.group(0)
        name_start = i
        line = body_start_line + body.count("\n", 0, name_start)
        loc = SourceLocation(path, line, 1)
        i = m.end()
        if name == END_MAIN:
            rest = body[i:]
            if rest.strip():
                raise GeneratedDataError(
                    "unexpected content after {} terminator in {}".format(END_MAIN, path), loc
                )
            return calls
        j = i
        while j < n and body[j].isspace():
            j += 1
        if j < n and body[j] == "(":
            depth = 0
            k = j
            while k < n:
                if body[k] == "(":
                    depth += 1
                elif body[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if depth != 0:
                raise GeneratedDataError(
                    "unbalanced parens in macro call '{}' in {}".format(name, path), loc
                )
            arg_text = body[j + 1 : k]
            args = [_parse_arg_text(a, path, loc) for a in _split_top_level_args(arg_text)]
            i = k + 1
        else:
            args = []
        calls.append(MacroCall(macro=name, macro_loc=loc, args=args, loc=loc))


def _find_array_blocks(text, header_re):
    results = []
    for match in header_re.finditer(text):
        name = match.group(1)
        name_index = match.start(1)
        brace_start = text.index("{", match.end())
        brace_end = find_matching_brace(text, brace_start)
        body = text[brace_start + 1 : brace_end]
        results.append((name, body, brace_start, line_of(text, name_index)))
    return results


def parse_hand_written(path, records):
    """Parse ``path`` (``src/events/ch2-eventinfo.h``) and return a dict
    with keys ``"lists_by_symbol"`` (``{symbol: {"symbol", "entries",
    "line"}}``), ``"tutorial"`` (``{"symbol", "entries", "line"}``), and
    ``"manifest"`` (``{"symbol", "fields", "line"}``). ``records`` (the
    generated :class:`~.schema.EventListsRecords`) is accepted for
    interface symmetry with the other tables' ``round_trip_errors`` but is
    not otherwise needed -- this parser scans every matching block in
    ``path`` rather than a caller-supplied symbol subset, since all of
    Ch2's event lists live in this single hand file."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    lists_by_symbol = {}
    for name, body, brace_start, line in _find_array_blocks(text, _LIST_ARRAY_RE):
        body_start_line = line_of(text, brace_start)
        calls = _parse_macro_calls(body, path, body_start_line)
        lists_by_symbol[name] = {"symbol": name, "entries": calls, "line": line}

    tutorial_blocks = _find_array_blocks(text, _TUTORIAL_ARRAY_RE)
    if len(tutorial_blocks) != 1:
        raise GeneratedDataError(
            "expected exactly one EventListScr_Ch2_Tutorial pointer array in {}, found {}".format(
                path, len(tutorial_blocks)
            )
        )
    tut_name, tut_body, _tut_brace_start, tut_line = tutorial_blocks[0]
    tut_entries = [e.strip() for e in tut_body.split(",")]
    tut_entries = [e for e in tut_entries if e]
    if not tut_entries or tut_entries[-1] != NULL_TOKEN:
        raise GeneratedDataError(
            "tutorial array '{}' does not end with the {} terminator in {}".format(
                tut_name, NULL_TOKEN, path
            ),
            SourceLocation(path, tut_line, 1),
        )
    tutorial = {"symbol": tut_name, "entries": tut_entries[:-1], "line": tut_line}

    manifest_matches = list(_MANIFEST_STRUCT_RE.finditer(text))
    if len(manifest_matches) != 1:
        raise GeneratedDataError(
            "expected exactly one Ch2Events struct in {}, found {}".format(path, len(manifest_matches))
        )
    match = manifest_matches[0]
    manifest_symbol = match.group(1)
    manifest_line = line_of(text, match.start(1))
    brace_start = text.index("{", match.end())
    brace_end = find_matching_brace(text, brace_start)
    struct_body = text[brace_start + 1 : brace_end]
    manifest_fields_raw = extract_designated_fields(struct_body)
    manifest_fields = {}
    for key, raw in manifest_fields_raw.items():
        value = raw.rstrip(",").strip()
        manifest_fields[key] = None if value == NULL_TOKEN else value

    return {
        "lists_by_symbol": lists_by_symbol,
        "tutorial": tutorial,
        "manifest": {"symbol": manifest_symbol, "fields": manifest_fields, "line": manifest_line},
    }


def compare_records(generated_records, hand_data, hand_path):
    """Compare ``generated_records`` (:class:`~.schema.EventListsRecords`)
    against ``hand_data`` (from :func:`parse_hand_written`). Returns a
    list of :class:`GeneratedDataError`."""
    errors = []

    gen_by_symbol = {lst.symbol: lst for lst in generated_records.lists}
    hand_by_symbol = hand_data["lists_by_symbol"]

    for symbol in sorted(set(gen_by_symbol) - set(hand_by_symbol)):
        lst = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated event list '{}' has no counterpart in {}".format(symbol, hand_path), lst.loc
            )
        )
    for symbol in sorted(set(hand_by_symbol) - set(gen_by_symbol)):
        hand = hand_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written event list '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )
    for symbol in sorted(set(gen_by_symbol) & set(hand_by_symbol)):
        lst = gen_by_symbol[symbol]
        hand = hand_by_symbol[symbol]
        gen_tuple = tuple(call.as_tuple() for call in lst.entries)
        hand_tuple = tuple(call.as_tuple() for call in hand["entries"])
        if gen_tuple != hand_tuple:
            errors.append(
                GeneratedDataError(
                    "event list '{}' entries mismatch: generated={!r} hand-written={!r}".format(
                        symbol, gen_tuple, hand_tuple
                    ),
                    SourceLocation(hand_path, hand["line"], 1),
                    "{}.entries".format(lst.field),
                )
            )

    tutorial = generated_records.tutorial
    hand_tutorial = hand_data["tutorial"]
    if tutorial.symbol != hand_tutorial["symbol"]:
        errors.append(
            GeneratedDataError(
                "tutorial symbol mismatch: generated='{}' hand-written='{}'".format(
                    tutorial.symbol, hand_tutorial["symbol"]
                ),
                SourceLocation(hand_path, hand_tutorial["line"], 1),
            )
        )
    if tutorial.entries != hand_tutorial["entries"]:
        errors.append(
            GeneratedDataError(
                "tutorial entries mismatch: generated={!r} hand-written={!r}".format(
                    tutorial.entries, hand_tutorial["entries"]
                ),
                SourceLocation(hand_path, hand_tutorial["line"], 1),
                "tutorial.entries",
            )
        )

    manifest = generated_records.manifest
    hand_manifest = hand_data["manifest"]
    if manifest.symbol != hand_manifest["symbol"]:
        errors.append(
            GeneratedDataError(
                "manifest symbol mismatch: generated='{}' hand-written='{}'".format(
                    manifest.symbol, hand_manifest["symbol"]
                ),
                SourceLocation(hand_path, hand_manifest["line"], 1),
            )
        )
    gen_fields = {name: field.value for name, field in manifest.fields.items()}
    hand_fields = hand_manifest["fields"]
    for field_name in sorted(set(gen_fields) | set(hand_fields) | set(MANIFEST_FIELD_NAMES)):
        gen_value = gen_fields.get(field_name)
        hand_value = hand_fields.get(field_name)
        if gen_value != hand_value:
            errors.append(
                GeneratedDataError(
                    "manifest field '{}' mismatch: generated={!r} hand-written={!r}".format(
                        field_name, gen_value, hand_value
                    ),
                    SourceLocation(hand_path, hand_manifest["line"], 1),
                    "manifest.fields[{}]".format(field_name),
                )
            )

    return errors
