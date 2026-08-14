#!/usr/bin/env python3
"""Read-only-publishing workflow permission/safety checker (issue #9).

A small, targeted structural checker (deliberately not a general YAML
parser -- stdlib-only, no PyYAML dependency) for the repository's
release-rehearsal and Full Matrix workflow contracts: exactly one
top-level `on` mapping, read-only permissions, decoded checkout credential
hygiene, exact target-SHA binding with no env/shell override, pinned external
actions, no secrets/network/publish mutation, the constrained successful-
Build-CI-on-master `workflow_run` path, and canonical executable commands in
named Full Matrix evidence/gate steps. Both contracts reject workflow/job
``defaults.run.shell`` and required-step ``shell`` overrides, retaining
GitHub's standard bash execution. The Full Matrix contract also binds each
lane's actual checkout to the dispatched SHA, requires an immediately
following executable SHA check, rejects conditional/continue-on-error false
greens, and validates the summary's real needs-result failure path.

Conservative and fail-closed by construction: every check here is a
structured/line-aware substring or regex match, never a full YAML/shell
parse, so an ambiguous or unusual construct this module cannot fully
understand is far more likely to trip a rule (a false positive an author
must then justify/rephrase) than to silently slip through (a false
negative). See docs/release_process.md.

Exit codes (CLI): 0 clean, 1 violation(s) found, 2 invocation/I/O error.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

ALLOWED_TRIGGERS = {"pull_request", "workflow_dispatch", "workflow_run"}
CANONICAL_WORKFLOW_RUN_TRIGGER = """\
  workflow_run:
    workflows: [ "Build CI" ]
    types: [ completed ]
    branches: [ "master" ]"""
WORKFLOW_RUN_JOB_CONDITION_EXPRESSION = (
    "${{ github.event_name != 'workflow_run' || "
    "github.event.workflow_run.conclusion == 'success' }}"
)
WORKFLOW_RUN_JOB_CONDITION = f"    if: {WORKFLOW_RUN_JOB_CONDITION_EXPRESSION}"
WORKFLOW_RUN_SHA_EXPRESSION = (
    "${{ github.event_name == 'workflow_run' && "
    "github.event.workflow_run.head_sha || github.sha }}"
)
WORKFLOW_RUN_TARGET_BINDING = f"      RELEASE_TARGET_SHA: {WORKFLOW_RUN_SHA_EXPRESSION}"
WORKFLOW_RUN_CHECKOUT_REF = f"          ref: {WORKFLOW_RUN_SHA_EXPRESSION}"
# issue #9 mandatory correction #1: there is no mutable-ref allowance any
# more (no version tag, branch, or short SHA of any external action is
# ever accepted -- see `check_uses_pins` below). `FULL_SHA_RE` is the one
# and only accepted shape for an external `uses:` reference's pin.
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

WORKFLOW_CONTRACT_RELEASE_REHEARSAL = "release-rehearsal"
WORKFLOW_CONTRACT_FULL_MATRIX = "full-matrix"
WORKFLOW_CONTRACT_CHOICES = (
    WORKFLOW_CONTRACT_RELEASE_REHEARSAL,
    WORKFLOW_CONTRACT_FULL_MATRIX,
)

RELEASE_ELIGIBILITY_TARGETS = frozenset(
    (
        "release-check",
        "release-rehearse",
        "release-check-require-eligible",
        "release-rehearse-require-eligible",
        "release-check-expect-blocked",
        "release-rehearse-expect-blocked",
    )
)

# Forbidden regardless of case/whitespace (each compiled with re.IGNORECASE
# below); every pattern here is a *substring or simple regex*, deliberately
# conservative rather than a full shell-semantics parse -- see module
# docstring. Line-continuation backslashes are collapsed before matching
# (see `_normalize_for_scanning`) so a naive "split the dangerous command
# across two lines" evasion does not work.
FORBIDDEN_PATTERNS = (
    (r"upload-artifact", "artifact upload action"),
    (r"actions/create-release", "release-creation action"),
    (r"softprops/action-gh-release", "release-creation action"),
    (r"\bgh\s+release\b", "'gh release' (mutating GitHub CLI subcommand)"),
    (r"gh\s+api\b[^\n]*(-x|--method)\s+(post|put|patch|delete)", "mutating 'gh api' call"),
    (r"\bgit\s+tag\b", "'git tag' (ref mutation)"),
    (r"\bgit\s+push\b", "'git push' (ref mutation)"),
    (r"environment\s*:", "GitHub Actions (protected-)environment usage"),
    (r"secrets\.", "'secrets.*' interpolation"),
    (r"github\.token\b", "'github.token' credential interpolation"),
    (r"\bgithub_token\b", "GITHUB_TOKEN credential reference"),
    (r"\bgh_token\b", "GH_TOKEN credential reference"),
    (r"\bcurl\b", "'curl' (network command)"),
    (r"\bwget\b", "'wget' (network command)"),
    # Bare invocation (`nc host port`, no flag at all) is exactly as
    # dangerous as a flagged one (`nc -e /bin/sh host port`) -- a fresh,
    # independent verifier reproduced the previous `nc\s+-`-only pattern
    # missing it. `\bnc\b` alone (any standalone "nc" token, flagged or
    # not) closes that gap; the `\b` word-boundary already prevents this
    # from matching as a mere substring inside an unrelated identifier
    # (e.g. "sync", "func", "async", "runc" all keep "nc" glued to a
    # preceding word character, so no boundary ever forms there).
    (r"\bnc\b", "'nc' (network command, bare or flagged invocation)"),
    (r"\bncat\b", "'ncat' (network command)"),
    (r"base64\s+(-d|--decode)", "base64 decode (common obfuscation/indirection pattern)"),
    (r"\bsh\s+-c\b", "'sh -c' (shell indirection)"),
    (r"\bbash\s+-c\b", "'bash -c' (shell indirection)"),
    (r"\beval\b", "'eval' (shell/command indirection)"),
    # Package/registry publish + registry-credential commands (issue #9
    # residual hardening): a fresh, independent verifier reproduced these
    # as unrejected -- symmetrical with the existing `gh release`/`git
    # push`/`git tag` ref-mutation and release-action heuristics above.
    (r"\bnpm\s+publish\b", "'npm publish' (package registry publish command)"),
    (r"\byarn\s+publish\b", "'yarn publish' (package registry publish command)"),
    (r"\bpnpm\s+publish\b", "'pnpm publish' (package registry publish command)"),
    (r"\bdocker(\s+image)?\s+push\b", "'docker push' (container image publish command)"),
    (r"\bdocker\s+login\b", "'docker login' (container registry credential command)"),
    # Shell process substitution (issue #9 final hardening): `<(...)` and
    # `>(...)` are real POSIX/bash command-position constructs -- their
    # body is executed as a command exactly like `$(...)` or a backtick
    # substitution is, so the same variable/fragment-assembly bypass
    # this module already closes for `$(...)`/backticks would apply
    # equally here. This real workflow has no legitimate use for either
    # spelling anywhere (confirmed: neither appears in
    # `.github/workflows/release-rehearsal.yml`), so -- following this
    # module's conservative, fail-closed design and the Musk-algorithm
    # instinct to delete rather than grow speculative complexity -- both
    # are rejected outright wherever they appear, rather than adding a
    # third parallel command-position-tracking implementation for a
    # construct the real workflow never needs.
    (r"<\(", "shell process substitution ('<(...)', unused by this real workflow; rejected fail-closed)"),
    (r">\(", "shell process substitution ('>(...)', unused by this real workflow; rejected fail-closed)"),
    (r"write-all", "GitHub Actions 'write-all' permissions shorthand"),
)
_COMPILED_FORBIDDEN_PATTERNS = [(re.compile(pattern, re.IGNORECASE), label) for pattern, label in FORBIDDEN_PATTERNS]

# `uses:` action-reference heuristics (issue #9 verifier remediation): a
# single generalized substring-in-the-action-name rule instead of an
# ever-growing enumeration of specific action names, so a "disguised"/
# unlisted-but-clearly-named upload/release/publish/deploy action is still
# caught. Deliberately case-insensitive.
_DANGEROUS_ACTION_NAME_SUBSTRINGS = ("upload", "release", "publish", "deploy")


# --- Canonical `uses:` occurrence extraction (fail-closed workflow/
# action-pin parsing hardening) ------------------------------------------
#
# The previous implementation here was a single line-anchored regex
# (`^\s*(?:-\s*)?uses:\s*(\S+)`, one match per *line*, MULTILINE): it only
# ever recognized a bare, unquoted `uses:` key sitting at the very start
# of a line (optionally after a single `-\s*` list-item marker). Two
# independent code-review findings showed that shape covers only the
# "happy path" YAML this repository's own real workflow happens to use,
# and is trivially bypassed by other, equally-valid YAML spellings of
# exactly the same key/value pair:
#
#   * a flow mapping step, e.g. `- {uses: actions/checkout@mutable}` or
#     `- {"uses": "actions/checkout@mutable"}` -- the key never sits at
#     column 0 (or right after a bare `-\s*`), so the old regex's `^`
#     anchor never matched it at all: a mutable ref hidden inside a flow
#     mapping silently passed every check below.
#   * a quoted key in block style, e.g. `"uses": ...` / `'uses': ...` --
#     the old regex only ever matched the bare word `uses`, never a
#     quoted spelling of it.
#   * a quoted *value*, e.g. `uses: "actions/checkout@mutable"` -- the
#     old regex's `(\S+)` captured the value *including* its
#     surrounding quote characters, which then never matched
#     `_USES_REF_SPLIT_RE` (a literal `"` is not part of any real action
#     name), so `check_uses_pins` silently treated it as a "no @ref at
#     all" case instead of validating the real, quoted pin underneath.
#
# This module deliberately still never depends on PyYAML (or any other
# new dependency) and never executes/evaluates any YAML or template --
# see the module docstring. Instead, `extract_uses_occurrences` below is
# a single, well-defined, structural *subset* scanner: a hand-written,
# character-by-character reader that understands exactly the YAML
# constructs a `uses:` key/value pair can legally appear in (block
# mapping, flow mapping, single/double-quoted keys and values, line
# comments, and enough indentation-/bracket-depth-aware nesting to tell
# one mapping scope apart from another for duplicate-key detection) --
# and explicitly, individually flags (rather than silently skipping)
# every construct it does not fully, unambiguously understand: a YAML
# anchor (`&name`) or an explicit tag (`!!str`, `!foo`) attached to the
# value, a YAML alias (`*name`) as the value, a GitHub Actions
# expression/template (`${{ ... }}`) anywhere in the value, an
# unterminated or multi-line quoted scalar, an unquoted `:` followed by
# whitespace inside a plain value (a strong sign of an unintended nested
# mapping key -- YAML's own plain-scalar grammar treats it exactly the
# same way), and a duplicate `uses` key repeated within the same
# enclosing mapping (block or flow). None of these ever "pass" -- every
# one of them produces a `problem`-tagged `UsesOccurrence` that
# `check_uses_pins` (below) always reports as a hard violation,
# regardless of what text happens to follow. This is the single,
# canonical extractor: `workflow_guard.check_uses_pins`,
# `check_checkout_pin`, `check_dangerous_uses_actions`, and
# `scripts/release_rehearsal/action_pins.py` (via
# `extract_uses_occurrences`) all share this one implementation -- there
# is exactly one definition, repository-wide, of what a `uses:`
# reference looks like and how to find every occurrence of one.
#
# Every physical `uses:` occurrence is yielded independently (never
# collapsed/deduplicated by action name) -- callers that need an
# action-name-keyed view (e.g. `action_pins.py`'s inventory cross-check)
# are responsible for grouping this module's occurrence list themselves,
# preserving every individual occurrence (see that module's own
# `workflow_external_occurrences`) -- this scanner itself never discards
# or overwrites one occurrence with another.
@dataclass(frozen=True)
class UsesOccurrence:
    """One `uses:` key/value pair found anywhere in a workflow's raw
    text (block or flow style, quoted or bare key). `line` is the
    1-based source line the *key* starts on. `key_repr` is the exact key
    spelling as found (`"uses"`, `'uses'`, or bare `uses`). `raw_value`
    is the unparsed value text exactly as it appeared (quotes, anchor/
    alias/tag prefix, and all -- for diagnostics). `action_ref` is the
    best-effort *decoded* value (quotes stripped/unescaped for a quoted
    scalar; the raw text, including any anchor/alias/tag prefix, for an
    unsupported shape) -- always a `str` (never `None`; an entirely
    empty `uses:` value decodes to `""`). `problem` is `None` for a
    clean, statically-resolvable value, or one of the short reason codes
    below (`"anchor"`, `"alias"`, `"tag"`, `"template-expression"`,
    `"unterminated-quote"`, `"ambiguous-colon-in-value"`,
    `"duplicate-key"`) otherwise. A caller must always treat any
    non-`None` `problem` as an unconditional, fail-closed rejection --
    never attempt to still parse/trust `action_ref` in that case."""

    line: int
    key_repr: str
    raw_value: str
    action_ref: str
    problem: "str | None"


# Deliberately excludes "#": whether an unquoted "#" terminates a
# plain value as a trailing comment depends on context (it must be
# preceded by whitespace -- see the design-rationale comment on the
# main scan loop's own "#" handling above) rather than being an
# unconditional terminator, so it is handled explicitly inline
# in the plain-value scan below instead of living in this set.
_PLAIN_VALUE_TERMINATORS = frozenset(",}]\r\n")


# --- Canonical YAML quoted-scalar semantic decoder (issue #9 semantic-
# decoding hardening) -----------------------------------------------
#
# A fresh, independent verifier reproduced a real fail-open bypass: this
# decoder previously only ever recognized a small, ad-hoc subset of YAML
# double-quote escapes (`\"`, `\\`, `\n`, `\t`, `\r`, `\0`) and silently
# passed *any other* escaped character straight through unchanged (a
# lossy "just use the raw character" fallback for e.g. `\x`, `\u`, `\U`,
# or any other letter). Two independent, real-world consequences follow
# directly from that: (1) a `uses:`/`permissions:`/`contents:` *key* or
# *value* spelled with a `\uXXXX`/`\xXX`/`\UXXXXXXXX` escape (e.g. the
# key `"u\u0073es"`, which every real YAML parser decodes to exactly the
# same string as the plain key `uses`) was never recognized as
# equivalent to its unescaped spelling at all, so a mutable/dangerous
# `uses:` reference or a `contents: write` grant hidden behind such an
# escape silently evaded every check below; (2) a genuinely malformed,
# truncated, unknown, or out-of-range/surrogate escape was silently
# "decoded" into a plausible-looking (but semantically wrong) literal
# character instead of being rejected, which is exactly backwards for a
# security-relevant, fail-closed parser: an unrecognized construct must
# never be silently accepted.
#
# This is now a complete, stdlib-only (no PyYAML, no `unicode_escape`
# shortcut -- that stdlib codec is a *Python* string-literal decoder, not
# a YAML one: it does not implement YAML's own escape table, silently
# accepts several escapes YAML does not define, and cannot be made to
# reject an invalid/surrogate Unicode scalar value on demand) YAML
# double-quoted escape decoder, exactly matching the YAML 1.2 spec's own
# "Escaped Characters" table (5.7): `\0 \a \b \t \n \v \f \r \e \  \" \/
# \\ \N \_ \L \P`, plus `\xXX` / `\uXXXX` / `\UXXXXXXXX` hex escapes.
# Every one of those -- and *only* those -- decodes; anything else after
# a backslash (an unknown escape letter, a truncated or non-hex `\x`/
# `\u`/`\U` sequence, or a hex escape whose codepoint is a UTF-16
# surrogate (U+D800-U+DFFF, never a valid standalone Unicode scalar
# value) or exceeds U+10FFFF) is rejected fail-closed -- reported back
# to the caller via `problem="invalid-escape"` -- rather than lossily
# decoded or silently passed through. Single-quoted scalars only ever
# support YAML's own `''`-escapes-to-`'` rule (spec 7.3.1); a bare
# backslash inside a single-quoted scalar is always literal (YAML never
# defines an escape there), exactly as before. Neither quote style
# supports a folded/continued value across multiple physical lines --
# an unclosed scalar at end-of-line/end-of-text always fails closed as
# `"unterminated-quote"`, exactly as before this hardening.
_DOUBLE_QUOTE_SIMPLE_ESCAPES = {
    "0": "\x00",
    "a": "\x07",
    "b": "\x08",
    "t": "\x09",
    "n": "\x0a",
    "v": "\x0b",
    "f": "\x0c",
    "r": "\x0d",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\u0085",
    "_": "\u00a0",
    "L": "\u2028",
    "P": "\u2029",
}
_HEX_ESCAPE_LENGTHS = {"x": 2, "u": 4, "U": 8}
_HEX_DIGIT_CHARS = frozenset("0123456789abcdefABCDEF")


def _scan_quoted_scalar(text: str, start: int, quote: str):
    """`start` indexes the opening quote character. Returns `(end,
    content, problem)`: `end` is the index just past the closing quote
    (or, if never properly closed, the index of the first raw `\r`/`\n`
    encountered, or `len(text)` if neither ever appears -- a caller can
    always safely resume scanning at `end`, whether or not this scalar
    was valid); `content` is the fully semantically-decoded scalar for
    a clean, single-line, validly-escaped scalar (double-quote escapes
    resolved per the YAML 1.2 spec's own escape table -- see the
    design-rationale comment above this function; single-quote `''`
    resolved to a literal `'`), or a best-effort (not-fully-trustworthy)
    partial rendering otherwise; `problem` is `None` for a clean,
    fully-understood scalar, `"unterminated-quote"` if a raw newline or
    end-of-text was reached before the scalar was properly closed on
    the same logical line (this scanner deliberately does not support a
    value folded/continued across multiple physical lines), or
    `"invalid-escape"` if the scalar contains a malformed, truncated,
    unknown, or out-of-range/surrogate double-quote escape sequence. A
    caller must always treat any non-`None` `problem` as an
    unconditional, fail-closed rejection -- never attempt to still
    trust `content` in that case."""
    n = len(text)
    i = start + 1
    parts: List[str] = []
    problem = None
    if quote == '"':
        while i < n:
            c = text[i]
            if c == "\\" and i + 1 < n and text[i + 1] not in "\r\n":
                nxt = text[i + 1]
                if nxt in _DOUBLE_QUOTE_SIMPLE_ESCAPES:
                    parts.append(_DOUBLE_QUOTE_SIMPLE_ESCAPES[nxt])
                    i += 2
                    continue
                hex_len = _HEX_ESCAPE_LENGTHS.get(nxt)
                if hex_len is not None:
                    hex_digits = text[i + 2:i + 2 + hex_len]
                    valid = len(hex_digits) == hex_len and all(d in _HEX_DIGIT_CHARS for d in hex_digits)
                    codepoint = int(hex_digits, 16) if valid else -1
                    if valid and (0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF):
                        valid = False
                    if not valid:
                        # Consume only the backslash + the `x`/`u`/`U`
                        # marker itself -- never the (possibly absent,
                        # possibly non-hex, possibly actually-the-
                        # closing-quote) characters that would have
                        # been the hex digits -- so a genuine closing
                        # quote or newline immediately following a
                        # truncated/invalid escape is still correctly
                        # found by the normal per-character scan below,
                        # keeping `end` accurate for skip-over purposes
                        # even when decoding this scalar has failed.
                        problem = "invalid-escape"
                        i += 2
                        continue
                    parts.append(chr(codepoint))
                    i += 2 + hex_len
                    continue
                # An unknown escape letter -- reject fail-closed rather
                # than lossily passing the raw character through (the
                # exact bypass a fresh, independent verifier reproduced
                # against this decoder's previous `escapes.get(nxt,
                # nxt)` fallback).
                problem = "invalid-escape"
                i += 2
                continue
            if c == '"':
                return i + 1, "".join(parts), problem
            if c in "\r\n":
                return i, "".join(parts), problem or "unterminated-quote"
            parts.append(c)
            i += 1
        return i, "".join(parts), problem or "unterminated-quote"
    while i < n:
        c = text[i]
        if c == "'":
            if i + 1 < n and text[i + 1] == "'":
                parts.append("'")
                i += 2
                continue
            return i + 1, "".join(parts), None
        if c in "\r\n":
            return i, "".join(parts), "unterminated-quote"
        parts.append(c)
        i += 1
    return i, "".join(parts), "unterminated-quote"


def extract_uses_occurrences(text: str) -> List[UsesOccurrence]:
    """The single, canonical `uses:` key/value-pair scanner (see the
    design-rationale comment block above this function). Fails closed by
    construction: a `uses:` value shape this scanner does not fully,
    unambiguously understand is always yielded with a non-`None`
    `problem` (never silently skipped/ignored), and a `uses` key
    repeated within the same enclosing mapping (block or flow) is always
    flagged `"duplicate-key"` for every repeat after the first."""
    occurrences: List[UsesOccurrence] = []
    n = len(text)
    i = 0
    line = 1
    # `flow_stack`: one frame per currently-open `{`/`[`; only a `{`
    # (mapping) frame's `seen` set matters for duplicate-key detection --
    # a `[` (sequence) frame carries no key semantics of its own but
    # still needs a stack slot so its matching `]` pops the right frame.
    flow_stack: List[dict] = []
    # `block_stack[0]` is a permanent root/document-level scope (never
    # popped) so even a stray top-level `uses:` (outside any list item)
    # still has a defined enclosing scope to check for a duplicate
    # sibling. Every subsequent frame corresponds to one open block
    # list-item (`- ...`) mapping, keyed by that item's own `-` column
    # (`indent`) so a dedent (a new line whose indentation is at or
    # below an open item's own column) correctly closes it -- and every
    # deeper-nested item -- before any new key on that line is scanned.
    block_stack: List[dict] = [{"indent": -1, "seen": set()}]

    def current_scope() -> dict:
        for frame in reversed(flow_stack):
            if frame["kind"] == "{":
                return frame
        return block_stack[-1]

    at_line_start = True
    while i < n:
        if at_line_start:
            j = i
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] not in "\r\n":
                indent = j - i
                if not flow_stack:
                    while len(block_stack) > 1 and block_stack[-1]["indent"] >= indent:
                        block_stack.pop()
                    if text[j] == "-" and (j + 1 >= n or text[j + 1] in " \t\r\n"):
                        block_stack.append({"indent": indent, "seen": set()})
            at_line_start = False

        ch = text[i]

        if ch == "\n":
            line += 1
            at_line_start = True
            i += 1
            continue
        if ch == "\r":
            i += 1
            continue
        # YAML's comment indicator ("#") only ever starts a comment
        # when it is the first character on the (logical) line or is
        # immediately preceded by whitespace (a space or tab) -- see
        # YAML spec 6.2.4 / 7.3.3 ("Plain scalars must not contain the
        # '#' character preceded by whitespace [that is not itself part
        # of the scalar]"; conversely, a '#' with *no* preceding
        # whitespace is simply an ordinary character embedded in
        # whatever token precedes it, e.g. the plain scalar `setup#`).
        # A fresh, independent final review reproduced a fail-open
        # bypass here: `- {name: setup#, uses: evilcorp/...@main}` has
        # its `#` glued directly onto `setup` with no preceding
        # whitespace, so it is *not* a comment at all -- the previous,
        # unconditional "any '#' starts a comment" rule nonetheless
        # consumed the rest of that physical line (silently discarding
        # the very real `uses:` key that followed), so the dangerous,
        # mutable-ref `uses:` occurrence was never even yielded, let
        # alone rejected. When the preceding-whitespace/start-of-line
        # condition does not hold, '#' is simply an ordinary character:
        # fall through to the normal key/quote/plain-value handling
        # below (which, for a bare '#' matching none of those, just
        # advances one character at the bottom of this loop) instead of
        # ever silently truncating the rest of the line.
        if ch == "#" and (i == 0 or text[i - 1] in " \t\r\n"):
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch in "{[":
            flow_stack.append({"kind": ch, "seen": set()})
            i += 1
            continue
        if ch in "}]":
            if flow_stack:
                flow_stack.pop()
            i += 1
            continue

        # A `uses` key -- quoted (`"uses":`/`'uses':`, decoded, or bare
        # (`uses:`) -- attempted *before* any generic quote handling
        # below, so a quoted key is recognized as a key instead of
        # merely being consumed as an unrelated quoted scalar.
        #
        # issue #9 semantic-decoding hardening: a quoted key is matched
        # against the *semantically decoded* scalar content (`"uses"`),
        # never the raw source spelling -- so an escaped-but-equivalent
        # spelling such as `"u\u0073es"` (which every real YAML parser
        # decodes to the exact same string as the plain key `uses`) is
        # recognized here exactly the same way the plain/literal
        # spelling already was, closing the fail-open bypass a fresh,
        # independent verifier reproduced against the previous
        # literal-4-characters-between-the-quotes shortcut. A bare/plain
        # key never has escape sequences at all (YAML's own plain-
        # scalar grammar has none), so bare-key matching still compares
        # the raw source spelling directly -- there is nothing to
        # decode.
        #
        # issue #9 residual-hardening (combined self-review finding):
        # a quoted key sitting in genuine key position (immediately
        # followed, after optional whitespace, by a single ':') whose
        # escape sequence does *not* fully, unambiguously decode
        # (`key_problem` set) is *never* silently skipped just because
        # this scanner cannot tell whether it spells "uses" -- a
        # previous revision did exactly that (treating an undecodable
        # key exactly like a *confirmed*-not-"uses" key, i.e. simply
        # continuing past it), which let a completely unpinned/mutable
        # `uses:` reference sail through entirely unflagged whenever its
        # key happened to carry one malformed escape byte. This scanner
        # never guesses a decoded meaning it cannot prove, but it also
        # never lets "cannot prove" collapse into "therefore harmless,
        # skip it": an undecodable key-position quoted scalar is always
        # surfaced as its own hard, fail-closed occurrence (`problem`
        # is the same `"invalid-escape"`/etc. reason `_scan_quoted_
        # scalar` reported), so no malformed relevant key ever simply
        # disappears.
        key_repr = None
        key_end = None
        key_undecodable = False
        quote = ch if ch in ('"', "'") else None
        if quote is not None:
            end, decoded_key, key_problem = _scan_quoted_scalar(text, i, quote)
            if key_problem is None and decoded_key == "uses":
                k = end
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == ":" and (k + 1 >= n or text[k + 1] != ":"):
                    key_repr = text[i:end]
                    key_end = k + 1
            elif key_problem is not None:
                k = end
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == ":" and (k + 1 >= n or text[k + 1] != ":"):
                    key_repr = text[i:end]
                    key_end = k + 1
                    key_undecodable = True
            if key_repr is None:
                i = end
                continue
        elif ch == "u" and text[i:i + 4] == "uses":
            word_boundary_before = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
            if word_boundary_before:
                k = i + 4
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == ":" and (k + 1 >= n or text[k + 1] != ":"):
                    key_repr = "uses"
                    key_end = k + 1

        if key_repr is None:
            i += 1
            continue

        k = key_end
        while k < n and text[k] in " \t":
            k += 1

        problem = None
        ws_before_value = k > key_end
        raw_start = k
        if k >= n or text[k] in "\r\n":
            value = ""
        elif text[k] == "#" and ws_before_value:
            # Same YAML comment-indicator rule as the main scan loop:
            # only a "#" preceded by whitespace (here, at least one
            # space/tab consumed right after the "uses:" colon) is a
            # comment. A bare "uses:#..." (no separating whitespace at
            # all) falls through to the plain-value branch below
            # instead, where it is treated as literal value content.
            value = ""
        elif text[k] in "&*!":
            prefix = text[k]
            end_of_line = k
            while end_of_line < n and text[end_of_line] not in "\r\n":
                end_of_line += 1
            value = text[k:end_of_line].strip()
            problem = {"&": "anchor", "*": "alias", "!": "tag"}[prefix]
            k = end_of_line
        elif text[k] in ('"', "'"):
            q = text[k]
            end, content, value_problem = _scan_quoted_scalar(text, k, q)
            value = content
            # issue #9 semantic-decoding hardening: `value_problem` is
            # now `"invalid-escape"` (a malformed/truncated/unknown/
            # out-of-range/surrogate double-quote escape -- see
            # `_scan_quoted_scalar`'s own docstring) as well as the
            # existing `"unterminated-quote"` -- both are always a
            # hard, fail-closed rejection: this scanner never lossily
            # decodes (or silently passes through) a quoted `uses:`
            # value it does not fully, unambiguously understand.
            if value_problem is not None:
                problem = value_problem
            elif "${{" in content:
                problem = "template-expression"
            k = end
        else:
            start = k
            ambiguous_colon = False
            while k < n and text[k] not in _PLAIN_VALUE_TERMINATORS:
                # A "#" only terminates a plain value as a trailing
                # comment when it is preceded by whitespace (a space or
                # tab) -- exactly the same YAML comment-indicator rule
                # as the main scan loop above. A final review reproduced
                # a fail-open bypass here: an unquoted plain value such
                # as `setup#` (no whitespace before the "#") previously
                # had this "#" mis-treated as an unconditional
                # terminator by `_PLAIN_VALUE_TERMINATORS`, truncating
                # the value and -- worse, in the main scan loop's own
                # matching bug -- discarding whatever real YAML followed
                # it on the same line (e.g. a sibling `uses:` key).
                # `k > start` guards the very first character of this
                # value: a "#" glued directly onto the preceding ":" (no
                # separating whitespace at all) is never treated as a
                # comment either, and instead becomes literal value
                # content, consistent with the "must be preceded by
                # whitespace" rule (there is no whitespace to precede it
                # here at all).
                if text[k] == "#" and k > start and text[k - 1] in " \t":
                    break
                if text[k] == ":" and (k + 1 >= n or text[k + 1] in " \t\r\n"):
                    ambiguous_colon = True
                    break
                k += 1
            value = text[start:k].strip()
            if ambiguous_colon:
                problem = "ambiguous-colon-in-value"
            elif "${{" in value:
                problem = "template-expression"

        if key_undecodable:
            # This key-position quoted scalar could not be proven to
            # spell anything other than "uses" (nor proven that it
            # does) -- the undecodable-key finding itself always wins
            # over whatever the value side happened to parse as, and
            # this ambiguous occurrence is never folded into the
            # confirmed-"uses" duplicate-key bookkeeping below (it is
            # not confirmed to *be* "uses" -- only that it cannot be
            # ruled out, which is already fully, separately reported).
            problem = key_problem
        else:
            scope = current_scope()
            if "uses" in scope["seen"]:
                problem = problem or "duplicate-key"
            else:
                scope["seen"].add("uses")

        occurrences.append(
            UsesOccurrence(
                line=line,
                key_repr=key_repr,
                raw_value=text[raw_start:k],
                action_ref=value,
                problem=problem,
            )
        )
        i = k

    return occurrences


_PROBLEM_DESCRIPTIONS = {
    "anchor": "a YAML anchor ('&name') attached to the value -- this scanner never resolves anchors",
    "alias": "a YAML alias ('*name') -- this scanner never resolves aliases, so the actual pinned value cannot be statically verified",
    "tag": "an explicit YAML tag (e.g. '!!str'/'!foo') attached to the value",
    "template-expression": "a GitHub Actions expression ('${{ ... }}') or other template/variable substitution -- the actual pinned value is not statically knowable and can change at runtime",
    "unterminated-quote": "an unterminated or multi-line quoted string -- only a single-line quoted scalar is a supported 'uses:' value shape",
    "ambiguous-colon-in-value": "an unquoted ':' followed by whitespace inside the value -- this looks like an unintended nested mapping key, not a real action reference",
    "duplicate-key": "a duplicate 'uses' key repeated within the same enclosing mapping -- a YAML mapping must not repeat a key, and this scanner refuses to guess which repeated value would actually win",
    "invalid-escape": "a malformed, truncated, unknown, or out-of-range/surrogate YAML double-quote escape sequence -- this scanner never lossily decodes or silently passes through an escape it does not fully, unambiguously understand",
}


# --- Shell variable/fragment command assembly (issue #9 residual
# hardening) ------------------------------------------------------------
#
# A fresh, independent verifier reproduced a dangerous command name
# assembled at *runtime* from two or more concatenated shell variable
# expansions in command position -- e.g. `X=cur; Y=l; $X$Y
# https://example.invalid` actually executes `curl ...` even though the
# literal substring "curl" never appears anywhere in the workflow file,
# so no substring/regex rule in `FORBIDDEN_PATTERNS` above can ever
# match it. This is a narrow, deliberately conservative, command-
# position/assignment-aware heuristic -- not a shell parser -- covering
# exactly the high-confidence shapes issue #9 names: `$X$Y`, `${X}${Y}`
# (and any bare/braced mix, 2 or more fragments), concatenated with zero
# intervening whitespace; and a single shell variable invoked directly
# as a command after being locally assigned a literal value earlier in
# the very same script (`CMD=curl` ... `$CMD https://...` -- no
# assembly needed, just one layer of indirection).
#
# This must never flag this real workflow's own safe, ordinary
# `>> "$GITHUB_STEP_SUMMARY"` job-summary redirection, nor ordinary
# non-command data interpolation (e.g. `echo "$A$B"`): both sit *after*
# the actual command name, never *at* command position, which is
# exactly what "command position" (defined below) excludes.
_VAR_REF = r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*"
_STATEMENT_SEP = r"(?:;|&&|\|\||\|)"
# A YAML `run:` scalar's *inline* value start (e.g. `- run: CMD=curl; ...`,
# the whole script on one physical text line) -- the shell script here
# begins right after the `run:` key, not at column 0 of the raw YAML
# text, so a bare `^` alone would miss it. Block-scalar forms
# (`run: |`, `run: >-`, ...) are unaffected: their actual command text
# starts on the *next* line, which the plain `^` alternative already
# covers, and the literal characters right after "run:" there (`|`,
# `>`, a fold/chomp indicator) never satisfy the identifier/`$`
# patterns below anyway, so no separate carve-out is needed for them.
_RUN_SCALAR_PREFIX = r"\brun:[ \t]*"
# The opening of a POSIX command-substitution subshell: either the
# modern `$(` spelling, or the legacy backtick (`` ` ``) spelling --
# both are real POSIX command-substitution syntax, and a fresh,
# independent final review confirmed the previous `$(`-only recognition
# let a variable/fragment-assembled command hide inside a backtick pair
# instead (e.g. a backtick-wrapped `$X$Y https://example.invalid`, or a
# backtick-wrapped `$CMD ...` where `CMD` was locally assigned/exported/
# `read` elsewhere) and go completely unrejected -- exactly the same
# "command name assembled/indirected at runtime, so no literal
# substring ever appears" evasion `$(` closes below, just spelled with
# backticks instead of `$(...)`. This module still never treats an
# *ordinary* backtick pair as dangerous by itself: a literal,
# non-assembled backtick command substitution, and -- critically --
# backticks used only as prose/markdown formatting punctuation (this
# very file's own header comments, and this real workflow's own
# top-of-file comments, both use backtick-wrapped words this way) are
# never flagged, because neither ever sits at a recognized command
# position (start of a `run:` line/scalar, right after a `;`/`&&`/
# `||`/`|` separator, or immediately inside an already-opened `$(`/
# backtick) in the first place -- only a variable/fragment-assembled or
# previously-tracked-variable command actually invoked *there* still
# triggers the same narrow rules as everywhere else.
#
# Issue #9 residual hardening (previous round): a fresh, independent
# verifier reproduced a dangerous command executed *inside* `$( ... )`
# -- `echo $($X$Y https://example.invalid)`, `echo $(${X}${Y} ...)`,
# and a direct `echo $($CMD ...)` where `CMD` was locally assigned
# elsewhere -- as unrejected, since the text immediately following `$(`
# is exactly as much "command position" as the start of a `run:` script
# line or the text right after a `;`/`&&`/`||`/`|` separator, yet none
# of those already-recognized command-position starts include it.
# Adding `$(` (and now the backtick) here is the single, minimal change
# that lets the *existing* concatenated-fragment and locally-assigned-
# single-variable checks below cover a command executed inside either
# subshell spelling for free, with no separate detection logic
# duplicated. This never turns an *ordinary*, safe `$(...)` (e.g. a
# literal `$(date)`, or one whose result is merely assigned/
# interpolated as data) into a violation by itself -- only a variable/
# fragment-assembled command actually invoked at that position still
# triggers the same narrow rules as everywhere else.
_COMMAND_SUBSTITUTION_OPEN = r"(?:\$\(|`)"
# The start of a `run:` script line (any line -- real line-continuations
# are already collapsed by `_normalize_for_scanning` before this ever
# runs), the inline start of a `run:` scalar's value on the same text
# line, immediately after a shell command separator (`;`, `&&`, `||`, or
# a pipe `|`), or immediately inside an opened `$( ... )` command
# substitution *or* an opened legacy backtick command substitution.
# Deliberately line/separator/subshell-aware rather than a full parser
# -- see module docstring.
_COMMAND_POSITION_PREFIX = (
    rf"(?:^|{_STATEMENT_SEP}|{_RUN_SCALAR_PREFIX}|{_COMMAND_SUBSTITUTION_OPEN})[ \t]*"
)
# A statement boundary: another separator, or a genuine end-of-line/
# end-of-text -- `\r` and `\n` are both matched directly (rather than
# relying solely on MULTILINE `$`, which sits *before* a bare `\n` and
# would otherwise miss a CRLF file's trailing `\r`).
_STATEMENT_END = rf"(?:{_STATEMENT_SEP}|[\r\n]|$)"
# A command-position token's trailing boundary: ordinary whitespace/
# end-of-line/end-of-text, a closing `)` -- added for issue #9's `$(...)`
# command-substitution coverage above, so a variable invoked directly as
# the *entire* body of a subshell with no trailing argument (`$($CMD)`,
# no space before the closing paren) still has a real boundary to match
# against -- or a closing backtick, for the same reason applied to the
# legacy backtick command-substitution spelling (a backtick-wrapped
# `$CMD` with no trailing argument and no space before the closing
# backtick).
_BOUNDARY_AFTER = r"(?:[ \t\r\n)`]|$)"

_CONCATENATED_VAR_REFS_RE = re.compile(
    rf"{_COMMAND_POSITION_PREFIX}(?:{_VAR_REF}){{2,}}", re.MULTILINE
)
_COMMAND_POSITION_SINGLE_VAR_RE = re.compile(
    rf"{_COMMAND_POSITION_PREFIX}({_VAR_REF})(?={_BOUNDARY_AFTER})", re.MULTILINE
)
# A "pure" local shell-variable assignment statement: exactly
# `NAME=value` (optionally `export NAME=value` -- issue #9 residual
# hardening: a fresh, independent verifier reproduced `export NAME=...`
# followed by a later direct `$NAME`/`${NAME}` command-position
# invocation as unrejected, since the plain `NAME=value` shape below
# never matched the `export` keyword prefix) occupying an entire command
# position by itself (no attached command afterward on the same
# statement -- so the common, legitimate `FOO=bar some-command args`
# inline-env-var-prefix idiom is deliberately *not* matched/recorded
# here, `export` prefix or not). Never tries to resolve/interpret
# `value` itself (no shell parser); merely recording *that* `NAME` was
# locally assigned anything at all is enough to make a later bare
# `$NAME`/`${NAME}` command-position invocation of it suspicious.
_PURE_ASSIGNMENT_RE = re.compile(
    rf"{_COMMAND_POSITION_PREFIX}(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=[^\s;&|]*[ \t]*(?={_STATEMENT_END})",
    re.MULTILINE,
)
# `read`/`read -r`/`read -r -s ...` (any number of simple single-token
# `-x` flags with no attached argument -- e.g. `-r`, `-s`, `-e`; a real
# shell's `read -p prompt NAME` etc., where a flag itself consumes a
# separate argument token, is out of scope, matching this module's
# narrow, high-confidence design) populating one **or more** shell
# variables from runtime input rather than a literal RHS value --
# issue #9 residual hardening: a fresh, independent verifier reproduced
# `read NAME` followed by a later direct `$NAME`/`${NAME}` command-
# position invocation as unrejected, since `_PURE_ASSIGNMENT_RE` above
# only ever recognizes the `NAME=value` shape, never `read NAME`.
#
# Final-round hardening: a fresh, independent verifier further confirmed
# only the *first* named variable was tracked, so a multi-variable
# `read A B` left `B` (and any further name) completely untracked -- a
# later direct `$B`/`${B}` command-position invocation of it went
# unrejected even though `A` would have been caught. Every
# whitespace-separated identifier-shaped name after `read` and its
# leading flags is now captured in a single group and split out below,
# so `read A B C` tracks `A`, `B`, *and* `C` alike.
_READ_ASSIGNMENT_RE = re.compile(
    rf"{_COMMAND_POSITION_PREFIX}read\b(?:[ \t]+-[A-Za-z]+)*((?:[ \t]+[A-Za-z_][A-Za-z0-9_]*)+)",
    re.MULTILINE,
)
# Splits a `_READ_ASSIGNMENT_RE` match's captured name-list group (e.g.
# `" A B C"`) into its individual identifier-shaped variable names
# (`["A", "B", "C"]`).
_READ_NAME_LIST_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SINGLE_VAR_NAME_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def check_variable_command_assembly(text: str) -> List[str]:
    """Rejects (1) two or more shell variable expansions concatenated
    with zero intervening whitespace in command position (`$X$Y`,
    `${X}${Y}`, and any bare/braced mix), including inside a `$( ... )`
    command substitution or a legacy backtick command substitution
    (e.g. `$($X$Y ...)`, `$(${X}${Y} ...)`, or the same shapes wrapped
    in backticks instead) -- a command name assembled at runtime from
    separately-innocuous fragments -- and (2) a single shell variable,
    previously assigned a value elsewhere in the very same script via a
    plain `NAME=value` assignment, an `export NAME=value` assignment, or
    a `read`/`read -r` statement (every variable name `read` populates
    is tracked, not only the first), later invoked directly in command
    position, including directly inside a `$( ... )` *or* backtick
    command substitution (e.g. `CMD=curl` ... `$CMD https://...`, or
    `... $($CMD ...)`, or the backtick-wrapped equivalent). All of these
    are high-confidence, command-position-aware evasions of every
    literal-command-name check in `FORBIDDEN_PATTERNS` above; none of
    them ever fire against ordinary tail-position interpolation (e.g.
    this repository's own real `>> "$GITHUB_STEP_SUMMARY"`), plain data
    interpolation, or an *ordinary*, non-assembled `$(...)`/backtick
    command substitution (e.g. `$(date)`), since none of those ever sit
    at a recognized command position."""
    violations: List[str] = []
    for match in _CONCATENATED_VAR_REFS_RE.finditer(text):
        shown = re.sub(r"^[ \t;&|(`]+", "", match.group(0))
        violations.append(
            "command position invokes a name assembled by concatenating 2+ shell variable "
            f"expansions with no separator (evades literal-command-name detection): {shown!r}"
        )
    assigned_by_value = {match.group(1) for match in _PURE_ASSIGNMENT_RE.finditer(text)}
    assigned_by_read = {
        name
        for match in _READ_ASSIGNMENT_RE.finditer(text)
        for name in _READ_NAME_LIST_RE.findall(match.group(1))
    }
    assigned_names = assigned_by_value | assigned_by_read
    if assigned_names:
        for match in _COMMAND_POSITION_SINGLE_VAR_RE.finditer(text):
            name_match = _SINGLE_VAR_NAME_RE.fullmatch(match.group(1))
            if not name_match:
                continue
            name = name_match.group(1)
            shown = re.sub(r"^[ \t;&|(`]+", "", match.group(0))
            if name in assigned_by_value:
                violations.append(
                    "command position directly invokes shell variable "
                    f"{name!r}, which was locally assigned a literal value earlier "
                    f"in this same script (evades literal-command-name detection): {shown!r}"
                )
            elif name in assigned_by_read:
                violations.append(
                    "command position directly invokes shell variable "
                    f"{name!r}, which was populated by a 'read' statement earlier in this "
                    f"same script (evades literal-command-name detection): {shown!r}"
                )
    return violations


def _normalize_for_scanning(text: str) -> str:
    """Collapses a POSIX shell line-continuation (a trailing backslash at
    end-of-line) into a single logical line before any pattern match runs,
    exactly like a real shell would when it actually executes a `run:`
    script -- the backslash and the newline are removed entirely (never
    replaced with a space), so a dangerous command split across two YAML
    lines, at *any* point including mid-token (a simple, common evasion
    of a naive single-line substring/regex check), cannot slip past
    `FORBIDDEN_PATTERNS`/checkout-pin checks.

    This also consumes (discards) any leading indentation -- spaces or
    tabs -- at the start of the continuation line itself. That mirrors
    the two layers of real semantics that actually apply to a `run: |`
    step: (1) a YAML block-scalar strips every line's *common* leading
    indentation before the shell ever sees the script text, so an
    equally-indented continuation line (the realistic, common-looking
    shape an author -- or an adversary -- actually writes) reaches the
    shell with *no* leading whitespace of its own, and (2) POSIX shell
    backslash-newline splicing then joins the (now-dedented) lines with
    no separator inserted. Skipping the indentation-consumption step
    would leave the continuation line's raw *YAML source* indentation
    sitting literally inside the joined line -- e.g. `gh rel\\` followed
    by an indented `ease create ...` would normalize to `gh rel
    ease create ...` (still two separate whitespace-separated words, so
    `\\bgh\\s+release\\b` never matches) instead of the actual executed
    `gh release create ...` (a single, dangerous `gh release`
    invocation). Handles CRLF line endings the same way. This is
    intentionally still just conservative text normalization, not a
    shell parser."""
    return re.sub(r"\\[ \t]*\r?\n[ \t]*", "", text)


def _extract_block(text: str, key: str) -> str:
    """Extracts a fixed-indentation top-level `key:` mapping block's raw
    text (from the `key:` line up to, but excluding, the next line at
    indentation 0). Deliberately simple/line-based since this checks a
    single, repository-authored workflow file with a known fixed shape."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == f"{key}:" or line.startswith(f"{key}:"):
            start = index
            break
    if start is None:
        return ""
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() == "":
            block.append(line)
            continue
        if not line.startswith((" ", "\t")):
            break
        block.append(line)
    return "\n".join(block)


@dataclass(frozen=True)
class _MappingEntry:
    key: str
    value: str
    line: int
    indent: int
    text: str


@dataclass(frozen=True)
class _SequenceItem:
    line: int
    text: str
    mapping_text: str


def _strip_yaml_line_comment(value: str) -> str:
    """Strips a plain-scalar trailing YAML comment without treating a
    `#` inside a quoted substring as a comment marker."""
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ('"', "'"):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _parse_mapping_key_line(line: str, indent: int):
    if line[:indent] != " " * indent:
        return None, None, "non-space indentation"
    content = line[indent:]
    if not content or content.startswith("#"):
        return None, None, None

    if content[0] in ('"', "'"):
        end, key, problem = _scan_quoted_scalar(content, 0, content[0])
        if problem is not None:
            return None, None, problem
        cursor = end
        while cursor < len(content) and content[cursor] in " \t":
            cursor += 1
        if cursor >= len(content) or content[cursor] != ":":
            return None, None, "not a mapping key"
        return key, _strip_yaml_line_comment(content[cursor + 1:].strip()), None

    match = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:", content)
    if match is None:
        return None, None, "not a mapping key"
    return match.group(1), _strip_yaml_line_comment(content[match.end():].strip()), None


def _mapping_entries_at_indent(text: str, indent: int):
    """Extracts block-mapping entries at one exact indentation level.

    This is intentionally a strict subset parser for the repository's
    canonical workflow shape. Comments and nested content are retained
    inside the owning entry, but cannot become sibling keys or values."""
    lines = text.splitlines()
    starts = []
    problems = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = len(line) - len(line.lstrip(" "))
        if leading != indent:
            continue
        key, value, problem = _parse_mapping_key_line(line, indent)
        if problem is not None:
            problems.append(f"line {index + 1}: {problem}")
            continue
        if key is not None:
            starts.append((index, key, value))

    entries = []
    for start, key, value in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            leading = len(line) - len(line.lstrip(" "))
            if leading <= indent:
                end = index
                break
        entries.append(
            _MappingEntry(
                key=key,
                value=value,
                line=start + 1,
                indent=indent,
                text="\n".join(lines[start:end]),
            )
        )
    return entries, problems


def _sequence_items_at_indent(text: str, indent: int):
    """Extracts canonical block-sequence items at one indentation level."""
    lines = text.splitlines()
    starts = []
    problems = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = len(line) - len(line.lstrip(" "))
        if leading != indent:
            continue
        content = line[indent:]
        if content == "-" or content.startswith("- "):
            starts.append(index)
        else:
            problems.append(f"line {index + 1}: expected a block sequence item")

    items = []
    for start in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            leading = len(line) - len(line.lstrip(" "))
            if leading <= indent:
                end = index
                break
        item_lines = lines[start:end]
        first_content = item_lines[0][indent + 1:].lstrip()
        mapping_lines = list(item_lines[1:])
        if first_content:
            mapping_lines.insert(0, " " * (indent + 2) + first_content)
        items.append(
            _SequenceItem(
                line=start + 1,
                text="\n".join(item_lines),
                mapping_text="\n".join(mapping_lines),
            )
        )
    return items, problems


def _decode_static_scalar(value: str):
    value = value.strip()
    if not value:
        return "", None
    if value[0] in ('"', "'"):
        end, decoded, problem = _scan_quoted_scalar(value, 0, value[0])
        if problem is not None:
            return decoded, problem
        if value[end:].strip():
            return decoded, "trailing content after quoted scalar"
        return decoded, None
    if value.startswith(("&", "*", "!", "${{")):
        return value, "non-static scalar"
    return value, None


def _workflow_jobs(text: str):
    """Returns every decoded top-level job mapping in source order.

    The workflow contracts below only trust one block-style top-level
    ``jobs`` mapping with unique, block-style job IDs. Duplicate jobs or
    alternate/ambiguous shapes fail closed instead of being merged.
    """
    violations = []
    top_entries, top_problems = _mapping_entries_at_indent(text, 0)
    violations.extend(f"workflow structure {problem}" for problem in top_problems)
    jobs_entries = [entry for entry in top_entries if entry.key == "jobs"]
    if len(jobs_entries) != 1 or jobs_entries[0].value:
        violations.append("workflow must contain exactly one block-style top-level jobs mapping")
        return [], violations

    job_entries, job_problems = _mapping_entries_at_indent(jobs_entries[0].text, 2)
    violations.extend(f"workflow jobs mapping {problem}" for problem in job_problems)
    seen = set()
    for entry in job_entries:
        if entry.value:
            violations.append(f"workflow job {entry.key!r} must use a block mapping")
        if entry.key in seen:
            violations.append(f"workflow jobs mapping repeats job ID {entry.key!r}")
        seen.add(entry.key)
    return job_entries, violations


def _job_steps(job: _MappingEntry):
    """Returns structurally decoded block-style steps for one job."""
    violations = []
    entries, entry_problems = _mapping_entries_at_indent(job.text, 4)
    violations.extend(f"job {job.key!r} mapping {problem}" for problem in entry_problems)
    steps_entries = [entry for entry in entries if entry.key == "steps"]
    if len(steps_entries) != 1 or steps_entries[0].value:
        violations.append(f"job {job.key!r} must contain exactly one block-style steps sequence")
        return [], violations

    items, item_problems = _sequence_items_at_indent(steps_entries[0].text, 6)
    violations.extend(f"job {job.key!r} steps sequence {problem}" for problem in item_problems)
    decoded = []
    for item in items:
        step_entries, step_problems = _mapping_entries_at_indent(item.mapping_text, 8)
        violations.extend(
            f"job {job.key!r} step starting at line {item.line} {problem}"
            for problem in step_problems
        )
        decoded.append((item, step_entries))
    return decoded, violations


def _flow_mapping_has_key(value: str, expected_key: str) -> bool:
    """Finds a decoded key in a flow mapping while skipping quoted values."""
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in ('"', "'"):
            end, _decoded, _problem = _scan_quoted_scalar(value, cursor, char)
            cursor = end
            continue
        if char not in "{,":
            cursor += 1
            continue

        key_start = cursor + 1
        while key_start < len(value) and value[key_start].isspace():
            key_start += 1
        if key_start >= len(value):
            break

        if value[key_start] in ('"', "'"):
            end, key, problem = _scan_quoted_scalar(
                value, key_start, value[key_start]
            )
            if problem is not None:
                cursor = end
                continue
            key_end = end
        else:
            match = re.match(r"[A-Za-z_][A-Za-z0-9_-]*", value[key_start:])
            if match is None:
                cursor = key_start + 1
                continue
            key = match.group(0)
            key_end = key_start + len(key)

        while key_end < len(value) and value[key_end].isspace():
            key_end += 1
        if key_end < len(value) and value[key_end] == ":" and key == expected_key:
            return True
        cursor = key_end + 1
    return False


def _check_defaults_run_shell(
    entries: List[_MappingEntry],
    defaults_indent: int,
    label: str,
) -> List[str]:
    """Rejects ``defaults.run.shell`` at workflow or job scope."""
    violations = []
    for defaults in [entry for entry in entries if entry.key == "defaults"]:
        if defaults.value:
            if _flow_mapping_has_key(defaults.value, "shell"):
                violations.append(
                    f"{label} must not override defaults.run.shell; required workflow "
                    "commands must use GitHub's standard bash"
                )
            continue

        defaults_entries, defaults_problems = _mapping_entries_at_indent(
            defaults.text, defaults_indent + 2
        )
        if defaults_problems and "shell" in defaults.text:
            violations.append(
                f"{label} defaults mapping containing shell is structurally ambiguous "
                "and rejected fail-closed"
            )
        for run_entry in [entry for entry in defaults_entries if entry.key == "run"]:
            if run_entry.value:
                has_shell = _flow_mapping_has_key(run_entry.value, "shell")
            else:
                run_entries, run_problems = _mapping_entries_at_indent(
                    run_entry.text, defaults_indent + 4
                )
                if run_problems and "shell" in run_entry.text:
                    violations.append(
                        f"{label} defaults.run mapping containing shell is structurally "
                        "ambiguous and rejected fail-closed"
                    )
                has_shell = any(entry.key == "shell" for entry in run_entries)
            if has_shell:
                violations.append(
                    f"{label} must not override defaults.run.shell; required workflow "
                    "commands must use GitHub's standard bash"
                )
    return violations


def check_shell_overrides(text: str, contract: str) -> List[str]:
    """Rejects shell replacement at every contract-controlled scope.

    GitHub accepts custom shell templates such as ``true {0}`` and
    ``bash -n {0}``, and alternate interpreters such as ``cmd``/``pwsh``.
    Those values can change a required command from execution into a
    syntax-only check, a no-op, or another interpreter, so actionlint/schema
    validity is not evidence that the guarded command ran. These workflows
    need no explicit shell mapping: GitHub's standard bash is the contract.
    """
    if contract not in WORKFLOW_CONTRACT_CHOICES:
        raise ValueError(f"unknown workflow contract: {contract}")

    text = _normalize_for_scanning(text)
    violations = []
    top_entries, top_problems = _mapping_entries_at_indent(text, 0)
    violations.extend(f"workflow shell contract {problem}" for problem in top_problems)
    violations.extend(
        _check_defaults_run_shell(top_entries, 0, "workflow-level defaults")
    )

    jobs, job_problems = _workflow_jobs(text)
    violations.extend(job_problems)
    for job in jobs:
        job_entries, entry_problems = _mapping_entries_at_indent(job.text, 4)
        violations.extend(
            f"job {job.key!r} shell contract {problem}"
            for problem in entry_problems
        )
        violations.extend(
            _check_defaults_run_shell(
                job_entries,
                4,
                f"job {job.key!r} defaults",
            )
        )

        controls_steps = (
            contract == WORKFLOW_CONTRACT_FULL_MATRIX
            or job.key == "release-rehearsal"
        )
        if not controls_steps:
            continue
        steps, step_problems = _job_steps(job)
        violations.extend(step_problems)
        for item, step_entries in steps:
            shell_entries = [entry for entry in step_entries if entry.key == "shell"]
            if not shell_entries:
                continue
            name = _step_name(step_entries)
            identifier = name if name is not None else f"line {item.line}"
            violations.append(
                f"job {job.key!r} required step {identifier!r} must not declare a "
                "step-level shell override; use GitHub's standard bash"
            )
    return sorted(set(violations))


def _entry_decoded_value(entry: _MappingEntry):
    value, problem = _decode_static_scalar(entry.value)
    return value, problem


def _run_script(entry: _MappingEntry):
    """Decodes one canonical inline or literal-block ``run`` value."""
    value = entry.value.strip()
    if value in ("|", "|-", "|+"):
        lines = entry.text.splitlines()[1:]
        script_lines = []
        for line in lines:
            if not line.strip():
                script_lines.append("")
                continue
            if not line.startswith(" " * (entry.indent + 2)):
                return "", "run block uses unexpected indentation"
            script_lines.append(line[entry.indent + 2:])
        return "\n".join(script_lines), None
    if value.startswith(">"):
        return "", "folded run blocks are not supported by this fail-closed contract"
    decoded, problem = _decode_static_scalar(value)
    if problem is not None:
        return "", f"run value is not a static scalar ({problem})"
    return decoded, None


def _strip_shell_line_comment(line: str) -> str:
    """Strips a real unquoted shell comment, preserving quoted ``#``."""
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote != "'" and char == "\\":
            escaped = True
            continue
        if char in ('"', "'"):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def _executable_run_lines(script: str) -> List[str]:
    """Returns nonblank, non-comment run lines with safe comment stripping."""
    lines = []
    for line in script.splitlines():
        executable = _strip_shell_line_comment(line).strip()
        if executable:
            lines.append(executable)
    return lines


def _split_shell_segments(line: str) -> List[str]:
    """Splits unquoted shell ``;``, ``&&`` and ``||`` command separators."""
    segments = []
    start = 0
    index = 0
    quote = None
    escaped = False
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote != "'" and char == "\\":
            escaped = True
            index += 1
            continue
        if char in ('"', "'"):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            index += 1
            continue
        separator_len = 0
        if quote is None and char == ";":
            separator_len = 1
        elif quote is None and line[index:index + 2] in ("&&", "||"):
            separator_len = 2
        if separator_len:
            segment = line[start:index].strip()
            if segment:
                segments.append(segment)
            index += separator_len
            start = index
            continue
        index += 1
    tail = line[start:].strip()
    if tail:
        segments.append(tail)
    return segments


_SHELL_ASSIGNMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=")


def _release_step_analysis(script: str):
    """Returns ``(targets, overrides, problems)`` for one run script.

    Only a real command-position ``make release-*`` segment counts as an
    invocation. Commented-out text and ``echo "make release-check"`` are
    data, not commands. Any step that actually invokes a release target
    is later rejected if this analysis found a shell assignment,
    ``export``, or ``env`` override for ``RELEASE_TARGET_SHA`` anywhere
    in that same step.
    """
    targets = []
    overrides = []
    problems = []
    for line in _executable_run_lines(script):
        for segment in _split_shell_segments(line):
            # GitHub expressions are resolved before the runner shell
            # executes. Replace each single-line expression with one
            # shell token before shlex parsing so an unquoted
            # ``NAME=${{ github.sha }} make ...`` prefix is analyzed as
            # the real runner sees it, rather than being split on the
            # expression's source-space.
            analysis_segment = re.sub(r"\$\{\{.*?\}\}", "__GITHUB_EXPRESSION__", segment)
            try:
                tokens = shlex.split(analysis_segment, comments=False, posix=True)
            except ValueError as error:
                if any(target in segment for target in RELEASE_ELIGIBILITY_TARGETS):
                    problems.append(f"cannot safely parse release command {segment!r}: {error}")
                continue
            if not tokens:
                continue

            index = 0
            while index < len(tokens):
                match = _SHELL_ASSIGNMENT_RE.match(tokens[index])
                if match is None:
                    break
                if match.group("name") == "RELEASE_TARGET_SHA":
                    overrides.append(f"command-scoped assignment {tokens[index]!r}")
                index += 1

            if index < len(tokens) and tokens[index] == "export":
                for token in tokens[index + 1:]:
                    match = _SHELL_ASSIGNMENT_RE.match(token)
                    if match is not None and match.group("name") == "RELEASE_TARGET_SHA":
                        overrides.append(f"shell export {token!r}")
                continue

            if index < len(tokens) and tokens[index] == "env":
                index += 1
                while index < len(tokens):
                    match = _SHELL_ASSIGNMENT_RE.match(tokens[index])
                    if match is None:
                        break
                    if match.group("name") == "RELEASE_TARGET_SHA":
                        overrides.append(f"env command override {tokens[index]!r}")
                    index += 1

            if index < len(tokens) and tokens[index] == "make":
                for token in tokens[index + 1:]:
                    if token in RELEASE_ELIGIBILITY_TARGETS:
                        targets.append(token)
    return targets, overrides, problems


def _step_name(step_entries: List[_MappingEntry]):
    names = [entry for entry in step_entries if entry.key == "name"]
    if len(names) != 1:
        return None
    name, problem = _entry_decoded_value(names[0])
    return name if problem is None else None


def _sole_release_job(text: str):
    """Returns the sole `release-rehearsal` job mapping, or violations.

    Duplicate top-level `jobs` mappings, duplicate job IDs, and any
    additional job all fail closed because there is no unambiguous job
    whose bindings can be trusted."""
    violations = []
    top_entries, top_problems = _mapping_entries_at_indent(text, 0)
    violations.extend(f"workflow structure {problem}" for problem in top_problems)
    jobs_entries = [entry for entry in top_entries if entry.key == "jobs"]
    if len(jobs_entries) != 1 or jobs_entries[0].value:
        violations.append("workflow_run workflow must contain exactly one block-style top-level jobs mapping")
        return None, violations

    job_entries, job_problems = _mapping_entries_at_indent(jobs_entries[0].text, 2)
    violations.extend(f"workflow_run jobs mapping {problem}" for problem in job_problems)
    if len(job_entries) != 1 or job_entries[0].key != "release-rehearsal" or job_entries[0].value:
        violations.append("workflow_run workflows must contain exactly one job named 'release-rehearsal'")
        return None, violations
    return job_entries[0], violations


def _job_entries(job: _MappingEntry):
    entries, problems = _mapping_entries_at_indent(job.text, 4)
    return entries, [f"release-rehearsal job mapping {problem}" for problem in problems]


def _check_job_target_binding(
    job: _MappingEntry,
    expected_expression: str,
    *,
    label: str = "release-rehearsal",
) -> List[str]:
    violations = []
    entries, problems = _job_entries(job)
    violations.extend(problems)
    env_entries = [entry for entry in entries if entry.key == "env"]
    if len(env_entries) != 1 or env_entries[0].value:
        return violations + [
            f"{label} job must contain exactly one block-style job-level env mapping "
            "for RELEASE_TARGET_SHA"
        ]

    env_values, env_problems = _mapping_entries_at_indent(env_entries[0].text, 6)
    violations.extend(f"{label} job env mapping {problem}" for problem in env_problems)
    target_values = [entry for entry in env_values if entry.key == "RELEASE_TARGET_SHA"]
    if len(target_values) != 1 or target_values[0].value != expected_expression:
        if expected_expression == WORKFLOW_RUN_SHA_EXPRESSION:
            violations.append(
                "workflow_run RELEASE_TARGET_SHA must bind head_sha for workflow_run and "
                "github.sha otherwise through the exact event-aware binding in the actual "
                "release-rehearsal job-level env mapping"
            )
        else:
            violations.append(
                f"{label} job-level env.RELEASE_TARGET_SHA must bind exactly "
                f"{expected_expression!r}"
            )
    return violations


def _check_workflow_run_checkout(job: _MappingEntry) -> List[str]:
    violations = []
    entries, problems = _job_entries(job)
    violations.extend(problems)
    steps_entries = [entry for entry in entries if entry.key == "steps"]
    if len(steps_entries) != 1 or steps_entries[0].value:
        return violations + [
            "workflow_run release-rehearsal job must contain exactly one block-style steps sequence"
        ]

    items, item_problems = _sequence_items_at_indent(steps_entries[0].text, 6)
    violations.extend(f"release-rehearsal steps sequence {problem}" for problem in item_problems)
    checkout_steps = []
    for item in items:
        step_entries, step_problems = _mapping_entries_at_indent(item.mapping_text, 8)
        violations.extend(
            f"release-rehearsal step starting at line {item.line} {problem}"
            for problem in step_problems
        )
        uses_entries = [entry for entry in step_entries if entry.key == "uses"]
        if len(uses_entries) != 1:
            continue
        action_ref, problem = _decode_static_scalar(uses_entries[0].value)
        if problem is not None:
            continue
        if action_ref.lower().startswith("actions/checkout@"):
            checkout_steps.append((item, step_entries))

    if len(checkout_steps) != 1:
        violations.append(
            "workflow_run release-rehearsal job must contain exactly one actions/checkout step "
            "in its actual steps sequence"
        )
        return violations

    _item, checkout_entries = checkout_steps[0]
    with_entries = [entry for entry in checkout_entries if entry.key == "with"]
    if len(with_entries) != 1 or with_entries[0].value:
        violations.append(
            "workflow_run checkout step must contain exactly one block-style with mapping"
        )
        return violations
    with_values, with_problems = _mapping_entries_at_indent(with_entries[0].text, 10)
    violations.extend(f"workflow_run checkout with mapping {problem}" for problem in with_problems)
    ref_values = [entry for entry in with_values if entry.key == "ref"]
    if len(ref_values) != 1 or ref_values[0].value != WORKFLOW_RUN_SHA_EXPRESSION:
        violations.append(
            "workflow_run checkout ref must bind head_sha for workflow_run and github.sha otherwise, "
            "exactly as with.ref on the actual actions/checkout step"
        )
    persist_values = [entry for entry in with_values if entry.key == "persist-credentials"]
    if len(persist_values) != 1 or persist_values[0].value != "false":
        violations.append(
            "workflow_run actual checkout with mapping must contain exactly "
            "'persist-credentials: false'"
        )
    return violations


# --- Decode-aware permission-scope/write detection helpers (issue #9
# semantic-decoding hardening) --------------------------------------
#
# `_ANY_SCOPE_WRITE_RE` below (unchanged) already matches *any*
# identifier-shaped scope key immediately followed by a `write`/
# `write-all` value, key and/or value optionally single/double-quoted --
# but only ever against the *raw source spelling* inside those optional
# quotes. A fresh, independent verifier reproduced the fail-open gap
# that leaves: `"c\u006fntents": "wr\u0069te"` never matches it at all,
# since its raw source spelling is literally the characters
# `c\u006fntents`/`wr\u0069te` (containing a backslash), never the
# literal words `contents`/`write` the regex's own character classes
# require. `_decode_quoted_span` below decodes exactly one already-
# regex-matched quoted span (or returns a bare/plain token unchanged --
# a YAML plain scalar has no escape sequences at all) via the same
# canonical `_scan_quoted_scalar` decoder `extract_uses_occurrences`
# uses, so there is exactly one decoder, repository-wide, for "what a
# quoted YAML scalar actually, semantically says" -- never a second,
# divergent implementation.
def _decode_all_quoted_spans(block_text: str):
    """Walks `block_text` char-by-char, replacing *every* well-formed
    quoted scalar span (single or double) it finds with its
    semantically decoded, unquoted content -- via the same canonical
    `_scan_quoted_scalar` decoder used everywhere else in this module.
    Returns `(decoded_text, problems)`; `problems` is a list of
    `(raw_span, problem)` pairs for every quoted span that failed to
    decode cleanly (left untouched, quotes and all, in `decoded_text` --
    never silently dropped or guessed at).

    Deliberately only ever called against a small, already-isolated
    single mapping's own raw text (the top-level `permissions:` block --
    see `check_top_level_permissions`), **never** the whole workflow
    file: a real workflow's `run:` steps routinely contain ordinary
    shell double-quoted strings (e.g. `echo "hello"`) that are not YAML
    quoted-scalar *nodes* at all (the YAML value there is the *entire*
    plain-scalar shell script; the quote characters inside it are just
    ordinary content) -- blindly decoding every quote character across
    the entire file would misinterpret those as YAML scalars instead.
    Restricting this whole-block substitution to a small, known-shape,
    shell-free mapping block avoids that misinterpretation entirely,
    while `check_no_write_anywhere`'s own narrower, regex-candidate-
    gated approach (`_SCOPE_WRITE_CANDIDATE_RE`) safely covers the same
    escaped-scope-grant detection everywhere else in the file."""
    n = len(block_text)
    i = 0
    out: List[str] = []
    problems: List[tuple] = []
    while i < n:
        ch = block_text[i]
        if ch in ('"', "'"):
            end, content, problem = _scan_quoted_scalar(block_text, i, ch)
            if problem is None:
                out.append(content)
            else:
                problems.append((block_text[i:end], problem))
                out.append(block_text[i:end])
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out), problems


def _decode_quoted_span(raw: str):
    """`raw` is either a bare/plain identifier-shaped token (returned
    unchanged -- a YAML plain scalar key/value never has escape
    sequences to decode) or a complete, well-formed single/double-quoted
    span (source spelling, opening/closing quote characters included).
    Returns `(decoded_or_best_effort, problem)`; `problem` is `None` for
    a bare token or a cleanly, unambiguously decoded quoted span, or the
    `_scan_quoted_scalar` fail-closed reason code (`"unterminated-
    quote"`/`"invalid-escape"`) otherwise -- a caller must always treat
    any non-`None` `problem` as an unconditional, fail-closed rejection,
    never trust the returned content in that case."""
    if raw[:1] in ('"', "'"):
        _end, content, problem = _scan_quoted_scalar(raw, 0, raw[0])
        return content, problem
    return raw, None


# A single well-formed (balanced-quote) double- or single-quoted span,
# used only to *locate* a quoted key/value candidate for the decode-
# aware scope/write check below -- deliberately permissive at the
# regex-matching stage (any escaped character after a backslash in the
# double-quoted alternative, `''` anywhere in the single-quoted one) so
# a genuinely malformed escape sequence is still captured as a candidate
# and handed to the real, strict `_scan_quoted_scalar` decoder for the
# fail-closed verdict, rather than the locating regex itself silently
# failing to match and letting the malformed span slip past unnoticed.
_QUOTED_SPAN_RE = r'"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\''
# A scope-key/value *candidate* pair: either side may be a bare
# identifier-shaped token or one of the quoted spans above. Matched
# across the *entire* text -- exactly the same "scan everywhere, prefer
# a false positive over a silent miss" design `_ANY_SCOPE_WRITE_RE`
# itself already uses (see that regex's own design-rationale comment) --
# not narrowed to any particular block, so an escaped grant nested at
# any depth (top-level `permissions:`, job-level, or inside a flow
# mapping) is found the same way.
_SCOPE_WRITE_CANDIDATE_RE = re.compile(
    rf"(?P<key>{_QUOTED_SPAN_RE}|[a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(?P<value>{_QUOTED_SPAN_RE}|[a-zA-Z][a-zA-Z0-9_-]*)"
)


def _mapping_keys_at_indent(block: str, indent: int):
    pattern = re.compile(
        rf"^ {{{indent}}}(?P<key>{_QUOTED_SPAN_RE}|[a-zA-Z][a-zA-Z0-9_-]*)\s*:",
        re.MULTILINE,
    )
    keys = []
    problems = []
    for match in pattern.finditer(block):
        raw = match.group("key")
        decoded, problem = _decode_quoted_span(raw)
        if problem is not None:
            problems.append((raw, problem))
        else:
            keys.append(decoded)
    return keys, problems


_YAML_11_TRUE_KEY_ALIASES = frozenset(
    ("on", "On", "ON", "true", "True", "TRUE", "yes", "Yes", "YES", "y", "Y")
)


def _top_level_on_entry(text: str):
    """Returns the one unambiguous top-level ``on`` block mapping.

    PyYAML/YAML-1.1-style parsers normalize an unquoted ``on`` key to
    boolean true. Reject every competing true-like spelling as well as
    literal/quoted duplicate ``on`` keys so parser normalization can
    never silently merge or overwrite the trigger block being checked.
    """
    violations = []
    entries, problems = _mapping_entries_at_indent(text, 0)
    violations.extend(f"workflow structure {problem}" for problem in problems)
    on_like = [entry for entry in entries if entry.key in _YAML_11_TRUE_KEY_ALIASES]
    exact = [entry for entry in on_like if entry.key == "on"]
    if len(on_like) != 1 or len(exact) != 1 or exact[0].value:
        violations.append(
            "workflow must contain exactly one top-level block-style YAML 'on' mapping; "
            "duplicate/true-normalized trigger keys are rejected before trigger validation"
        )
        return None, violations
    return exact[0], violations


def check_top_level_on_mapping(text: str) -> List[str]:
    _entry, violations = _top_level_on_entry(text)
    return violations


def check_triggers(text: str) -> List[str]:
    violations = []
    on_entry, structure_violations = _top_level_on_entry(text)
    if on_entry is None:
        return structure_violations
    block = on_entry.text
    trigger_keys, trigger_problems = _mapping_keys_at_indent(block, 2)
    for raw, problem in trigger_problems:
        violations.append(
            f"top-level trigger key {raw!r} is not a supported/decodable YAML scalar "
            f"({_PROBLEM_DESCRIPTIONS.get(problem, problem)})"
        )
    found = set(trigger_keys)
    disallowed = found - ALLOWED_TRIGGERS
    if disallowed:
        violations.append(f"disallowed trigger(s): {sorted(disallowed)} (only {sorted(ALLOWED_TRIGGERS)} allowed)")
    if not found:
        violations.append("'on:' block declares no recognizable trigger keys")
    workflow_run_entries = _extract_trigger_entries(block, "workflow_run")
    if "workflow_run" in found:
        if trigger_keys.count("workflow_run") != 1 or len(workflow_run_entries) != 1:
            violations.append("workflow_run trigger must use exactly one canonical block mapping")
        elif workflow_run_entries[0] != CANONICAL_WORKFLOW_RUN_TRIGGER:
            violations.append(
                "workflow_run trigger must exactly name workflow 'Build CI', type 'completed', "
                "and branch 'master'; broad or differently scoped workflow_run triggers are rejected"
            )
    return violations


def _extract_trigger_entries(on_block: str, trigger: str) -> List[str]:
    """Returns each block-style top-level trigger entry with indentation
    preserved. This intentionally accepts no flow-style or alternate
    spelling for the privileged workflow_run trigger: the caller compares
    the complete entry byte-for-byte with one canonical read-only shape."""
    lines = on_block.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(rf"  {re.escape(trigger)}\s*:", line)
    ]
    entries = []
    for start in starts:
        end = start + 1
        while end < len(lines) and not re.match(r"^  [a-zA-Z_]+:", lines[end]):
            end += 1
        entries.append("\n".join(line.rstrip() for line in lines[start:end]).rstrip())
    return entries


def _has_workflow_run_trigger(text: str) -> bool:
    on_entry, violations = _top_level_on_entry(text)
    if on_entry is None or violations:
        return False
    keys, _problems = _mapping_keys_at_indent(on_entry.text, 2)
    return "workflow_run" in keys


def check_workflow_run_contract(text: str) -> List[str]:
    """The sole permitted workflow_run path is one read-only rehearsal job
    after successful Build CI on master, bound to that run's exact head SHA.
    Pull-request and manual runs retain github.sha through the same explicit
    event-aware expression."""
    if not _has_workflow_run_trigger(text):
        return []

    violations = []
    job, job_violations = _sole_release_job(text)
    violations.extend(job_violations)
    if job is None:
        return violations

    entries, entry_problems = _job_entries(job)
    violations.extend(entry_problems)
    if_entries = [entry for entry in entries if entry.key == "if"]
    if len(if_entries) != 1 or if_entries[0].value != WORKFLOW_RUN_JOB_CONDITION_EXPRESSION:
        violations.append(
            "workflow_run release-rehearsal job-level if must run only when Build CI conclusion "
            "is exactly 'success' while preserving pull_request/workflow_dispatch paths"
        )
    violations.extend(_check_job_target_binding(job, WORKFLOW_RUN_SHA_EXPRESSION))
    violations.extend(_check_workflow_run_checkout(job))
    return violations


def check_top_level_permissions(text: str) -> List[str]:
    violations = []
    lines = text.splitlines()
    top_level_perm_idx = None
    for index, line in enumerate(lines):
        if line.rstrip() == "permissions:":
            top_level_perm_idx = index
            break
        if line.startswith("jobs:"):
            break
    if top_level_perm_idx is None:
        violations.append("no top-level 'permissions:' block found before 'jobs:'")
        return violations
    block = _extract_block(text, "permissions")
    # issue #9 semantic-decoding hardening: the top-level `permissions:`
    # block is a small, already-isolated mapping (never shell/`run:`
    # script content), so it is safe to decode *every* quoted scalar
    # inside it in one pass -- unlike scanning the whole workflow file
    # for quoted spans (which would also misinterpret an unrelated
    # quoted shell string elsewhere -- see `check_no_write_anywhere`'s
    # own narrower, candidate-regex-gated approach for that case). A
    # scalar that fails to decode cleanly is left in its raw/undecoded
    # form in `decoded_block` (so the substring checks below still run
    # against *something*) and is separately, always reported fail-
    # closed here regardless of what the substring checks find.
    decoded_block, block_problems = _decode_all_quoted_spans(block)
    for raw_span, problem in block_problems:
        violations.append(
            f"top-level permissions block contains a quoted scalar {raw_span!r} that is not a "
            f"supported/decodable YAML quoted scalar ({_PROBLEM_DESCRIPTIONS.get(problem, problem)}) "
            "-- rejected fail-closed"
        )
    if "contents: read" not in decoded_block:
        violations.append(f"top-level permissions block does not declare 'contents: read': {block.strip()!r}")
    if re.search(r"\bwrite\b", decoded_block, re.IGNORECASE):
        violations.append(f"top-level permissions block grants a 'write' scope: {block.strip()!r}")
    return violations


# Any GitHub Actions permission *scope* name (`contents`, `id-token`,
# `packages`, `pull-requests`, `issues`, `actions`, `checks`,
# `deployments`, `statuses`, `pages`, `security-events`, `discussions`,
# `attestations`, `models`, and any future scope GitHub ever adds) is a
# lowercase-with-hyphens identifier. Rather than enumerate a fixed,
# ever-growing list of "known" scope names (issue #9 verifier
# remediation: the independent reviewer found the previous check only
# ever matched the literal word `contents`), this matches *any*
# identifier-shaped mapping key -- quoted or not, any case, any amount of
# surrounding whitespace, whether it appears at top level, job level,
# deeply nested, or inside a `{ "flow", "mapping" }` -- immediately
# followed by a `write`/`write-all` value. A single, general rule instead
# of a fixed enumeration is exactly the same "generalized heuristic over
# an ever-growing specific list" design `_DANGEROUS_ACTION_NAME_SUBSTRINGS`
# already uses for `uses:` action names below.
_ANY_SCOPE_WRITE_RE = re.compile(
    r"""['"]?(?P<scope>[a-zA-Z][a-zA-Z0-9_-]*)['"]?\s*:\s*['"]?(?P<value>write(?:-all)?)['"]?\b""",
    re.IGNORECASE,
)


def check_no_write_anywhere(text: str) -> List[str]:
    """Detects **any** `<scope>: write` permission grant -- `contents`,
    `id-token`, `packages`, `pull-requests`, `issues`, `actions`,
    `checks`, `deployments`, `statuses`, or any scope this module's
    authors have never heard of -- job-level or nested, any indentation,
    any amount/kind of whitespace around the colon, any case, optionally
    single/double-quoted (key and/or value), and whether it sits in
    block style or an inline/flow mapping (e.g.
    `permissions: {contents: read, id-token: write}`) -- anywhere outside
    the validated top-level `permissions:` block -- i.e. everywhere,
    since this function scans the *entire* text; `check_top_level_
    permissions` above separately allows (indeed requires) `contents:
    read` there, and never itself grants any scope `write`. Also flags a
    bare `permissions: write-all`/`write` shorthand scalar wherever it
    occurs, and a bare, ambiguous `write`/`write-all` shorthand for any
    other scope-shaped key."""
    violations = []
    for match in _ANY_SCOPE_WRITE_RE.finditer(text):
        scope, value = match.group("scope"), match.group("value")
        # The message always includes the *canonical*, whitespace/quote-
        # normalized "scope: value" rendering (reconstructed from the
        # named groups, e.g. always exactly "contents: write" -- never
        # "contents:      write" or "'contents': write" verbatim) so a
        # consumer/test can match on it regardless of the original
        # formatting quirk that was actually used, plus the raw matched
        # text for full transparency.
        violations.append(
            f"found permission scope grant {scope}: {value} (raw: {match.group(0)!r})"
        )
    for match in re.finditer(r"permissions\s*:\s*['\"]?write(-all)?\b", text, re.IGNORECASE):
        violations.append(f"found a 'permissions: write...' shorthand grant: {match.group(0)!r}")
    # issue #9 semantic-decoding hardening: `_ANY_SCOPE_WRITE_RE` above
    # only ever matches a scope key/value pair's *raw source spelling*
    # (optional surrounding quote characters aside) -- it never decodes
    # a YAML escape sequence. A fresh, independent verifier reproduced
    # the resulting fail-open bypass: `"c\u006fntents": "wr\u0069te"`
    # never matches it at all, since the raw text between the quotes is
    # literally `c\u006fntents`/`wr\u0069te`, not the literal words
    # `contents`/`write`. `_SCOPE_WRITE_CANDIDATE_RE` locates every
    # bare-or-quoted "key: value" candidate pair anywhere in the text
    # (the same "scan everywhere" design as `_ANY_SCOPE_WRITE_RE`
    # itself); each candidate is only actually decoded (via the one
    # canonical `_scan_quoted_scalar` decoder) when at least one side is
    # quoted *and* contains a backslash (a plain/bare pair, or an
    # unescaped quoted pair, is already fully covered by the literal
    # regex above -- re-decoding it would be redundant, never
    # incremental). A candidate whose key or value fails to decode
    # cleanly is always reported fail-closed here (this scanner never
    # silently guesses that an undecodable escaped pair was *probably*
    # harmless); a cleanly-decoded pair is reported exactly like its
    # literal-spelling counterpart above whenever the decoded value is
    # `write`/`write-all` (case-insensitive, matching the literal check
    # above).
    for match in _SCOPE_WRITE_CANDIDATE_RE.finditer(text):
        raw_key, raw_value = match.group("key"), match.group("value")
        key_is_quoted = raw_key[:1] in ('"', "'")
        value_is_quoted = raw_value[:1] in ('"', "'")
        if not (key_is_quoted or value_is_quoted):
            continue  # already covered by the literal regex above
        if "\\" not in raw_key and "\\" not in raw_value:
            continue  # no escape sequence present -- nothing new to decode
        key_decoded, key_problem = _decode_quoted_span(raw_key)
        value_decoded, value_problem = _decode_quoted_span(raw_value)
        if key_problem is not None or value_problem is not None:
            problem = key_problem or value_problem
            violations.append(
                f"found a quoted permission-scope key/value pair {match.group(0)!r} with an "
                f"unsupported/invalid quoted-scalar escape ({_PROBLEM_DESCRIPTIONS.get(problem, problem)}) "
                "-- rejected fail-closed"
            )
            continue
        if value_decoded.lower() in ("write", "write-all"):
            violations.append(
                f"found permission scope grant {key_decoded}: {value_decoded} (raw: {match.group(0)!r}, "
                "decoded from an escaped/quoted spelling)"
            )
    return violations


def check_checkout_pin(text: str) -> List[str]:
    """Validates the decoded ``with`` mapping on every actual checkout.

    A raw substring elsewhere in the file is never evidence: each
    block-style job/steps/step mapping is decoded, the real
    ``actions/checkout`` step is identified from its own ``uses`` value,
    and that same step must contain exactly one block-style ``with``
    mapping with exactly one decoded ``persist-credentials`` key whose
    value is ``false``. Missing/true/duplicate values and comment decoys
    all fail closed. Pin format remains centralized in
    :func:`check_uses_pins`.
    """
    violations = []
    jobs, job_violations = _workflow_jobs(text)
    violations.extend(job_violations)
    checkout_count = 0
    for job in jobs:
        steps, step_violations = _job_steps(job)
        violations.extend(step_violations)
        for item, step_entries in steps:
            uses_entries = [entry for entry in step_entries if entry.key == "uses"]
            if len(uses_entries) != 1:
                continue
            action_ref, problem = _entry_decoded_value(uses_entries[0])
            if problem is not None or not action_ref.lower().startswith("actions/checkout@"):
                continue
            checkout_count += 1
            with_entries = [entry for entry in step_entries if entry.key == "with"]
            if len(with_entries) != 1 or with_entries[0].value:
                violations.append(
                    f"job {job.key!r} checkout step starting at line {item.line} must contain "
                    "exactly one block-style with mapping"
                )
                continue
            with_values, with_problems = _mapping_entries_at_indent(with_entries[0].text, 10)
            violations.extend(
                f"job {job.key!r} checkout with mapping {problem}"
                for problem in with_problems
            )
            persist_values = [
                entry for entry in with_values if entry.key == "persist-credentials"
            ]
            decoded_values = []
            for entry in persist_values:
                decoded, value_problem = _entry_decoded_value(entry)
                if value_problem is not None:
                    violations.append(
                        f"job {job.key!r} checkout persist-credentials value is not a "
                        f"supported static scalar ({value_problem})"
                    )
                decoded_values.append(decoded)
            if len(persist_values) != 1 or decoded_values != ["false"]:
                violations.append(
                    f"job {job.key!r} actual checkout with mapping must contain exactly "
                    "'persist-credentials: false'"
                )
    if checkout_count == 0:
        violations.append("no 'actions/checkout' step found")
    return violations


# `owner/repo[/subpath]@ref`: captures the action reference up to (but
# excluding) the final `@ref` segment, and the ref itself, in one shot --
# reused by both `check_uses_pins` (pin-format enforcement) and
# `scripts/release_rehearsal/action_pins.py` (the separate committed
# inventory cross-check), so there is exactly one definition of "what an
# external `uses:` action reference looks like".
_USES_REF_SPLIT_RE = re.compile(r"^(?P<action>[^@\s]+)@(?P<ref>[^\s]+)$")


def is_local_action_reference(action_ref: str) -> bool:
    """The single, explicit, narrow "safe local action" rule (issue #9
    mandatory correction #1): a reference to an action *inside this same
    repository* (`./path/to/action` or `../path/to/action`) is implicitly
    pinned to the exact same immutable commit as the workflow file that
    references it -- there is no separate external SHA to pin, and no
    separate upstream source to independently validate. Nothing else is
    ever exempted: any `owner/repo[/subpath]@ref` reference (a real
    external action, on GitHub or any other host) and a Docker
    `docker://...` reference (never used by this repository's real
    workflow, so deliberately not carved out at all -- see module
    docstring's fail-closed design) are always treated as external and
    must be pinned to an exact 40-lowercase-hex commit SHA."""
    return action_ref.startswith("./") or action_ref.startswith("../")


def check_uses_pins(text: str) -> List[str]:
    """Every external `uses:` reference (i.e. every reference that is not
    a local action -- see `is_local_action_reference`) must be pinned to
    an exact, immutable, 40-lowercase-hex commit SHA. A mutable version
    tag (`v7`, `v7.0.1`, `main`, any other branch name), a short SHA, a
    malformed reference, or a wrong-case (uppercase/mixed-case) SHA are
    all rejected alike -- there is no accepted-tag allowlist any more.
    A reference with no `@ref` segment at all (an entirely unpinned
    `owner/repo` -- implicitly whatever the default branch currently
    is) is exactly as rejected as a mutable tag.

    Every occurrence `extract_uses_occurrences` finds is visited here --
    block style, flow style, quoted or bare keys, all alike -- and any
    occurrence that scanner could not fully, unambiguously parse (an
    anchor/alias/tag, a template expression, an unterminated quote, an
    ambiguous embedded colon, or a duplicate key within the same
    mapping) is *always* reported as a hard violation, regardless of
    whatever text happens to follow: this module never guesses a
    meaning for a construct it does not fully understand, and never
    lets an unrecognized `uses:` shape silently pass through
    unchecked."""
    violations = []
    for occ in extract_uses_occurrences(text):
        if occ.problem is not None:
            description = _PROBLEM_DESCRIPTIONS.get(occ.problem, occ.problem)
            violations.append(
                f"line {occ.line}: 'uses:' value {occ.raw_value!r} is not a recognized/supported "
                f"static action reference ({description}) -- rejected fail-closed"
            )
            continue
        action_ref = occ.action_ref
        if is_local_action_reference(action_ref):
            continue
        split = _USES_REF_SPLIT_RE.match(action_ref)
        if split is None:
            violations.append(
                f"line {occ.line}: 'uses: {action_ref}' has no '@ref' pin at all -- every external "
                "action must be pinned to an exact 40-lowercase-hex commit SHA"
            )
            continue
        ref = split.group("ref")
        if not FULL_SHA_RE.fullmatch(ref):
            violations.append(
                f"line {occ.line}: 'uses: {action_ref}' is not pinned to an immutable "
                f"40-lowercase-hex commit SHA (found {ref!r} -- a version tag, branch name, short "
                "SHA, or wrong-case SHA is never accepted; see docs/release_data/action_pins.json "
                "for the exact pinned SHA and its documented upstream source/version)"
            )
    return violations


def check_forbidden_patterns(text: str) -> List[str]:
    violations = []
    for pattern, label in _COMPILED_FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            violations.append(f"forbidden pattern found ({label}): {match.group(0)!r}")
    return violations


# issue #9 verifier remediation: the normal release workflow's
# publication-eligibility steps (`make release-check`/`make release-
# rehearse`, and their `-require-eligible`/`-expect-blocked` siblings --
# see release.mk) must bind the exact, immutable checked-out commit as
# this candidate's target SHA: `${{ github.sha }}` for pull-request/manual
# runs, or the completed Build CI run's `workflow_run.head_sha` for the
# tightly constrained workflow_run path. It must never silently leave the
# value to whatever `git rev-parse HEAD` happens to resolve to inside the
# runner. release.mk's own `RELEASE_TARGET_SHA ?= $(shell git rev-parse
# HEAD)` accepts an environment-variable override with exactly this name,
# so the sole release job's job-level `env:` mapping must carry it.
def check_release_target_sha_binding(text: str) -> List[str]:
    """Validates every actual release eligibility/rehearsal step.

    A command-position ``make release-*`` invocation is discovered only
    inside decoded job/steps/run mappings after safely stripping shell
    comments. Commented-out text and echo-only strings never count. Each
    owning job must have exactly one canonical job-level
    ``RELEASE_TARGET_SHA`` binding: ``github.sha`` normally, or the
    event-aware completed-run head SHA expression for the tightly
    constrained ``workflow_run`` path.

    That validated job mapping is the *only* permitted source. A release
    step may not shadow it through step-level ``env`` (even with the same
    expression), a command-prefix assignment, ``env NAME=value``, a
    standalone assignment, or ``export``. Duplicate job or step env keys
    fail closed rather than relying on YAML/shell last-value-wins rules.

    Deliberately NOT folded into `validate_workflow_text()`'s shared
    aggregator (called directly by `cli.py`'s `cmd_workflow_guard`
    instead, alongside `validate_workflow_text()`) -- that aggregator is
    reused by ~170 other unit tests exercising small, isolated workflow-
    text snippets for unrelated checks (permissions, pins, forbidden
    patterns, variable-assembly) that were never meant to also carry a
    full `RELEASE_TARGET_SHA` binding; keeping this issue-#9-specific
    check separate avoids a false-positive blast radius across every
    one of those unrelated fixtures."""
    jobs, violations = _workflow_jobs(text)
    expected_expression = (
        WORKFLOW_RUN_SHA_EXPRESSION
        if _has_workflow_run_trigger(text)
        else "${{ github.sha }}"
    )
    found_release_step = False
    for job in jobs:
        steps, step_violations = _job_steps(job)
        violations.extend(step_violations)
        job_has_release_step = False
        for item, step_entries in steps:
            run_entries = [entry for entry in step_entries if entry.key == "run"]
            if len(run_entries) != 1:
                continue
            script, run_problem = _run_script(run_entries[0])
            if run_problem is not None:
                continue
            targets, overrides, analysis_problems = _release_step_analysis(script)
            if not targets and not analysis_problems:
                continue
            found_release_step = True
            job_has_release_step = True
            for problem in analysis_problems:
                violations.append(
                    f"job {job.key!r} release step starting at line {item.line} {problem}"
                )

            env_entries = [entry for entry in step_entries if entry.key == "env"]
            for env_entry in env_entries:
                if env_entry.value:
                    if "RELEASE_TARGET_SHA" in env_entry.value:
                        violations.append(
                            f"job {job.key!r} release step starting at line {item.line} "
                            "must not override RELEASE_TARGET_SHA in step-level env"
                        )
                    continue
                env_values, env_problems = _mapping_entries_at_indent(env_entry.text, 10)
                violations.extend(
                    f"job {job.key!r} release step env mapping {problem}"
                    for problem in env_problems
                )
                target_values = [
                    entry for entry in env_values if entry.key == "RELEASE_TARGET_SHA"
                ]
                if target_values:
                    violations.append(
                        f"job {job.key!r} release step starting at line {item.line} must not "
                        "override RELEASE_TARGET_SHA in step-level env; only the validated "
                        "job-level binding is allowed"
                    )
            if len(env_entries) > 1:
                violations.append(
                    f"job {job.key!r} release step starting at line {item.line} repeats "
                    "step-level env mappings"
                )
            for override in overrides:
                violations.append(
                    f"job {job.key!r} release step starting at line {item.line} must not "
                    f"override RELEASE_TARGET_SHA via {override}; only the validated "
                    "job-level binding is allowed"
                )

        if job_has_release_step:
            violations.extend(
                _check_job_target_binding(
                    job,
                    expected_expression,
                    label=job.key,
                )
            )
    if not found_release_step:
        return []
    return sorted(set(violations))


_FULL_MATRIX_STEP_COMMANDS = {
    ("host", "Run artifact guard gates"): (
        "python3 -m unittest discover -s scripts/artifact_guard_tests -p 'test_*.py' -v",
        "python3 scripts/artifact_guard.py --revision HEAD",
    ),
    ("host", "Run documentation gates"): (
        "python3 -m unittest discover -s scripts/docs_check_tests -p 'test_*.py' -v",
        "python3 scripts/check_docs.py --check --check-examples",
    ),
    ("host", "Run generated-data gates"): (
        "make generated-data-test",
        "make generated-data-check",
    ),
    ("host", "Run expansion localization gates"): (
        "make localization-test",
    ),
    ("host", "Run full-game localization artifact gates"): (
        "make game-localization-test",
        "python3 -m scripts.localization.game_locales check",
        "python3 -m scripts.localization.game_locales check-crosswalk",
        "python3 -m scripts.localization.game_locales check-raw-closure",
    ),
    ("host", "Run CJK font gates"): (
        "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
    ),
    ("host", "Run remaining texttools codec gates"): (
        "python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v",
    ),
    ("host", "Run configuration and linker-budget gates"): (
        "python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v",
        "python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v",
    ),
    ("modern", "Build tools"): (
        "./build_tools.sh",
    ),
    ("modern", "Run canonical modern linker/runtime gate"): (
        "make expansion-modern-linker-check MODERN_CONFIG=${{ matrix.config }} MODERN_ABI=aapcs -j2",
    ),
    ("legacy", "Build tools"): (
        "./build_tools.sh",
    ),
    ("legacy", "Build archival lane without a copyrighted baserom"): (
        "set -euo pipefail",
        "test ! -e baserom.gba",
        "make legacy -j2",
        "test ! -e baserom.gba",
    ),
    ("legacy", "Validate pinned archival payload identities"): (
        "make -C mgfembp compare",
    ),
    ("release-evidence", "Run release test suites"): (
        "make release-test",
    ),
    ("release-evidence", "Validate Full Matrix workflow contract"): (
        "make release-full-matrix-workflow-guard",
    ),
    ("release-evidence", "Check generated release evidence"): (
        "make release-changelog-check",
        'python3 -m scripts.release_rehearsal.allowlist check --target-sha "$RELEASE_TARGET_SHA"',
    ),
    ("release-evidence", "Check release documentation"): (
        "python3 -m scripts.release_rehearsal.doc_links",
    ),
    ("release-evidence", "Check release tree and submodule bindings"): (
        "make release-tree-coverage-check",
        "make release-submodule-binding-check",
    ),
    ("release-evidence", "Check provenance records"): (
        'python3 -m scripts.release_rehearsal.provenance check --target-sha "$RELEASE_TARGET_SHA"',
    ),
    ("release-evidence", "Check source-release guard"): (
        "python3 - <<'PY'",
        "from pathlib import Path",
        "from scripts.release_rehearsal.source_guard import (",
        "load_allowlist,",
        "load_map_hex_exceptions,",
        "scan_source_release_candidate,",
        ")",
        'allowlist = load_allowlist(Path("docs/release_data/source_allowlist.json"))',
        "exceptions = load_map_hex_exceptions(",
        'Path("docs/release_data/map_hex_exceptions.json")',
        ")",
        "violations = scan_source_release_candidate(",
        'Path("."),',
        "allowlist,",
        "map_hex_exceptions=exceptions,",
        ")",
        "for path, rule in violations:",
        'print(f"{path}: {rule}")',
        "if violations:",
        "raise SystemExit(1)",
        'print("source_guard: pass (tracked release candidate)")',
        "PY",
    ),
    ("release-evidence", "Assert publication remains blocked"): (
        "make release-check-expect-blocked",
    ),
}

_FULL_MATRIX_LANE_JOBS = ("host", "modern", "legacy", "release-evidence")
_FULL_MATRIX_REQUIRED_JOBS = _FULL_MATRIX_LANE_JOBS + ("summary",)
_FULL_MATRIX_CHECKOUT_REF = "${{ github.sha }}"
_FULL_MATRIX_VERIFY_STEP_NAME = "Log and verify tested revision"
_FULL_MATRIX_VERIFY_COMMANDS = (
    "set -euo pipefail",
    'ACTUAL_SHA="$(git rev-parse HEAD)"',
    "printf 'github.sha=%s\\n' \"$EXPECTED_SHA\"",
    "printf 'github.ref=%s\\n' \"$EXPECTED_REF\"",
    "printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"",
    'test "$ACTUAL_SHA" = "$EXPECTED_SHA"',
    "printf 'verified checkout.sha=%s\\n' \"$ACTUAL_SHA\" >> \"$GITHUB_STEP_SUMMARY\"",
)
_FULL_MATRIX_SUMMARY_NEEDS = _FULL_MATRIX_LANE_JOBS
_FULL_MATRIX_SUMMARY_ENV = {
    "RUN_SHA": "${{ github.sha }}",
    "RUN_REF": "${{ github.ref }}",
    "HOST_RESULT": "${{ needs.host.result }}",
    "MODERN_RESULT": "${{ needs.modern.result }}",
    "LEGACY_RESULT": "${{ needs.legacy.result }}",
    "RELEASE_EVIDENCE_RESULT": "${{ needs.release-evidence.result }}",
}
_FULL_MATRIX_SUMMARY_COMMANDS = (
    "{",
    'echo "## Full Matrix CI"',
    "echo",
    'echo "| Item | Conclusion |"',
    'echo "| --- | --- |"',
    'echo "| SHA | \\`$RUN_SHA\\` |"',
    'echo "| Ref | \\`$RUN_REF\\` |"',
    'echo "| host | $HOST_RESULT |"',
    'echo "| modern (debug + release) | $MODERN_RESULT |"',
    'echo "| legacy | $LEGACY_RESULT |"',
    'echo "| release-evidence | $RELEASE_EVIDENCE_RESULT |"',
    '} >> "$GITHUB_STEP_SUMMARY"',
    'for result in "$HOST_RESULT" "$MODERN_RESULT" "$LEGACY_RESULT" "$RELEASE_EVIDENCE_RESULT"',
    "do",
    'if [ "$result" != "success" ]; then',
    'echo "required Full Matrix CI lane did not succeed: $result" >&2',
    "exit 1",
    "fi",
    "done",
)


def _static_entry_value(entry: _MappingEntry):
    value, problem = _entry_decoded_value(entry)
    return value, problem


def _check_continue_on_error(entries: List[_MappingEntry], label: str) -> List[str]:
    """Allows only an absent or literal-false continue-on-error value."""
    violations = []
    matches = [entry for entry in entries if entry.key == "continue-on-error"]
    if len(matches) > 1:
        violations.append(f"{label} repeats continue-on-error")
    for entry in matches:
        value, problem = _static_entry_value(entry)
        if problem is not None or value.lower() != "false":
            violations.append(
                f"{label} must not enable or dynamically compute continue-on-error; "
                "required Full Matrix gates must fail closed"
            )
    return violations


def _decode_flow_identifier_sequence(value: str):
    """Decodes the canonical ``[job-a, job-b]`` needs form."""
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return (), "not a flow sequence"
    body = value[1:-1].strip()
    if not body:
        return (), None
    items = tuple(item.strip() for item in body.split(","))
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", item) for item in items):
        return (), "contains a non-identifier or ambiguous item"
    return items, None


def _full_matrix_checkout_steps(steps):
    checkout_steps = []
    for index, (item, entries) in enumerate(steps):
        uses_entries = [entry for entry in entries if entry.key == "uses"]
        if len(uses_entries) != 1:
            continue
        action_ref, problem = _static_entry_value(uses_entries[0])
        if problem is None and action_ref.lower().startswith("actions/checkout@"):
            checkout_steps.append((index, item, entries))
    return checkout_steps


def _check_full_matrix_lane_checkout(job: _MappingEntry, steps) -> List[str]:
    """Validates one lane's actual checkout and immediate SHA verifier."""
    violations = []
    checkout_steps = _full_matrix_checkout_steps(steps)
    if len(checkout_steps) != 1:
        return [
            f"Full Matrix lane {job.key!r} must contain exactly one actual "
            "actions/checkout step"
        ]

    checkout_index, _checkout_item, checkout_entries = checkout_steps[0]
    with_entries = [entry for entry in checkout_entries if entry.key == "with"]
    if len(with_entries) != 1 or with_entries[0].value:
        violations.append(
            f"Full Matrix lane {job.key!r} actual checkout must contain exactly one "
            "block-style with mapping"
        )
    else:
        with_values, with_problems = _mapping_entries_at_indent(with_entries[0].text, 10)
        violations.extend(
            f"Full Matrix lane {job.key!r} checkout with mapping {problem}"
            for problem in with_problems
        )
        expected_values = {
            "fetch-depth": "0",
            "submodules": "recursive",
            "persist-credentials": "false",
        }
        for key, expected in expected_values.items():
            matches = [entry for entry in with_values if entry.key == key]
            decoded = [_static_entry_value(entry) for entry in matches]
            if len(matches) != 1 or decoded != [(expected, None)]:
                violations.append(
                    f"Full Matrix lane {job.key!r} actual checkout with mapping must "
                    f"contain exactly {key!r}: {expected!r}"
                )

        ref_values = [entry for entry in with_values if entry.key == "ref"]
        if len(ref_values) > 1 or (
            ref_values and ref_values[0].value != _FULL_MATRIX_CHECKOUT_REF
        ):
            violations.append(
                f"Full Matrix lane {job.key!r} actual checkout ref must be omitted "
                "for workflow_dispatch's selected ref or bind exactly to github.sha; "
                "branch literals and other revisions are rejected"
            )

    verify_index = checkout_index + 1
    if verify_index >= len(steps):
        violations.append(
            f"Full Matrix lane {job.key!r} actual checkout must be immediately followed "
            "by the executable dispatched-SHA verification step"
        )
        return violations

    verify_item, verify_entries = steps[verify_index]
    if _step_name(verify_entries) != _FULL_MATRIX_VERIFY_STEP_NAME:
        violations.append(
            f"Full Matrix lane {job.key!r} actual checkout must be immediately followed "
            f"by step {_FULL_MATRIX_VERIFY_STEP_NAME!r}"
        )
        return violations

    if {entry.key for entry in verify_entries} != {"name", "env", "run"}:
        violations.append(
            f"Full Matrix lane {job.key!r} SHA verification step must contain only "
            "name, env, and run mappings"
        )

    env_entries = [entry for entry in verify_entries if entry.key == "env"]
    if len(env_entries) != 1 or env_entries[0].value:
        violations.append(
            f"Full Matrix lane {job.key!r} SHA verification step must contain exactly "
            "one block-style env mapping"
        )
    else:
        env_values, env_problems = _mapping_entries_at_indent(env_entries[0].text, 10)
        violations.extend(
            f"Full Matrix lane {job.key!r} SHA verification env mapping {problem}"
            for problem in env_problems
        )
        expected_env = {
            "EXPECTED_SHA": "${{ github.sha }}",
            "EXPECTED_REF": "${{ github.ref }}",
        }
        if len(env_values) != len(expected_env):
            violations.append(
                f"Full Matrix lane {job.key!r} SHA verification env must contain "
                "only EXPECTED_SHA and EXPECTED_REF"
            )
        for key, expected in expected_env.items():
            matches = [entry for entry in env_values if entry.key == key]
            if len(matches) != 1 or matches[0].value != expected:
                violations.append(
                    f"Full Matrix lane {job.key!r} SHA verification env.{key} must "
                    f"bind exactly {expected!r}"
                )

    run_entries = [entry for entry in verify_entries if entry.key == "run"]
    if len(run_entries) != 1:
        violations.append(
            f"Full Matrix lane {job.key!r} SHA verification step must contain exactly "
            "one static run command"
        )
    else:
        script, problem = _run_script(run_entries[0])
        if problem is not None:
            violations.append(
                f"Full Matrix lane {job.key!r} SHA verification step {problem}"
            )
        else:
            commands = tuple(_executable_run_lines(script))
            if commands != _FULL_MATRIX_VERIFY_COMMANDS:
                violations.append(
                    f"Full Matrix lane {job.key!r} SHA verification executable commands "
                    f"must be exactly {list(_FULL_MATRIX_VERIFY_COMMANDS)!r}, found "
                    f"{list(commands)!r} (step starts at line {verify_item.line}); "
                    "comments, echo strings, and true are not verification"
                )
    return violations


def _check_full_matrix_summary(job: _MappingEntry, steps) -> List[str]:
    """Validates the all-lanes dependency and real needs-result failure path."""
    violations = []
    entries, entry_problems = _mapping_entries_at_indent(job.text, 4)
    violations.extend(
        f"Full Matrix summary job mapping {problem}" for problem in entry_problems
    )

    needs_entries = [entry for entry in entries if entry.key == "needs"]
    if len(needs_entries) != 1:
        violations.append(
            "Full Matrix summary job must contain exactly one needs mapping"
        )
    else:
        needs, problem = _decode_flow_identifier_sequence(needs_entries[0].value)
        if problem is not None or needs != _FULL_MATRIX_SUMMARY_NEEDS:
            violations.append(
                "Full Matrix summary job needs must list host, modern, legacy, and "
                "release-evidence exactly once"
            )

    env_entries = [entry for entry in entries if entry.key == "env"]
    if len(env_entries) != 1 or env_entries[0].value:
        violations.append(
            "Full Matrix summary job must contain exactly one block-style env mapping"
        )
    else:
        env_values, env_problems = _mapping_entries_at_indent(env_entries[0].text, 6)
        violations.extend(
            f"Full Matrix summary env mapping {problem}" for problem in env_problems
        )
        if len(env_values) != len(_FULL_MATRIX_SUMMARY_ENV):
            violations.append(
                "Full Matrix summary env must contain only the canonical run identity "
                "and needs-result bindings"
            )
        for key, expected in _FULL_MATRIX_SUMMARY_ENV.items():
            matches = [entry for entry in env_values if entry.key == key]
            if len(matches) != 1 or matches[0].value != expected:
                violations.append(
                    f"Full Matrix summary env.{key} must bind exactly {expected!r}"
                )

    if len(steps) != 1:
        violations.append("Full Matrix summary job must contain exactly one step")
        return violations
    item, step_entries = steps[0]
    if _step_name(step_entries) != "Render fail-closed matrix summary":
        violations.append(
            "Full Matrix summary job must contain the named fail-closed summary step"
        )
        return violations
    if {entry.key for entry in step_entries} != {"name", "run"}:
        violations.append(
            "Full Matrix summary step must contain only name and run mappings; "
            "step env/if/continue-on-error overrides are rejected"
        )
    run_entries = [entry for entry in step_entries if entry.key == "run"]
    if len(run_entries) != 1:
        violations.append(
            "Full Matrix summary step must contain exactly one static run command"
        )
        return violations
    script, problem = _run_script(run_entries[0])
    if problem is not None:
        violations.append(f"Full Matrix summary step {problem}")
        return violations
    commands = tuple(_executable_run_lines(script))
    if commands != _FULL_MATRIX_SUMMARY_COMMANDS:
        violations.append(
            "Full Matrix summary executable commands must render and fail from the "
            f"canonical needs-result variables exactly, found {list(commands)!r} "
            f"(step starts at line {item.line})"
        )
    return violations


def check_full_matrix_contract(text: str) -> List[str]:
    """Requires the complete fail-closed Full Matrix job/step contract.

    Required jobs/steps may not opt into continue-on-error or conditional
    skipping. Each lane has one actual checkout of either the explicit
    dispatched SHA or workflow_dispatch's selected default, immediately
    followed by a recorded ``git rev-parse HEAD`` comparison with
    ``github.sha``. The summary depends on every lane and fails from
    structurally bound ``needs.*.result`` values. Comments, echo strings,
    ``true``, unrelated mappings, and shell/env shadowing never count.
    """
    text = _normalize_for_scanning(text)
    jobs, violations = _workflow_jobs(text)
    jobs_by_name = {}
    for job in jobs:
        jobs_by_name.setdefault(job.key, []).append(job)

    if tuple(job.key for job in jobs) != _FULL_MATRIX_REQUIRED_JOBS:
        violations.append(
            "Full Matrix jobs must be exactly host, modern, legacy, "
            "release-evidence, and summary in canonical order"
        )

    decoded_steps = {}
    for job_name in _FULL_MATRIX_REQUIRED_JOBS:
        matching_jobs = jobs_by_name.get(job_name, [])
        if len(matching_jobs) != 1:
            violations.append(
                f"Full Matrix contract requires exactly one job named {job_name!r}"
            )
            continue
        job = matching_jobs[0]
        job_entries, entry_problems = _mapping_entries_at_indent(job.text, 4)
        violations.extend(
            f"Full Matrix job {job_name!r} mapping {problem}"
            for problem in entry_problems
        )
        violations.extend(
            _check_continue_on_error(job_entries, f"Full Matrix job {job_name!r}")
        )
        if_entries = [entry for entry in job_entries if entry.key == "if"]
        if job_name == "summary":
            decoded_if = [_static_entry_value(entry) for entry in if_entries]
            if len(if_entries) != 1 or decoded_if != [("always()", None)]:
                violations.append(
                    "Full Matrix summary job must use exactly 'if: always()'"
                )
        elif if_entries:
            violations.append(
                f"Full Matrix required job {job_name!r} must not have a job-level "
                "if condition that can skip the lane"
            )

        steps, step_violations = _job_steps(job)
        violations.extend(step_violations)
        decoded_steps[job_name] = steps
        for item, step_entries in steps:
            name = _step_name(step_entries)
            identifier = name if name is not None else f"line {item.line}"
            step_label = f"Full Matrix job {job_name!r} step {identifier!r}"
            violations.extend(_check_continue_on_error(step_entries, step_label))
            if [entry for entry in step_entries if entry.key == "if"]:
                violations.append(
                    f"{step_label} must not have an if condition that can skip a "
                    "required step"
                )

    for job_name in _FULL_MATRIX_LANE_JOBS:
        matching_jobs = jobs_by_name.get(job_name, [])
        if len(matching_jobs) == 1 and job_name in decoded_steps:
            violations.extend(
                _check_full_matrix_lane_checkout(
                    matching_jobs[0],
                    decoded_steps[job_name],
                )
            )

    for (job_name, step_name), expected_commands in _FULL_MATRIX_STEP_COMMANDS.items():
        matching_jobs = jobs_by_name.get(job_name, [])
        if len(matching_jobs) != 1:
            continue
        steps = decoded_steps.get(job_name, [])
        matching_steps = [
            (item, entries)
            for item, entries in steps
            if _step_name(entries) == step_name
        ]
        if len(matching_steps) != 1:
            violations.append(
                f"Full Matrix job {job_name!r} must contain exactly one named step "
                f"{step_name!r}"
            )
            continue
        item, entries = matching_steps[0]
        run_entries = [entry for entry in entries if entry.key == "run"]
        if len(run_entries) != 1:
            violations.append(
                f"Full Matrix job {job_name!r} step {step_name!r} must contain exactly "
                "one static run command"
            )
            continue
        script, problem = _run_script(run_entries[0])
        if problem is not None:
            violations.append(
                f"Full Matrix job {job_name!r} step {step_name!r} {problem}"
            )
            continue
        actual_commands = tuple(_executable_run_lines(script))
        if actual_commands != expected_commands:
            violations.append(
                f"Full Matrix job {job_name!r} step {step_name!r} executable commands "
                f"must be exactly {list(expected_commands)!r}, found {list(actual_commands)!r} "
                f"(step starts at line {item.line})"
            )

    summary_jobs = jobs_by_name.get("summary", [])
    if len(summary_jobs) == 1 and "summary" in decoded_steps:
        violations.extend(
            _check_full_matrix_summary(
                summary_jobs[0],
                decoded_steps["summary"],
            )
        )
    return sorted(set(violations))


def check_dangerous_uses_actions(text: str) -> List[str]:
    """Generalized, case-insensitive `uses:` action-name heuristic: any
    referenced action whose name contains "upload", "release", "publish",
    or "deploy" is rejected, regardless of exact action identity/case --
    this deliberately catches a disguised/unlisted-but-clearly-named
    action (e.g. a fork, a differently-cased reference, or an action this
    module's authors have never heard of) instead of only matching a
    fixed enumeration that must be kept manually up to date forever.
    Scans every occurrence `extract_uses_occurrences` finds (block,
    flow, quoted, or bare key alike), not only a bare block-style
    `uses:` at column 0."""
    violations = []
    for occ in extract_uses_occurrences(text):
        action_ref = occ.action_ref
        if not action_ref:
            continue
        lowered = action_ref.lower()
        for needle in _DANGEROUS_ACTION_NAME_SUBSTRINGS:
            if needle in lowered:
                violations.append(
                    f"line {occ.line}: 'uses:' references a dangerous-sounding action: "
                    f"{action_ref!r} (contains {needle!r})"
                )
                break
    return violations


def validate_workflow_text(text: str) -> List[str]:
    normalized = _normalize_for_scanning(text)
    violations: List[str] = []
    violations.extend(check_triggers(normalized))
    violations.extend(check_workflow_run_contract(normalized))
    violations.extend(check_top_level_permissions(normalized))
    violations.extend(check_no_write_anywhere(normalized))
    violations.extend(check_checkout_pin(normalized))
    violations.extend(check_uses_pins(normalized))
    violations.extend(check_forbidden_patterns(normalized))
    violations.extend(check_dangerous_uses_actions(normalized))
    violations.extend(check_variable_command_assembly(normalized))
    return sorted(set(violations))


def validate_workflow_contract(text: str, contract: str) -> List[str]:
    """Validates shared safety plus one named repository workflow contract."""
    if contract not in WORKFLOW_CONTRACT_CHOICES:
        raise ValueError(f"unknown workflow contract: {contract}")
    normalized = _normalize_for_scanning(text)
    violations = list(validate_workflow_text(normalized))
    violations.extend(check_release_target_sha_binding(normalized))
    violations.extend(check_shell_overrides(normalized, contract))
    if contract == WORKFLOW_CONTRACT_FULL_MATRIX:
        violations.extend(check_full_matrix_contract(normalized))
    return sorted(set(violations))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument(
        "--contract",
        choices=WORKFLOW_CONTRACT_CHOICES,
        default=WORKFLOW_CONTRACT_RELEASE_REHEARSAL,
    )
    args = parser.parse_args(argv)

    try:
        text = args.workflow.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    violations = validate_workflow_contract(text, args.contract)
    for violation in violations:
        print(violation)
    if violations:
        print(f"workflow_guard: {len(violations)} finding(s)", file=sys.stderr)
        return 1
    print(f"workflow_guard: {args.workflow} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
