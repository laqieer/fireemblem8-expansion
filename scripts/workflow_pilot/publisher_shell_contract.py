"""Closed contract for the trusted patch-release builder-isolation shell."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import posixpath
import re
from typing import Iterable


REVIEWED_PATCH_RELEASE_RUN_SHA256 = (
    "3d84f8748c55f6a014974834fa02227e7bf2f59bb3125490b6d976935b14213b"
)
REVIEWED_BUILDER_ISOLATION_SHA256 = (
    "f7c9f894a58fe1fd9b57c9e31dbe87fd05db5cab12d04ac98de490e3fee5c95f"
)
REVIEWED_BUILDER_HELPER_INVENTORY = {
    (
        "builder_main",
        "3b57d31d0a22d2c4463a1c64925b8a71e65d595ab3c7a32a646e5cc8ab4bc1ab",
    ): 1,
    (
        "checked_runtime_transport_signature",
        "86e8011457f1911f1c977ad8f162fa651f99a069add4324a9933eb20029aee5a",
    ): 1,
    (
        "checked_supervisor_transport_signature",
        "7aef373e44ec1a6019d66401d9944401c0c13f99eab7c2b093ee910b6ccd8292",
    ): 1,
    (
        "create_runtime_transport_file",
        "13b2bcd8e740138330cf75c2061ce14ee0af6b2f89f09445afc58aceadb6daea",
    ): 1,
    (
        "create_supervisor_transport_file",
        "f2a16cb5bfd6c0026c8796dc8ba483642c4df2d849800ae0a628ee9062996326",
    ): 1,
    (
        "isolated_stage_failure",
        "3e52e0e844a917bec8c2afc23f2f9511d72eb22c82314c082c7b460f7199f2eb",
    ): 1,
    (
        "list_dev_mount_targets",
        "f1e90d92ae1dfda62f37c0cadfc32c09634132a3f4d00a008b17cec923757cce",
    ): 1,
    (
        "list_writable_mount_records",
        "c9a14585224556625d83df8124c9299c7c5c12a635f87316d88175f6a4a20c8f",
    ): 1,
    (
        "read_checked_runtime_transport_file",
        "b3c563d971d288d74fafae4d33e747bd52f4603d1831facac8430cd0774ad52a",
    ): 1,
    (
        "read_checked_supervisor_transport_file",
        "75554d782b0eed4c69f0324dc83090782db30fccc9499aa00994bd70fb66eae2",
    ): 1,
    (
        "remove_runtime_transport_file",
        "be01a3eb446e29c705b450d542853329d0861fc1819635a0658bfd5aed2b8f14",
    ): 1,
    (
        "remove_supervisor_transport_file",
        "a1ecfc82bb32648553c081bc3464760c729ba15215d695629e55596b1706c5dd",
    ): 1,
    (
        "unmount_if_mounted",
        "b07d659b2dad4afa252d0873a823a628af04c2980a441ab138fff67942e0dda6",
    ): 1,
}
REVIEWED_HIDDEN_MASK_LOOP_SHA256 = (
    "77e81e3a773e78b4c58132c553ea3a3ef0719f802fe1164b59f60aef948235f5"
)
REVIEWED_HIDDEN_MASK_LOOP_COMMANDS = (
    "for hidden in /home/runner /root /var /run /sys",
    "do",
    'test -d "$hidden"',
    '/usr/bin/mount -t tmpfs     -o nosuid,nodev,noexec,mode=0755,size=1m     builder-mask "$hidden"',
    '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"',
    "done",
)
REVIEWED_HIDDEN_MASK_LOOP_PREVIOUS_COMMAND = "unmount_if_mounted /sys"
REVIEWED_HIDDEN_MASK_LOOP_NEXT_COMMAND = "unmount_if_mounted /tmp"
REVIEWED_HIDDEN_MASK_LOOP_HEADER = "for hidden in /home/runner /root /var /run /sys; do\n"
APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS = {
    ("/usr/bin/mount", "-o", "remount,ro,nosuid,nodev,noexec", "$hidden"),
}
APPROVED_NONLITERAL_MOUNT_COMMANDS = {
    ("/usr/bin/mount", "--bind", "$builder_root/handoff", "/mnt/export"),
    ("/usr/bin/mount", "--bind", "$builder_root/wheelhouse", "/mnt/wheelhouse"),
    (
        "/usr/bin/mount",
        "-t",
        "tmpfs",
        "-o",
        "nosuid,nodev,noexec,mode=0755,size=1m",
        "builder-mask",
        "$hidden",
    ),
}
APPROVED_SUPERVISOR_COMMAND_TOKENS = {
    ("/mnt/supervisor",),
    ("/usr/bin/mkdir", "-m", "0700", "/mnt/supervisor"),
    (
        "/usr/bin/mount",
        "-t",
        "tmpfs",
        "-o",
        "nosuid,nodev,noexec,mode=0700,size=1m",
        "builder-supervisor",
        "/mnt/supervisor",
    ),
    ("/usr/bin/mkdir", "-m", "0700", "/mnt/supervisor/cgroup"),
    ("/usr/bin/mount", "--bind", "$cgroup_path", "/mnt/supervisor/cgroup"),
    (
        "/usr/bin/mount",
        "-o",
        "remount,bind,ro,nosuid,nodev,noexec",
        "/mnt/supervisor/cgroup",
    ),
    ("test", "$(stat -c %u /mnt/supervisor)", "=", "0"),
    ("test", "$(/usr/bin/stat -c %u /mnt/supervisor)", "=", "0"),
    ("test", "$(stat -c %a /mnt/supervisor)", "=", "700"),
    ("test", "$(/usr/bin/stat -c %a /mnt/supervisor)", "=", "700"),
    ("supervisor_cgroup=/mnt/supervisor/cgroup",),
}
PATCH_RELEASE_PARSER_HEREDOC_NAMES = (
    "list_dev_mount_targets",
    "list_writable_mount_records",
)
PATCH_RELEASE_PARSER_HEREDOC_INTRODUCER = "  /usr/bin/python3 -I -S - <<'PY'"
_PATCH_RELEASE_PARSER_HEREDOC_RE = re.compile(
    r"(?ms)^(?P<name>list_dev_mount_targets|list_writable_mount_records)\(\) \{\n"
    r"  /usr/bin/python3 -I -S - <<'PY'\n"
    r"(?P<body>.*?)\n"
    r"PY\n"
    r"\}"
)
PATCH_RELEASE_MEMBERSHIP_CHECKER_NAME = "check_supervisor_cgroup_membership"
PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER = (
    "/usr/bin/python3 -I -S - \"$$\" <<'PY'"
)
_PATCH_RELEASE_MEMBERSHIP_CHECKER_RE = re.compile(
    r"(?ms)^/usr/bin/python3 -I -S - \"\$\$\" <<'PY'\n"
    r"(?P<body>.*?)\n"
    r"PY$"
)
_PATCH_RELEASE_MEMBERSHIP_CHECKER_SOURCE = """\
import os
import re
import sys

MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"
MAX_MEMBERSHIP_BYTES = 4096

if len(sys.argv) != 2 or re.fullmatch(r"[1-9][0-9]*", sys.argv[1]) is None:
    raise SystemExit(125)
expected_pid = int(sys.argv[1], 10)
checker_pid = os.getpid()
if expected_pid == checker_pid:
    raise SystemExit(125)
try:
    with open(MEMBERSHIP_PATH, "rb", buffering=0) as stream:
        payload = stream.read(MAX_MEMBERSHIP_BYTES + 1)
except OSError:
    raise SystemExit(125)
if (
    len(payload) > MAX_MEMBERSHIP_BYTES
    or not payload.endswith(b"\\n")
):
    raise SystemExit(125)
records = payload[:-1].split(b"\\n")
if len(records) != 2 or any(
    re.fullmatch(rb"[1-9][0-9]*", record) is None
    for record in records
):
    raise SystemExit(125)
members = {int(record, 10) for record in records}
if (
    len(members) != 2
    or members != {expected_pid, checker_pid}
):
    raise SystemExit(125)
"""
_SIMPLE_COMMAND_PREFIXES = frozenset(
    {"if", "then", "do", "elif", "while", "until", "!", "else", "{"}
)
_CONTROL_OPERATORS = frozenset({";", "&&", "||", "|", "|&", "&"})
_DISALLOWED_MOUNT_WRAPPERS = frozenset({"env", "command", "eval"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_BUSYBOX_BASENAME = "busybox"
_ENV_BASENAME = "env"
_SHELL_INTERPRETER_BASENAMES = frozenset({"bash", "sh", "dash"})
_SHELL_STRUCTURE_TOKENS = frozenset(
    {"case", "do", "done", "elif", "else", "esac", "fi", "for", "if", "in", "{", "}"}
)
_ENV_ZERO_ARG_OPTIONS = frozenset({"-i", "--ignore-environment"})
_ENV_OPTIONS_WITH_ARGS = frozenset({"-C", "--chdir", "-u", "--unset"})
_LITERAL_RUN_HEADER_RE = re.compile(
    r"^(?P<indent> {6})run:[ \t]*(?P<style>\|[1-9+-]*)(?:[ \t]*(?:#.*)?)?(?:\r?\n|\Z)$"
)
_NICE_OLD_STYLE_RE = re.compile(r"-[0-9+-]+")
_NICE_SHORT_ADJUSTMENT_RE = re.compile(r"-n[0-9+-]+")
_OBVIOUS_WRAPPER_OPERAND_RE = re.compile(r"(?:0x[0-9A-Fa-f]+|[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*)")
_WRAPPER_BASENAMES = frozenset({"command", "nice", "setsid", "sudo", "timeout"})
_FLOCK_ZERO_ARG_OPTIONS = frozenset(
    {"-F", "--no-fork", "-n", "--nonblock", "-o", "--close", "-s", "--shared", "--verbose", "-u", "--unlock", "-x", "--exclusive"}
)
_FLOCK_OPTIONS_WITH_ARGS = frozenset({"-E", "--conflict-exit-code", "-w", "--timeout"})
_MOUNT_SHORT_ZERO_ARG_OPTIONS = frozenset(
    {"a", "c", "f", "F", "h", "i", "l", "n", "r", "v", "V", "w", "B", "M", "R"}
)
_MOUNT_SHORT_OPTIONS_WITH_ARGS = frozenset({"L", "N", "O", "T", "U", "t"})
_SUBSTITUTION_SCAN_MAX_DEPTH = 8
_SUBSTITUTION_SCAN_MAX_BODY_CHARS = 16384
_SUBSTITUTION_SCAN_MAX_COUNT = 128
_SHELL_BRACED_PARAMETER_RE = re.compile(r"\$\{(?P<body>[^{}]*)\}")
_SHELL_PLAIN_VARIABLE_REFERENCE_RE = re.compile(
    r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_SHELL_POSITIONAL_REFERENCE_RE = re.compile(
    r"\$(?P<position>[1-9][0-9]*)"
)
_SHELL_ALIAS_ASSIGNMENT_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<append>\+)?=(?P<value>.*)"
)
_SHELL_INDEXED_ASSIGNMENT_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\[(?P<index>[^\]]*)\](?P<append>\+)?=(?P<value>.*)"
)
_SHELL_ARRAY_EXPANSION_RE = re.compile(
    r"\$\{[!#]?[A-Za-z_][A-Za-z0-9_]*\[[^\]]*\][^}]*\}"
)
_RAW_CGROUP_ROOT_MARKER = "\x00raw-cgroup-root\x00"
_AMBIGUOUS_TRACKED_PARAMETER_MARKER = "\x00ambiguous-tracked-parameter\x00"
_AMBIGUOUS_ARRAY_ALIAS_MARKER = "\x00ambiguous-array-alias\x00"
_INDEXED_ARRAY_ALIAS_MARKER = "\x00indexed-array-alias\x00"
_ASSOCIATIVE_ARRAY_ALIAS_MARKER = "\x00associative-array-alias\x00"
_AMBIGUOUS_DYNAMIC_ALIAS_MARKER = "\x00ambiguous-dynamic-alias\x00"
_NAMEREF_ALIAS_MARKER = "\x00nameref-alias\x00"
_READONLY_ALIAS_MARKER = "\x00readonly-alias\x00"
_LITERAL_DOLLAR_MARKER = "\x00literal-dollar\x00"
_DISPATCH_STATE_VARIABLES = frozenset(
    {
        "BASHOPTS",
        "BASH_ALIASES",
        "BASH_CMDS",
        "BASH_ENV",
        "ENV",
        "PATH",
        "SHELLOPTS",
    }
)
_DISPATCH_SET_OPTIONS = frozenset({"hashall", "posix"})
_VALID_SET_OPTION_NAMES = frozenset(
    "allexport braceexpand emacs errexit errtrace functrace hashall "
    "histexpand history ignoreeof interactive-comments keyword monitor "
    "noclobber noexec noglob nolog notify nounset onecmd physical pipefail "
    "posix privileged verbose vi xtrace".split()
)
_RESERVED_TRANSPORT_OUTPUTS = frozenset(
    {
        "checked_runtime_transport_output",
        "checked_supervisor_transport_output",
    }
)
_REVIEWED_TRANSPORT_PHASE_EVENTS = (
    ("producer", "read_checked_supervisor_transport_file", "$dev_mounts_file", "$dev_mount_targets_max_bytes"),
    ("consumer", "for", "((index=${#checked_supervisor_transport_output[@]} - 1; index >= 0; index--))"),
    ("consumer", "dev_mount=${checked_supervisor_transport_output[index]}"),
    ("producer", "read_checked_supervisor_transport_file", "$remaining_dev_mounts_file", "$dev_mount_targets_max_bytes"),
    ("consumer", "test", "${#checked_supervisor_transport_output[@]}", "-eq", "1"),
    ("consumer", "test", "${checked_supervisor_transport_output[0]}", "=", "/dev"),
    ("producer", "read_checked_runtime_transport_file", "$writable_mount_records_file", "$writable_mount_records_max_bytes"),
    ("consumer", "test", "$(( ${#checked_runtime_transport_output[@]} % 2 ))", "-eq", "0"),
    ("consumer", "for", "((index=0; index < ${#checked_runtime_transport_output[@]}; index+=2))"),
    ("consumer", "mount_target=${checked_runtime_transport_output[index]}"),
    ("consumer", "mount_options=${checked_runtime_transport_output[index + 1]}"),
)
_REVIEWED_BUILDER_SECURITY_BUILTINS = frozenset(
    {
        "cd",
        "exec",
        "exit",
        "local",
        "mapfile",
        "printf",
        "return",
        "set",
        "test",
        "trap",
        "ulimit",
        "wait",
    }
)
_SECURITY_SENSITIVE_FUNCTION_NAMES = _REVIEWED_BUILDER_SECURITY_BUILTINS | frozenset(
    {
        "alias",
        "builtin",
        "case",
        "command",
        "coproc",
        "declare",
        "enable",
        "env",
        "eval",
        "export",
        "hash",
        "getopts",
        "local",
        "mapfile",
        "mount",
        "printf",
        "read",
        "readarray",
        "readonly",
        "set",
        "shift",
        "shopt",
        "sort",
        "source",
        "stat",
        "test",
        "typeset",
        "unalias",
        "unset",
        "wait",
    }
)
_ANALYZED_BUILDER_COMMANDS = frozenset(
    {
        "alias",
        "builtin",
        "case",
        "cd",
        "command",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "declare",
        "echo",
        "enable",
        "eval",
        "exec",
        "export",
        "fi",
        "for",
        "hash",
        "if",
        "in",
        "local",
        "mapfile",
        "printf",
        "read",
        "readarray",
        "readonly",
        "return",
        "set",
        "shopt",
        "source",
        "test",
        "trap",
        "typeset",
        "ulimit",
        "unalias",
        "unset",
        "until",
        "while",
    }
)
_RAW_CGROUP_DYNAMIC_FILENAME_MARKERS = (
    "$",
    "`",
    "*",
    "?",
    "[",
    "]",
    "{",
    "}",
    "~",
    "<(",
    ">(",
    "+(",
    "@(",
    "!(",
)


@dataclass(frozen=True)
class _ShellToken:
    text: str
    has_shell_syntax: bool
    segments: tuple[tuple[str, bool, bool], ...] = ()


@dataclass(frozen=True)
class _ShellCommandRecord:
    text: str
    preceding_operator: str | None
    following_operator: str | None
    execution_scopes: tuple[str, ...] = ()
    control_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _BashLineLexState:
    quote: str = "normal"
    parameter_depth: int = 0
    arithmetic_depth: int = 0
    arithmetic_kind: str | None = None
    arithmetic_bracket_depth: int = 0
    array_bracket_depth: int = 0
    execution_stack: tuple[
        tuple[str, str, bool, int, str | None, int],
        ...,
    ] = ()
    word_start: bool = True


@dataclass(frozen=True)
class _FlockCommandTarget:
    mode: str
    argv_index: int | None = None
    command_text: str | None = None


def reviewed_patch_release_run_sha256(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def assert_reviewed_patch_release_run_script_identity(
    script: str,
    *,
    label: str,
) -> None:
    actual = reviewed_patch_release_run_sha256(script)
    if actual != REVIEWED_PATCH_RELEASE_RUN_SHA256:
        raise ValueError(
            f"{label} raw identity differs from the reviewed security boundary"
        )


def reviewed_builder_isolation_sha256(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def assert_reviewed_builder_isolation_shell_identity(
    script: str,
    *,
    label: str,
) -> None:
    actual = reviewed_builder_isolation_sha256(script)
    if actual != REVIEWED_BUILDER_ISOLATION_SHA256:
        raise ValueError(
            f"{label} raw identity differs from the reviewed security boundary"
        )


def _parse_literal_style(style: str, *, label: str) -> tuple[int | None, str]:
    if not style.startswith("|"):
        raise ValueError(f"{label} must use a literal run block")
    indent_indicator: int | None = None
    chomping = ""
    for character in style[1:]:
        if character in "+-":
            if chomping:
                raise ValueError(f"{label} literal run block indicators differ")
            chomping = character
            continue
        if character in "123456789":
            if indent_indicator is not None:
                raise ValueError(f"{label} literal run block indicators differ")
            indent_indicator = int(character)
            continue
        raise ValueError(f"{label} literal run block indicators differ")
    return indent_indicator, chomping


def _normalized_line_parts(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\n"
    return line, ""


def _leading_space_count(text: str, *, label: str) -> int:
    index = 0
    while index < len(text) and text[index] == " ":
        index += 1
    if index < len(text) and text[index] == "\t":
        raise ValueError(f"{label} run block uses tab indentation")
    return index


def literal_run_script_from_step_block(step_block: str, *, label: str) -> str:
    lines = step_block.splitlines(keepends=True)
    headers = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := _LITERAL_RUN_HEADER_RE.match(line))
    ]
    if len(headers) != 1:
        raise ValueError(f"{label} must use exactly one direct literal run block")

    header_index, header_match = headers[0]
    explicit_indent, chomping = _parse_literal_style(
        header_match.group("style"),
        label=label,
    )
    content_lines = lines[header_index + 1 :]
    if explicit_indent is None:
        leading_blank_indent = 0
        content_indent = None
        for line in content_lines:
            raw_line, _line_break = _normalized_line_parts(line)
            leading_spaces = _leading_space_count(raw_line, label=label)
            if raw_line[leading_spaces:] == "":
                leading_blank_indent = max(leading_blank_indent, leading_spaces)
                continue
            content_indent = max(leading_blank_indent, leading_spaces)
            break
        if content_indent is None:
            content_indent = leading_blank_indent
    else:
        content_indent = len(header_match.group("indent")) + explicit_indent

    chunks: list[str] = []
    for line in content_lines:
        raw_line, line_break = _normalized_line_parts(line)
        leading_spaces = _leading_space_count(raw_line, label=label)
        content = raw_line[leading_spaces:]
        if content == "":
            if leading_spaces >= content_indent:
                chunks.append(raw_line[content_indent:])
            else:
                chunks.append("")
        else:
            if leading_spaces < content_indent:
                raise ValueError(f"{label} literal run block indentation differs")
            chunks.append(raw_line[content_indent:])
        if line_break:
            chunks.append("\n")

    script = "".join(chunks)
    if chomping == "-":
        return script.rstrip("\n")
    if chomping == "+":
        return script
    if not script:
        return ""
    return script.rstrip("\n") + "\n"


def builder_isolation_shell_source(run_script: str, *, label: str) -> str:
    opener = "<<'BUILDER_ISOLATION'\n"
    if run_script.count(opener) != 1:
        raise ValueError(f"{label} builder isolation heredoc count differs")
    start = run_script.index(opener) + len(opener)
    terminator = "\nBUILDER_ISOLATION\n"
    if run_script.count(terminator) != 1:
        raise ValueError(f"{label} builder isolation heredoc terminator differs")
    end = run_script.index(terminator, start)
    return run_script[start:end] + "\n"


def _decode_ansi_c_escape(
    text: str,
    index: int,
) -> tuple[str, int]:
    if index + 1 >= len(text):
        raise ValueError("unterminated ANSI-C escape")
    escape = text[index + 1]
    simple = {
        "a": "\a",
        "b": "\b",
        "e": "\x1b",
        "E": "\x1b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "\\": "\\",
        "'": "'",
        '"': '"',
        "?": "?",
    }
    if escape in simple:
        return simple[escape], index + 2
    if escape == "c" and index + 2 < len(text):
        return chr(ord(text[index + 2]) & 0x1F), index + 3
    if escape in "xXuU":
        widths = {"x": 2, "X": 2, "u": 4, "U": 8}
        width = widths[escape]
        end = index + 2
        while (
            end < len(text)
            and end < index + 2 + width
            and text[end] in "0123456789abcdefABCDEF"
        ):
            end += 1
        if end == index + 2:
            return "\\" + escape, index + 2
        return chr(int(text[index + 2 : end], 16)), end
    if escape in "01234567":
        end = index + 2
        while (
            end < len(text)
            and end < index + 4
            and text[end] in "01234567"
        ):
            end += 1
        return chr(int(text[index + 1 : end], 8)), end
    return "\\" + escape, index + 2


def _scan_bash_physical_line(
    line: str,
    state: _BashLineLexState,
) -> tuple[_BashLineLexState, bool, int | None]:
    quote = state.quote
    parameter_depth = state.parameter_depth
    arithmetic_depth = state.arithmetic_depth
    arithmetic_kind = state.arithmetic_kind
    arithmetic_bracket_depth = state.arithmetic_bracket_depth
    array_bracket_depth = state.array_bracket_depth
    execution_stack = list(state.execution_stack)
    word_start = state.word_start

    def current_state() -> _BashLineLexState:
        return _BashLineLexState(
            quote=quote,
            parameter_depth=parameter_depth,
            arithmetic_depth=arithmetic_depth,
            arithmetic_kind=arithmetic_kind,
            arithmetic_bracket_depth=arithmetic_bracket_depth,
            array_bracket_depth=array_bracket_depth,
            execution_stack=tuple(execution_stack),
            word_start=word_start,
        )

    def open_execution(kind: str, closes_word: bool) -> None:
        nonlocal arithmetic_bracket_depth, arithmetic_depth
        nonlocal arithmetic_kind, quote, word_start
        execution_stack.append(
            (
                kind,
                quote,
                closes_word,
                arithmetic_depth,
                arithmetic_kind,
                arithmetic_bracket_depth,
            )
        )
        quote = "normal"
        arithmetic_depth = 0
        arithmetic_kind = None
        arithmetic_bracket_depth = 0
        word_start = True

    def close_execution(expected: set[str]) -> bool:
        nonlocal arithmetic_bracket_depth, arithmetic_depth
        nonlocal arithmetic_kind, quote, word_start
        if not execution_stack or execution_stack[-1][0] not in expected:
            return False
        (
            _kind,
            restore_quote,
            closes_word,
            restore_arithmetic_depth,
            restore_arithmetic_kind,
            restore_arithmetic_bracket_depth,
        ) = execution_stack.pop()
        quote = restore_quote
        arithmetic_depth = restore_arithmetic_depth
        arithmetic_kind = restore_arithmetic_kind
        arithmetic_bracket_depth = restore_arithmetic_bracket_depth
        word_start = closes_word
        return True

    index = 0
    while index < len(line):
        character = line[index]
        if quote == "normal":
            if array_bracket_depth:
                if character == "\\":
                    if index == len(line) - 1:
                        return current_state(), True, None
                    index += 2
                    continue
                if line.startswith("$'", index):
                    quote = "ansi"
                    index += 2
                    continue
                if line.startswith('$"', index):
                    quote = "locale"
                    index += 2
                    continue
                if character in {"'", '"'}:
                    quote = "single" if character == "'" else "double"
                elif character == "[":
                    array_bracket_depth += 1
                elif character == "]":
                    array_bracket_depth -= 1
                index += 1
                continue
            if arithmetic_depth or arithmetic_bracket_depth:
                if character == "\\":
                    if index == len(line) - 1:
                        return current_state(), True, None
                    index += 2
                    continue
                if line.startswith("$'", index):
                    quote = "ansi"
                    index += 2
                    continue
                if line.startswith('$"', index):
                    quote = "locale"
                    index += 2
                    continue
                if line.startswith("$((", index):
                    arithmetic_depth += 2
                    index += 3
                    continue
                if line.startswith("$[", index):
                    arithmetic_bracket_depth += 1
                    index += 2
                    continue
                if line.startswith("$(", index):
                    open_execution("command-substitution", False)
                    index += 2
                    continue
                if character == "`":
                    open_execution("backtick-substitution", False)
                    index += 1
                    continue
                if arithmetic_depth:
                    if character == "(":
                        arithmetic_depth += 1
                    elif character == ")":
                        arithmetic_depth -= 1
                        if arithmetic_depth == 0:
                            word_start = arithmetic_kind == "command"
                            arithmetic_kind = None
                elif character == "]":
                    arithmetic_bracket_depth -= 1
                index += 1
                continue
            if character in " \t":
                word_start = True
            elif (
                character == "#"
                and word_start
                and parameter_depth == 0
            ):
                return current_state(), False, index
            elif line.startswith("$'", index):
                quote = "ansi"
                word_start = False
                index += 2
                continue
            elif line.startswith('$"', index):
                quote = "locale"
                word_start = False
                index += 2
                continue
            elif line.startswith("$((", index):
                arithmetic_depth = 2
                arithmetic_kind = "expansion"
                word_start = False
                index += 3
                continue
            elif line.startswith("((", index):
                arithmetic_depth = 2
                arithmetic_kind = "command"
                word_start = False
                index += 2
                continue
            elif line.startswith("$[", index):
                arithmetic_bracket_depth = 1
                word_start = False
                index += 2
                continue
            elif line.startswith("$(", index):
                open_execution("command-substitution", False)
                index += 2
                continue
            elif (
                character in "<>"
                and index + 1 < len(line)
                and line[index + 1] == "("
            ):
                open_execution("process-substitution", False)
                index += 2
                continue
            elif character == "`":
                if not close_execution({"backtick-substitution"}):
                    open_execution("backtick-substitution", False)
                index += 1
                continue
            elif character == "(":
                open_execution("subshell", True)
                index += 1
                continue
            elif character == ")":
                if not close_execution(
                    {
                        "command-substitution",
                        "process-substitution",
                        "subshell",
                    }
                ):
                    word_start = True
                index += 1
                continue
            elif character == "[" and not word_start:
                array_bracket_depth = 1
                word_start = False
            elif (
                character == "$"
                and index + 1 < len(line)
                and line[index + 1] == "{"
            ):
                parameter_depth += 1
                word_start = False
                index += 2
                continue
            elif character == "}" and parameter_depth:
                parameter_depth -= 1
                word_start = False
            elif character == "'":
                quote = "single"
                word_start = False
            elif character == '"':
                quote = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return current_state(), True, None
                index += 2
                word_start = False
                continue
            elif character in "&|;" and parameter_depth == 0:
                if (
                    character in "&|"
                    and index + 1 < len(line)
                    and line[index + 1] == character
                ):
                    index += 1
                word_start = True
            elif character in "()<>":
                word_start = True
            else:
                word_start = False
        elif quote == "single":
            if character == "'":
                quote = "normal"
        elif quote == "ansi":
            if character == "\\":
                if index == len(line) - 1:
                    return current_state(), True, None
                index += 2
                continue
            if character == "'":
                quote = "normal"
        else:
            if character == '"':
                quote = "normal"
            elif character == "\\":
                if index == len(line) - 1:
                    return current_state(), True, None
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
            elif line.startswith("$((", index):
                if arithmetic_depth == 0:
                    arithmetic_kind = "expansion"
                arithmetic_depth += 2
                index += 3
                continue
            elif line.startswith("$(", index):
                open_execution("command-substitution", False)
                index += 2
                continue
            elif line.startswith("$[", index):
                arithmetic_bracket_depth += 1
                index += 2
                continue
            elif arithmetic_depth:
                if character == "(":
                    arithmetic_depth += 1
                elif character == ")":
                    arithmetic_depth -= 1
                    if arithmetic_depth == 0:
                        word_start = arithmetic_kind == "command"
                        arithmetic_kind = None
            elif arithmetic_bracket_depth and character == "]":
                arithmetic_bracket_depth -= 1
            elif character == "`":
                open_execution("backtick-substitution", False)
                index += 1
                continue
            elif (
                character == "$"
                and index + 1 < len(line)
                and line[index + 1] == "{"
            ):
                parameter_depth += 1
                index += 2
                continue
            elif character == "}" and parameter_depth:
                parameter_depth -= 1
        index += 1
    return (
        current_state(),
        False,
        None,
    )


def bash_logical_lines(script: str, *, label: str) -> tuple[str, ...]:
    state = _BashLineLexState()
    logical_lines: list[str] = []
    current = ""
    for line in script.splitlines():
        state, continued, comment_index = _scan_bash_physical_line(
            line,
            state,
        )
        current += line if comment_index is None else line[:comment_index]
        if continued:
            current = current[:-1]
            continue
        if (
            state.quote != "normal"
            or state.parameter_depth
            or state.arithmetic_depth
            or state.arithmetic_bracket_depth
            or state.array_bracket_depth
            or any(
                restore_quote != "normal"
                for _kind, restore_quote, _boundary, *_arithmetic
                in state.execution_stack
            )
        ):
            current += "\n"
            continue
        logical_lines.append(current)
        current = ""
        if state.execution_stack:
            state = _BashLineLexState(
                execution_stack=state.execution_stack,
                word_start=True,
            )
        else:
            state = _BashLineLexState()
    if current or state.execution_stack:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    return tuple(logical_lines)


def _logical_heredoc_declarations(
    logical: str,
    *,
    label: str,
) -> tuple[tuple[str, bool, bool], ...]:
    declarations: list[tuple[str, bool, bool]] = []
    quote: str | None = None
    parameter_depth = 0
    arithmetic_depth = 0
    arithmetic_kind: str | None = None
    arithmetic_bracket_depth = 0
    array_bracket_depth = 0
    word_start = True
    index = 0
    while index < len(logical):
        character = logical[index]
        if quote is None:
            if array_bracket_depth:
                if character == "\\":
                    index += 2
                    continue
                if character in {"'", '"'}:
                    quote = character
                elif character == "[":
                    array_bracket_depth += 1
                elif character == "]":
                    array_bracket_depth -= 1
                index += 1
                continue
            if character in " \t":
                word_start = True
                index += 1
                continue
            if character == "\\":
                index += 2
                word_start = False
                continue
            if arithmetic_depth or arithmetic_bracket_depth:
                if logical.startswith("$((", index):
                    arithmetic_depth += 2
                    index += 3
                    continue
                if logical.startswith("$[", index):
                    arithmetic_bracket_depth += 1
                    index += 2
                    continue
                if arithmetic_depth:
                    if character == "(":
                        arithmetic_depth += 1
                    elif character == ")":
                        arithmetic_depth -= 1
                        if arithmetic_depth == 0:
                            word_start = arithmetic_kind == "command"
                            arithmetic_kind = None
                elif character == "]":
                    arithmetic_bracket_depth -= 1
                index += 1
                continue
            if logical.startswith("$'", index):
                quote = "ansi"
                word_start = False
                index += 2
                continue
            if logical.startswith('$"', index):
                quote = "locale"
                word_start = False
                index += 2
                continue
            if logical.startswith("$((", index):
                arithmetic_depth = 2
                arithmetic_kind = "expansion"
                word_start = False
                index += 3
                continue
            if logical.startswith("((", index):
                arithmetic_depth = 2
                arithmetic_kind = "command"
                word_start = False
                index += 2
                continue
            if logical.startswith("$[", index):
                arithmetic_bracket_depth = 1
                word_start = False
                index += 2
                continue
            if character in {"'", '"'}:
                quote = character
                word_start = False
                index += 1
                continue
            if logical.startswith("${", index):
                parameter_depth += 1
                word_start = False
                index += 2
                continue
            if character == "}" and parameter_depth:
                parameter_depth -= 1
                word_start = False
                index += 1
                continue
            if character == "[" and not word_start:
                array_bracket_depth = 1
                index += 1
                continue
            if (
                parameter_depth
                or character != "<"
                or not logical.startswith("<<", index)
            ):
                index += 1
                word_start = character in "&|;()<>"
                continue
            if logical.startswith("<<<", index):
                index += 3
                continue
            strip_tabs = logical.startswith("<<-", index)
            index += 3 if strip_tabs else 2
            while index < len(logical) and logical[index] in " \t":
                index += 1
            delimiter: list[str] = []
            delimiter_quote: str | None = None
            quoted = False
            while index < len(logical):
                character = logical[index]
                if delimiter_quote is None:
                    if character in " \t;&|<>":
                        break
                    if logical.startswith("$'", index):
                        delimiter_quote = "ansi"
                        quoted = True
                        index += 2
                        continue
                    if logical.startswith('$"', index):
                        delimiter_quote = "locale"
                        quoted = True
                        index += 2
                        continue
                    if character in {"'", '"'}:
                        delimiter_quote = character
                        quoted = True
                        index += 1
                        continue
                    if character == "\\":
                        if index + 1 >= len(logical):
                            raise ValueError(
                                f"{label} heredoc delimiter escape differs"
                            )
                        quoted = True
                        delimiter.append(logical[index + 1])
                        index += 2
                        continue
                    delimiter.append(character)
                    index += 1
                    continue
                if (
                    delimiter_quote == "ansi"
                    and character == "\\"
                ):
                    decoded, index = _decode_ansi_c_escape(
                        logical,
                        index,
                    )
                    delimiter.append(decoded)
                    continue
                closing_quote = {
                    "ansi": "'",
                    "locale": '"',
                }.get(delimiter_quote, delimiter_quote)
                if character == closing_quote:
                    delimiter_quote = None
                elif (
                    delimiter_quote in {'"', "locale"}
                    and character == "\\"
                    and index + 1 < len(logical)
                    and logical[index + 1] in '$`"\\'
                ):
                    quoted = True
                    delimiter.append(logical[index + 1])
                    index += 2
                    continue
                else:
                    delimiter.append(character)
                index += 1
            if delimiter_quote is not None or not delimiter:
                raise ValueError(f"{label} heredoc delimiter differs")
            declarations.append(
                ("".join(delimiter), strip_tabs, not quoted)
            )
            word_start = False
            continue
        if quote == "ansi":
            if character == "\\":
                _decoded, index = _decode_ansi_c_escape(
                    logical,
                    index,
                )
                continue
            if character == "'":
                quote = None
            index += 1
            continue
        closing_quote = '"' if quote == "locale" else quote
        if character == closing_quote:
            quote = None
        elif (
            quote in {'"', "locale"}
            and character == "\\"
            and index + 1 < len(logical)
            and logical[index + 1] in '$`"\\'
        ):
            index += 2
            continue
        elif logical.startswith("${", index):
            parameter_depth += 1
            index += 2
            continue
        elif character == "}" and parameter_depth:
            parameter_depth -= 1
        index += 1
    if (
        quote is not None
        or parameter_depth
        or arithmetic_depth
        or arithmetic_bracket_depth
        or array_bracket_depth
    ):
        raise ValueError(f"{label} heredoc command quoting differs")
    return tuple(declarations)


def _unquoted_heredoc_has_active_expansion(body: str) -> bool:
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\" and index + 1 < len(body):
            if body[index + 1] in "$`\\":
                index += 2
                continue
        if character in "$`":
            return True
        index += 1
    return False


def _strip_generic_heredoc_bodies(script: str, *, label: str) -> str:
    lines = script.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    state = _BashLineLexState()
    while index < len(lines):
        logical = ""
        while index < len(lines):
            physical = lines[index]
            raw_line = physical.rstrip("\r\n")
            output.append(physical)
            state, continued, comment_index = _scan_bash_physical_line(
                raw_line,
                state,
            )
            logical += (
                raw_line
                if comment_index is None
                else raw_line[:comment_index]
            )
            index += 1
            if continued:
                logical = logical[:-1]
                continue
            if (
                state.quote != "normal"
                or state.parameter_depth
                or state.arithmetic_depth
                or state.arithmetic_bracket_depth
                or state.array_bracket_depth
            ):
                logical += "\n"
                continue
            break
        if (
            state.quote != "normal"
            or state.parameter_depth
            or state.arithmetic_depth
            or state.arithmetic_bracket_depth
            or state.array_bracket_depth
        ):
            raise ValueError(f"{label} has unterminated command quoting")
        declarations = _logical_heredoc_declarations(
            logical,
            label=label,
        )
        for delimiter, strip_tabs, expansion_active in declarations:
            body_lines: list[str] = []
            while index < len(lines):
                physical = lines[index]
                raw_line = physical.rstrip("\r\n")
                comparable = raw_line.lstrip("\t") if strip_tabs else raw_line
                index += 1
                if comparable == delimiter:
                    break
                body_lines.append(physical)
            else:
                raise ValueError(
                    f"{label} has unterminated heredoc {delimiter!r}"
                )
            body = "".join(body_lines)
            if (
                expansion_active
                and _unquoted_heredoc_has_active_expansion(body)
            ):
                raise ValueError(
                    f"{label} has expansion-active heredoc body"
                )
        if state.execution_stack:
            state = _BashLineLexState(
                execution_stack=state.execution_stack,
                word_start=True,
            )
        else:
            state = _BashLineLexState()
    if state.execution_stack:
        raise ValueError(f"{label} has unterminated execution scope")
    return "".join(output)


def split_bash_command_records(
    script: str,
    *,
    label: str,
) -> tuple[_ShellCommandRecord, ...]:
    script = _strip_generic_heredoc_bodies(script, label=label)
    records: list[_ShellCommandRecord] = []
    pending_operator: str | None = None
    scope_stack: list[tuple[str, str | None]] = []
    backtick_active = False
    for logical in bash_logical_lines(script, label=label):
        current: list[str] = []
        quote: str | None = None
        word_start = True
        record_scopes: tuple[str, ...] | None = None
        arithmetic_depth = 0
        arithmetic_bracket_depth = 0
        index = 0

        def emit(following_operator: str | None) -> None:
            nonlocal current, pending_operator, record_scopes, word_start
            command = "".join(current).strip()
            if command:
                records.append(
                    _ShellCommandRecord(
                        text=command,
                        preceding_operator=pending_operator,
                        following_operator=following_operator,
                        execution_scopes=record_scopes or (),
                    )
                )
                pending_operator = following_operator
            elif following_operator is not None:
                pending_operator = following_operator
            current = []
            record_scopes = None
            word_start = True

        def begin_command() -> None:
            nonlocal record_scopes
            if record_scopes is None:
                record_scopes = tuple(
                    scope_name for scope_name, _owner in scope_stack
                )

        while index < len(logical):
            character = logical[index]
            if quote is None:
                if arithmetic_depth or arithmetic_bracket_depth:
                    begin_command()
                    current.append(character)
                    if character == "\\" and index + 1 < len(logical):
                        current.append(logical[index + 1])
                        index += 2
                        continue
                    if logical.startswith("$((", index):
                        current.extend(("((",))
                        arithmetic_depth += 2
                        index += 3
                        continue
                    if logical.startswith("$[", index):
                        current.append("[")
                        arithmetic_bracket_depth += 1
                        index += 2
                        continue
                    if arithmetic_depth:
                        if character == "(":
                            arithmetic_depth += 1
                        elif character == ")":
                            arithmetic_depth -= 1
                    elif character == "]":
                        arithmetic_bracket_depth -= 1
                    index += 1
                    continue
                if character in " \t":
                    current.append(character)
                    word_start = True
                elif character == "#" and word_start:
                    break
                elif logical.startswith("$'", index):
                    begin_command()
                    current.extend(("$", "'"))
                    quote = "ansi"
                    word_start = False
                    index += 2
                    continue
                elif logical.startswith('$"', index):
                    begin_command()
                    current.extend(("$", '"'))
                    quote = "locale"
                    word_start = False
                    index += 2
                    continue
                elif logical.startswith("$((", index):
                    begin_command()
                    current.extend(("$", "(", "("))
                    arithmetic_depth = 2
                    word_start = False
                    index += 3
                    continue
                elif logical.startswith("((", index):
                    begin_command()
                    current.extend(("(", "("))
                    arithmetic_depth = 2
                    word_start = False
                    index += 2
                    continue
                elif logical.startswith("$[", index):
                    begin_command()
                    current.extend(("$", "["))
                    arithmetic_bracket_depth = 1
                    word_start = False
                    index += 2
                    continue
                elif character == "'":
                    begin_command()
                    current.append(character)
                    quote = "'"
                    word_start = False
                elif character == '"':
                    begin_command()
                    current.append(character)
                    quote = '"'
                    word_start = False
                elif character == "\\":
                    if index + 1 >= len(logical):
                        raise ValueError(
                            f"{label} shell operator escape differs"
                        )
                    begin_command()
                    current.append(character)
                    current.append(logical[index + 1])
                    index += 1
                    word_start = False
                elif character == "`":
                    begin_command()
                    current.append(character)
                    if backtick_active:
                        if (
                            not scope_stack
                            or scope_stack[-1]
                            != ("backtick-substitution", None)
                        ):
                            raise ValueError(
                                f"{label} backtick scope differs"
                            )
                        scope_stack.pop()
                    else:
                        scope_stack.append(
                            ("backtick-substitution", None)
                        )
                    backtick_active = not backtick_active
                    word_start = False
                elif (
                    character in "$<>"
                    and index + 1 < len(logical)
                    and logical[index + 1] == "("
                ):
                    begin_command()
                    current.append(character)
                    current.append("(")
                    scope_stack.append(
                        (
                            "command-substitution"
                            if character == "$"
                            else "process-substitution",
                            None,
                        )
                    )
                    index += 1
                    word_start = False
                elif character == "(":
                    begin_command()
                    current.append(character)
                    if (
                        index + 1 < len(logical)
                        and logical[index + 1] == ")"
                        and re.search(
                            r"(?:[A-Za-z_][A-Za-z0-9_]*|function\s+"
                            r"[A-Za-z_][A-Za-z0-9_]*)\s*$",
                            "".join(current[:-1]),
                        )
                    ):
                        current.append(")")
                        index += 1
                    else:
                        scope_stack.append(("subshell", None))
                    word_start = False
                elif character == ")":
                    begin_command()
                    current.append(character)
                    if (
                        scope_stack
                        and scope_stack[-1][0]
                        in {
                            "command-substitution",
                            "process-substitution",
                            "subshell",
                        }
                        and scope_stack[-1][1] is None
                    ):
                        scope_stack.pop()
                    word_start = False
                elif character in "&|;":
                    operator = character
                    if index + 1 < len(logical):
                        pair = logical[index : index + 2]
                        if pair in {"&&", "||", "|&"}:
                            operator = pair
                            index += 1
                    if operator in _CONTROL_OPERATORS:
                        emit(operator)
                    else:
                        current.append(operator)
                        word_start = False
                else:
                    begin_command()
                    current.append(character)
                    word_start = False
            elif quote == "'":
                current.append(character)
                if character == "'":
                    quote = None
            elif quote == "ansi":
                current.append(character)
                if character == "\\" and index + 1 < len(logical):
                    current.append(logical[index + 1])
                    index += 1
                elif character == "'":
                    quote = None
            else:
                current.append(character)
                if character == "\\" and index + 1 < len(logical):
                    current.append(logical[index + 1])
                    index += 1
                elif character == "`":
                    if backtick_active:
                        if (
                            not scope_stack
                            or scope_stack[-1]
                            != ("backtick-substitution", quote)
                        ):
                            raise ValueError(
                                f"{label} backtick scope differs"
                            )
                        scope_stack.pop()
                    else:
                        scope_stack.append(
                            ("backtick-substitution", quote)
                        )
                    backtick_active = not backtick_active
                elif logical.startswith("$((", index):
                    current.extend(("(", "("))
                    arithmetic_depth += 2
                    index += 2
                elif logical.startswith("$[", index):
                    current.append("[")
                    arithmetic_bracket_depth += 1
                    index += 1
                elif arithmetic_depth:
                    if character == "(":
                        arithmetic_depth += 1
                    elif character == ")":
                        arithmetic_depth -= 1
                elif arithmetic_bracket_depth and character == "]":
                    arithmetic_bracket_depth -= 1
                elif (
                    character == "$"
                    and index + 1 < len(logical)
                    and logical[index + 1] == "("
                ):
                    current.append("(")
                    scope_stack.append(
                        ("command-substitution", quote)
                    )
                    index += 1
                elif character == ")":
                    if (
                        scope_stack
                        and scope_stack[-1][0]
                        in {"command-substitution", "subshell"}
                        and scope_stack[-1][1] == quote
                    ):
                        scope_stack.pop()
                elif character == '"':
                    quote = None
            index += 1
        emit(None)
    if scope_stack or backtick_active:
        raise ValueError(f"{label} has unbalanced execution scope")
    return tuple(records)


def split_bash_simple_command_strings(script: str, *, label: str) -> tuple[str, ...]:
    return tuple(
        record.text
        for record in split_bash_command_records(script, label=label)
    )


def _mandatory_action_context_is_unconditional(
    record: _ShellCommandRecord,
    *,
    control_stack: list[str],
    function_stack: list[str],
    require_production_helpers: bool,
) -> bool:
    if (
        control_stack
        or record.control_scopes
        or record.execution_scopes
    ):
        return False
    if any(
        operator in {"&&", "||", "|", "|&", "&"}
        for operator in (
            record.preceding_operator,
            record.following_operator,
        )
    ):
        return False
    return (
        not require_production_helpers
        or function_stack == ["builder_main"]
    )


def raw_patch_release_parser_sources(script: str) -> tuple[tuple[str, str], ...]:
    matches = list(_PATCH_RELEASE_PARSER_HEREDOC_RE.finditer(script))
    membership_matches = list(
        _PATCH_RELEASE_MEMBERSHIP_CHECKER_RE.finditer(script)
    )
    if script.count(PATCH_RELEASE_PARSER_HEREDOC_INTRODUCER) != len(
        PATCH_RELEASE_PARSER_HEREDOC_NAMES
    ):
        raise ValueError("patch-release parser heredoc count differs")
    if len(matches) != len(PATCH_RELEASE_PARSER_HEREDOC_NAMES):
        raise ValueError("patch-release parser heredoc structure differs")
    if (
        script.count(PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER) != 1
        or len(membership_matches) != 1
    ):
        raise ValueError(
            "patch-release membership checker heredoc structure differs"
        )
    names = tuple(match.group("name") for match in matches)
    if names != PATCH_RELEASE_PARSER_HEREDOC_NAMES:
        raise ValueError("patch-release parser function association differs")
    sources = tuple(
        (match.group("name"), match.group("body") + "\n")
        for match in matches
    )
    return sources + (
        (
            PATCH_RELEASE_MEMBERSHIP_CHECKER_NAME,
            membership_matches[0].group("body") + "\n",
        ),
    )


def validate_patch_release_parser_heredocs(script: str, *, label: str) -> None:
    for name, body in raw_patch_release_parser_sources(script):
        try:
            tree = ast.parse(body)
        except SyntaxError as error:
            raise ValueError(
                f"{label} patch-release parser Python body is invalid"
            ) from error
        if name == PATCH_RELEASE_MEMBERSHIP_CHECKER_NAME and ast.dump(
            tree,
            include_attributes=False,
        ) != ast.dump(
            ast.parse(_PATCH_RELEASE_MEMBERSHIP_CHECKER_SOURCE),
            include_attributes=False,
        ):
            raise ValueError(
                f"{label} patch-release membership checker differs"
            )


def _strip_patch_release_parser_heredoc_bodies(script: str) -> str:
    stripped = _PATCH_RELEASE_PARSER_HEREDOC_RE.sub(
        lambda match: (
            f"{match.group('name')}() {{\n"
            f"{PATCH_RELEASE_PARSER_HEREDOC_INTRODUCER}\n"
            "PY\n"
            "}\n"
        ),
        script,
    )
    return _PATCH_RELEASE_MEMBERSHIP_CHECKER_RE.sub(
        f"{PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER}\nPY",
        stripped,
    )


def _raw_token_has_glob_bracket(text: str, *, start_index: int) -> bool:
    quote: str | None = None
    index = start_index + 1
    while index < len(text):
        character = text[index]
        if quote is None:
            if character in " \t":
                return False
            if character == "\\" and index + 1 < len(text):
                index += 2
                continue
            if character in "\"'":
                quote = character
                index += 1
                continue
            if character == "]":
                return True
            index += 1
            continue
        if quote == "'":
            if character == "'":
                quote = None
            index += 1
            continue
        if character == "\\" and index + 1 < len(text) and text[index + 1] in '$`"\\':
            index += 2
            continue
        if character == '"':
            quote = None
        index += 1
    return False


def _token_shell_syntax_flags(command_text: str, *, label: str) -> tuple[bool, ...]:
    flags: list[bool] = []
    token_started = False
    token_has_shell_syntax = False
    quote: str | None = None
    index = 0

    while index < len(command_text):
        character = command_text[index]
        if quote is None:
            if character in " \t":
                if token_started:
                    flags.append(token_has_shell_syntax)
                    token_started = False
                    token_has_shell_syntax = False
                index += 1
                continue

            token_started = True

            if command_text.startswith("$'", index):
                quote = "ansi"
                index += 2
                continue
            if command_text.startswith('$"', index):
                quote = "locale"
                index += 2
                continue
            if character == "'":
                quote = "'"
                index += 1
                continue
            if character == '"':
                quote = '"'
                index += 1
                continue
            if character == "\\":
                if index + 1 >= len(command_text):
                    raise ValueError(f"{label} shell token continuation differs")
                index += 2
                continue
            if character in "$`{}*?~":
                token_has_shell_syntax = True
            elif character in "<>" and index + 1 < len(command_text) and command_text[index + 1] == "(":
                token_has_shell_syntax = True
            elif character in "@+!" and index + 1 < len(command_text) and command_text[index + 1] == "(":
                token_has_shell_syntax = True
            elif character == "[" and _raw_token_has_glob_bracket(command_text, start_index=index):
                token_has_shell_syntax = True
            index += 1
            continue

        if quote == "'":
            if character == "'":
                quote = None
            index += 1
            continue
        if quote == "ansi":
            if character == "\\" and index + 1 < len(command_text):
                index += 2
                continue
            if character == "'":
                quote = None
            index += 1
            continue

        if character == "\\" and index + 1 < len(command_text) and command_text[index + 1] in '$`"\\':
            index += 2
            continue
        if character in "$`":
            token_has_shell_syntax = True
        elif character == '"':
            quote = None
        index += 1

    if quote is not None:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    if token_started:
        flags.append(token_has_shell_syntax)
    return tuple(flags)


def _token_quote_segments(
    command_text: str,
    *,
    label: str,
) -> tuple[tuple[tuple[str, bool, bool], ...], ...]:
    tokens: list[tuple[tuple[str, bool, bool], ...]] = []
    segments: list[tuple[str, bool, bool]] = []
    current: list[str] = []
    current_mode: tuple[bool, bool] | None = None
    token_started = False
    quote: str | None = None
    arithmetic_depth = 0
    arithmetic_bracket_depth = 0

    def append(
        character: str,
        expansion_active: bool,
        redirection_active: bool,
    ) -> None:
        nonlocal current_mode
        mode = (expansion_active, redirection_active)
        if current_mode is not None and current_mode != mode:
            segments.append(
                ("".join(current), current_mode[0], current_mode[1])
            )
            current.clear()
        current_mode = mode
        current.append(character)

    def finish_token() -> None:
        nonlocal current_mode, token_started
        if current_mode is not None:
            segments.append(
                ("".join(current), current_mode[0], current_mode[1])
            )
        elif token_started:
            segments.append(("", False, False))
        tokens.append(tuple(segments))
        segments.clear()
        current.clear()
        current_mode = None
        token_started = False

    index = 0
    while index < len(command_text):
        character = command_text[index]
        if quote is None:
            if arithmetic_depth or arithmetic_bracket_depth:
                token_started = True
                if command_text.startswith("$((", index):
                    append("$", True, True)
                    append("(", True, True)
                    append("(", True, True)
                    arithmetic_depth += 2
                    index += 3
                    continue
                if command_text.startswith("$[", index):
                    append("$", True, True)
                    append("[", True, True)
                    arithmetic_bracket_depth += 1
                    index += 2
                    continue
                append(character, True, True)
                if arithmetic_depth:
                    if character == "(":
                        arithmetic_depth += 1
                    elif character == ")":
                        arithmetic_depth -= 1
                elif character == "]":
                    arithmetic_bracket_depth -= 1
                index += 1
                continue
            if character in " \t":
                if token_started:
                    finish_token()
                index += 1
                continue
            token_started = True
            if command_text.startswith("$'", index):
                quote = "ansi"
                index += 2
                continue
            if command_text.startswith('$"', index):
                quote = "locale"
                index += 2
                continue
            if command_text.startswith("$((", index):
                append("$", True, True)
                append("(", True, True)
                append("(", True, True)
                arithmetic_depth = 2
                index += 3
                continue
            if command_text.startswith("((", index):
                append("(", True, True)
                append("(", True, True)
                arithmetic_depth = 2
                index += 2
                continue
            if command_text.startswith("$[", index):
                append("$", True, True)
                append("[", True, True)
                arithmetic_bracket_depth = 1
                index += 2
                continue
            if character == "'":
                quote = "'"
                index += 1
                continue
            if character == '"':
                quote = '"'
                index += 1
                continue
            if character == "\\":
                if index + 1 >= len(command_text):
                    raise ValueError(
                        f"{label} shell token continuation differs"
                    )
                append(command_text[index + 1], False, False)
                index += 2
                continue
            append(character, True, True)
            index += 1
            continue
        if quote == "'":
            if character == "'":
                quote = None
            else:
                append(character, False, False)
            index += 1
            continue
        if quote == "ansi":
            if character == "\\":
                decoded, index = _decode_ansi_c_escape(
                    command_text,
                    index,
                )
                append(decoded, False, False)
                continue
            if character == "'":
                quote = None
            else:
                append(character, False, False)
            index += 1
            continue
        if character == '"':
            quote = None
            index += 1
            continue
        if (
            character == "\\"
            and index + 1 < len(command_text)
            and command_text[index + 1] in '$`"\\'
        ):
            append(command_text[index + 1], False, False)
            index += 2
            continue
        append(character, True, False)
        index += 1
    if quote is not None:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    if token_started:
        finish_token()
    return tuple(tokens)


def _parse_shell_tokens(command_text: str, *, label: str) -> tuple[_ShellToken, ...]:
    segments = _token_quote_segments(command_text, label=label)
    values = tuple(
        "".join(segment for segment, _active, _redirect in token_segments)
        for token_segments in segments
    )
    syntax_flags = tuple(
        any(
            (
                expansion_active
                and any(marker in segment for marker in "$`")
            )
            or (
                redirection_active
                and any(marker in segment for marker in "{}*?~")
            )
            or (
                redirection_active
                and any(
                    marker in segment
                    for marker in (
                        "<(",
                        ">(",
                        "@(",
                        "+(",
                        "!(",
                    )
                )
                or (
                    redirection_active
                    and re.search(r"\[[^\]]+\]", segment) is not None
                )
            )
            for segment, expansion_active, redirection_active in token_segments
        )
        for token_segments in segments
    )
    return tuple(
        _ShellToken(
            text=value,
            has_shell_syntax=has_shell_syntax,
            segments=token_segments,
        )
        for value, has_shell_syntax, token_segments in zip(
            values,
            syntax_flags,
            segments,
        )
    )


def _shell_token_from_text(token_text: str, *, label: str) -> _ShellToken:
    tokens = _parse_shell_tokens(token_text, label=label)
    if len(tokens) != 1:
        raise ValueError(f"{label} shell token boundaries differ")
    return tokens[0]


def _token_texts(tokens: Iterable[_ShellToken]) -> tuple[str, ...]:
    return tuple(token.text for token in tokens)


def _is_redirection_token(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9]*(?:<<<|<<-?|>>|<>|>\||<&|>&|<|>)",
            text,
        )
    )


def _split_attached_redirections(
    tokens: tuple[_ShellToken, ...],
) -> tuple[_ShellToken, ...]:
    lexed: list[_ShellToken] = []
    arithmetic_depth = 0
    arithmetic_bracket_depth = 0

    def make_token(
        characters: list[tuple[str, bool, bool]],
    ) -> _ShellToken:
        segments: list[tuple[str, bool, bool]] = []
        current: list[str] = []
        mode: tuple[bool, bool] | None = None
        for character, expansion_active, redirection_active in characters:
            next_mode = (expansion_active, redirection_active)
            if mode is not None and mode != next_mode:
                segments.append(("".join(current), mode[0], mode[1]))
                current.clear()
            mode = next_mode
            current.append(character)
        if mode is not None:
            segments.append(("".join(current), mode[0], mode[1]))
        text = "".join(character for character, _expand, _redirect in characters)
        return _ShellToken(
            text=text,
            has_shell_syntax=any(
                expansion_active
                and character in "$`{}*?~"
                for character, expansion_active, _redirect in characters
            ),
            segments=tuple(segments),
        )

    for token in tokens:
        characters = [
            (character, expansion_active, redirection_active)
            for segment, expansion_active, redirection_active in token.segments
            for character in segment
        ]
        if not characters:
            lexed.append(token)
            continue
        chunk_start = 0
        index = 0
        split_occurred = False
        while index < len(characters):
            character, _expand, redirection_active = characters[index]
            lookahead = "".join(
                item[0] for item in characters[index : index + 3]
            )
            if arithmetic_depth or arithmetic_bracket_depth:
                if lookahead.startswith("$(("):
                    arithmetic_depth += 2
                    index += 3
                    continue
                if lookahead.startswith("$["):
                    arithmetic_bracket_depth += 1
                    index += 2
                    continue
                if arithmetic_depth:
                    if character == "(":
                        arithmetic_depth += 1
                    elif character == ")":
                        arithmetic_depth -= 1
                elif character == "]":
                    arithmetic_bracket_depth -= 1
                index += 1
                continue
            if redirection_active and lookahead.startswith("$(("):
                arithmetic_depth = 2
                index += 3
                continue
            if redirection_active and lookahead.startswith("(("):
                arithmetic_depth = 2
                index += 2
                continue
            if redirection_active and lookahead.startswith("$["):
                arithmetic_bracket_depth = 1
                index += 2
                continue
            if (
                character not in "<>"
                or not redirection_active
                or (
                    index + 1 < len(characters)
                    and characters[index + 1][0] == "("
                    and characters[index + 1][2]
                )
            ):
                index += 1
                continue
            remaining = "".join(
                item[0] for item in characters[index : index + 3]
            )
            operator = next(
                (
                    candidate
                    for candidate in (
                        "<<<",
                        "<<-",
                        "<<",
                        ">>",
                        "<>",
                        ">|",
                        "<&",
                        ">&",
                        "<",
                        ">",
                    )
                    if remaining.startswith(candidate)
                ),
                None,
            )
            if operator is None:
                index += 1
                continue
            prefix = characters[chunk_start:index]
            descriptor = ""
            if prefix and all(item[0].isdigit() for item in prefix):
                descriptor = "".join(item[0] for item in prefix)
                prefix = []
            if prefix:
                lexed.append(make_token(prefix))
            operator_end = index + len(operator)
            lexed.append(
                _ShellToken(
                    text=descriptor + operator,
                    has_shell_syntax=False,
                    segments=((descriptor + operator, True, True),),
                )
            )
            split_occurred = True
            chunk_start = operator_end
            index = operator_end
        if not split_occurred:
            lexed.append(token)
        elif chunk_start < len(characters):
            lexed.append(make_token(characters[chunk_start:]))
    return tuple(lexed)


def _canonical_literal_path(token: _ShellToken) -> str | None:
    if (
        not token.text
        or not token.text.startswith("/")
        or token.has_shell_syntax
    ):
        return None
    return posixpath.normpath(token.text)


def _token_has_shell_syntax(token: _ShellToken) -> bool:
    return token.has_shell_syntax


def _command_references_supervisor(tokens: Iterable[_ShellToken], command_text: str) -> bool:
    for token in tokens:
        path = _canonical_literal_path(token)
        if path == "/mnt/supervisor" or (
            path is not None and path.startswith("/mnt/supervisor/")
        ):
            return True
        if token.text.startswith("path=$(/usr/bin/mktemp /mnt/supervisor/"):
            return True
    return False


def _is_reviewed_supervisor_command(tokens: tuple[_ShellToken, ...]) -> bool:
    token_texts = _token_texts(tokens)
    return token_texts in APPROVED_SUPERVISOR_COMMAND_TOKENS or (
        len(token_texts) == 1
        and token_texts[0].startswith("path=$(/usr/bin/mktemp /mnt/supervisor/")
    ) or token_texts in {
        ("/usr/bin/stat", "-c", "%u", "/mnt/supervisor"),
        ("/usr/bin/stat", "-c", "%a", "/mnt/supervisor"),
    } or (
        len(token_texts) == 2
        and token_texts[0] == "/usr/bin/mktemp"
        and token_texts[1].startswith("/mnt/supervisor/")
    )


def _is_shell_interpreter_token(token: _ShellToken) -> bool:
    return (
        not _token_has_shell_syntax(token)
        and posixpath.basename(token.text) in _SHELL_INTERPRETER_BASENAMES
    )


def _literal_token_basename(token: _ShellToken) -> str | None:
    if _token_has_shell_syntax(token) or _ASSIGNMENT_RE.fullmatch(token.text):
        return None
    return posixpath.basename(posixpath.normpath(token.text))


def _is_env_executable_token(token: _ShellToken) -> bool:
    return _literal_token_basename(token) == _ENV_BASENAME


def _is_busybox_executable_token(token: _ShellToken) -> bool:
    return _literal_token_basename(token) == _BUSYBOX_BASENAME


def _is_shell_interpreter_reference_token(token: _ShellToken) -> bool:
    if _ASSIGNMENT_RE.fullmatch(token.text):
        return False
    if _token_has_shell_syntax(token):
        return True
    normalized = token.text.strip("\"'`()")
    return posixpath.basename(normalized) in _SHELL_INTERPRETER_BASENAMES


def _literal_token_is_obvious_wrapper_operand(token: _ShellToken) -> bool:
    if _token_has_shell_syntax(token):
        return False
    if _OBVIOUS_WRAPPER_OPERAND_RE.fullmatch(token.text):
        return True
    path = _canonical_literal_path(token)
    return path is not None and path.startswith("/dev/")


def _token_looks_like_short_option_cluster(text: str, option: str) -> bool:
    return text.startswith("-") and not text.startswith("--") and option in text[1:]


def _could_be_env_short_option_token(token: str) -> bool:
    if not token.startswith("-") or token.startswith("--") or token == "-":
        return False
    cluster = token[1:]
    if not cluster:
        return False

    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option == "S":
            return index == len(cluster) - 1
        if option == "i":
            index += 1
            continue
        if option in {"C", "u"}:
            return True
        return False
    return True


def _token_could_start_env_surface(token: _ShellToken) -> bool:
    if _token_has_shell_syntax(token):
        return False
    text = token.text
    return (
        text in _ENV_ZERO_ARG_OPTIONS
        or text in _ENV_OPTIONS_WITH_ARGS
        or text.startswith("--chdir=")
        or text.startswith("--unset=")
        or _reject_env_split_string_option(text)
        or _could_be_env_short_option_token(text)
    )


def _nonliteral_token_starts_shell_c_surface(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> bool:
    if start_index + 1 >= len(tokens):
        return False
    next_text = tokens[start_index + 1].text
    return next_text == "-c" or _token_looks_like_short_option_cluster(next_text, "c")


def _nonliteral_token_starts_env_surface(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> bool:
    if start_index + 1 >= len(tokens):
        return False
    next_token = tokens[start_index + 1]
    if not _token_could_start_env_surface(next_token):
        return False
    next_index, suspicious = _parse_env_execution_segment(
        tokens,
        start_index=start_index + 1,
    )
    return suspicious or next_index is not None


def _nonliteral_token_starts_busybox_env_surface(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> bool:
    if start_index + 2 >= len(tokens):
        return False
    applet = tokens[start_index + 1]
    if not (_token_has_shell_syntax(applet) or _is_env_executable_token(applet)):
        return False
    option_token = tokens[start_index + 2]
    if not _token_could_start_env_surface(option_token):
        return False
    next_index, suspicious = _parse_env_execution_segment(
        tokens,
        start_index=start_index + 2,
    )
    return suspicious or next_index is not None


def _parse_flock_short_option_token(
    token: str,
    *,
    has_next_token: bool,
) -> tuple[str, bool]:
    if not token.startswith("-") or token.startswith("--") or token == "-":
        return "not-short-option", False
    cluster = token[1:]
    if not cluster:
        return "invalid", False

    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option in {"F", "n", "o", "s", "u", "x"}:
            index += 1
            continue
        if option in {"E", "w"}:
            if index == len(cluster) - 1:
                return "next-argument", has_next_token
            return "attached-argument", False
        if option == "c":
            if index == len(cluster) - 1:
                return "command-argument", has_next_token
            return "command-attached", False
        return "invalid", False
    return "zero-argument", False


def _flock_command_string_is_forbidden(command_text: str) -> bool:
    if not command_text:
        return True
    try:
        return _shell_text_has_forbidden_surface(
            command_text,
            label="flock command string",
            allowed_hidden_indices=frozenset(),
        )
    except ValueError:
        return True


def _parse_flock_command_target(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> _FlockCommandTarget | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if current == "--":
            index += 1
            break
        if current in _FLOCK_ZERO_ARG_OPTIONS:
            index += 1
            continue
        if current in _FLOCK_OPTIONS_WITH_ARGS:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if current.startswith("--timeout=") or current.startswith("--conflict-exit-code="):
            index += 1
            continue
        option_kind, has_next_argument = _parse_flock_short_option_token(
            current,
            has_next_token=index + 1 < len(tokens),
        )
        if option_kind == "zero-argument" or option_kind == "attached-argument":
            index += 1
            continue
        if option_kind == "next-argument":
            if not has_next_argument:
                return None
            index += 2
            continue
        if option_kind != "not-short-option":
            return None
        break

    if index >= len(tokens):
        return None

    index += 1
    if index >= len(tokens):
        return _FlockCommandTarget(mode="no-command")

    current = tokens[index].text
    if current == "--":
        index += 1
        if index >= len(tokens):
            return None
        return _FlockCommandTarget(mode="argv", argv_index=index)
    if current in {"-c", "--command"}:
        if index + 1 >= len(tokens) or index + 2 != len(tokens):
            return None
        return _FlockCommandTarget(
            mode="command-string",
            command_text=tokens[index + 1].text,
        )
    if current.startswith("--command="):
        if index + 1 != len(tokens):
            return None
        return _FlockCommandTarget(
            mode="command-string",
            command_text=current.split("=", 1)[1],
        )
    option_kind, _has_next_argument = _parse_flock_short_option_token(
        current,
        has_next_token=index + 1 < len(tokens),
    )
    if option_kind == "command-attached":
        if index + 1 != len(tokens):
            return None
        return _FlockCommandTarget(
            mode="command-string",
            command_text=current[2:],
        )
    if option_kind == "command-argument":
        if index + 1 >= len(tokens) or index + 2 != len(tokens):
            return None
        return _FlockCommandTarget(
            mode="command-string",
            command_text=tokens[index + 1].text,
        )
    return _FlockCommandTarget(mode="argv", argv_index=index)


def _unmodeled_literal_prefix_hides_nonliteral_command_surface(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> bool:
    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if _token_has_shell_syntax(token):
            return (
                _nonliteral_token_starts_shell_c_surface(tokens, start_index=index)
                or _nonliteral_token_starts_env_surface(tokens, start_index=index)
                or _nonliteral_token_starts_busybox_env_surface(tokens, start_index=index)
            )
        if _is_env_executable_token(token) or _is_busybox_executable_token(token):
            return False
        basename = _literal_token_basename(token)
        if basename == "flock":
            flock_target = _parse_flock_command_target(tokens, start_index=index)
            if flock_target is None:
                return True
            if flock_target.mode == "command-string":
                return _flock_command_string_is_forbidden(
                    flock_target.command_text or ""
                )
            if flock_target.mode == "argv":
                index = flock_target.argv_index or len(tokens)
                continue
            return False
        if basename in _WRAPPER_BASENAMES:
            next_index = _next_wrapper_command_index(tokens, start_index=index)
            if next_index is None:
                return True
            index = next_index
            continue
        if token.text == "--" or token.text.startswith("-"):
            index += 1
            continue
        if _literal_token_is_obvious_wrapper_operand(token):
            index += 1
            continue
        return False
    return False


def _strip_command_prefixes(command: tuple[_ShellToken, ...]) -> tuple[_ShellToken, ...]:
    tokens = list(command)
    while tokens:
        stripped = False
        while tokens and tokens[0].text in _SIMPLE_COMMAND_PREFIXES:
            tokens.pop(0)
            stripped = True
        while tokens and _ASSIGNMENT_RE.fullmatch(tokens[0].text):
            tokens.pop(0)
            stripped = True
        if tokens and tokens[0].text == "time":
            tokens.pop(0)
            stripped = True
            if tokens and tokens[0].text == "-p":
                tokens.pop(0)
            continue
        if not stripped:
            break
    return tuple(tokens)


def _is_pure_closing_paren_token(token: _ShellToken) -> bool:
    return bool(token.text) and set(token.text) == {")"}


def _token_could_be_case_pattern(token: _ShellToken) -> bool:
    text = token.text
    return ")" in text or any(
        opener in text
        for opener in ("@(", "!(", "+(", "*(", "?(")
    )


def _case_pattern_payload_tokens(
    token: _ShellToken,
    *,
    label: str,
) -> tuple[_ShellToken, ...] | None:
    text = token.text
    if not text:
        return None

    extglob_depth = 0
    in_bracket = False
    saw_extglob = False
    terminator_index: int | None = None
    index = 0

    while index < len(text):
        character = text[index]
        if in_bracket:
            if character == "]":
                in_bracket = False
            index += 1
            continue
        if character == "[":
            in_bracket = True
            index += 1
            continue
        if character in "@!+*?" and index + 1 < len(text) and text[index + 1] == "(":
            saw_extglob = True
            extglob_depth += 1
            index += 2
            continue
        if character == "(":
            raise ValueError(f"{label} case-pattern token differs")
        if character == ")":
            if extglob_depth:
                extglob_depth -= 1
                index += 1
                continue
            terminator_index = index
            break
        index += 1

    if in_bracket or extglob_depth:
        raise ValueError(f"{label} case-pattern token differs")
    if terminator_index is None:
        if saw_extglob:
            raise ValueError(f"{label} case-pattern token differs")
        return None

    payload = text[terminator_index + 1 :]
    if not payload:
        return ()
    if payload.startswith(")"):
        raise ValueError(f"{label} case-pattern token differs")
    return (_shell_token_from_text(payload, label=label),)


def _semantic_surface_tokens(
    command: tuple[_ShellToken, ...],
    *,
    label: str,
) -> tuple[_ShellToken, ...]:
    tokens = command
    while True:
        tokens = _strip_command_prefixes(tokens)
        if not tokens:
            return ()
        if len(tokens) >= 2 and tokens[0].text.endswith("()") and tokens[1].text == "{":
            if len(tokens) == 2:
                return ()
            raise ValueError("inline shell function body differs")
        if tokens[0].text in _SHELL_STRUCTURE_TOKENS:
            return ()
        if _is_pure_closing_paren_token(tokens[0]):
            return ()
        if (
            len(tokens) == 1
            and tokens[0].text.endswith("))")
            and not _token_has_shell_syntax(tokens[0])
        ):
            return ()
        if _token_could_be_case_pattern(tokens[0]):
            case_payload = _case_pattern_payload_tokens(tokens[0], label=label)
            if case_payload is not None:
                tokens = case_payload + tokens[1:]
                if not tokens:
                    return ()
                continue
        return tokens


def _reject_env_split_string_option(token: str) -> bool:
    if token == "--split-string" or token.startswith("--split-string="):
        return True
    if not token.startswith("-") or token.startswith("--") or token == "-":
        return False
    return "S" in token[1:]


def _parse_env_short_option_token(token: str, *, has_next_token: bool) -> tuple[bool, bool]:
    if not token.startswith("-") or token.startswith("--") or token == "-":
        return False, False
    cluster = token[1:]
    if not cluster:
        return False, False

    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option == "S":
            return True, False
        if option == "i":
            index += 1
            continue
        if option in {"C", "u"}:
            if index != len(cluster) - 1:
                return True, False
            return False, has_next_token
        return True, False
    return False, False


def _parse_env_execution_segment(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> tuple[int | None, bool]:
    index = start_index
    while index < len(tokens):
        current_token = tokens[index]
        current = current_token.text
        if current == "--":
            index += 1
            break
        if _ASSIGNMENT_RE.fullmatch(current):
            index += 1
            continue
        if _token_has_shell_syntax(current_token):
            return None, True
        if current in _ENV_ZERO_ARG_OPTIONS:
            index += 1
            continue
        if _reject_env_split_string_option(current):
            return None, True
        if current in _ENV_OPTIONS_WITH_ARGS:
            if index + 1 >= len(tokens):
                return None, True
            index += 2
            continue
        if current.startswith("--chdir=") or current.startswith("--unset="):
            index += 1
            continue
        if current.startswith("--"):
            return None, True
        ambiguous, consumes_arg = _parse_env_short_option_token(
            current,
            has_next_token=index + 1 < len(tokens),
        )
        if ambiguous:
            return None, True
        if consumes_arg:
            index += 2
            continue
        if current.startswith("-"):
            return None, True
        return index, False
    return None, False


def _next_timeout_command_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if _token_has_shell_syntax(token):
            return None
        if current == "--":
            index += 1
            break
        if current in {"--foreground", "--preserve-status", "-v"}:
            index += 1
            continue
        if current in {"-k", "--kill-after", "-s", "--signal"}:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if (current.startswith("-k") and current != "-k") or (
            current.startswith("-s") and current != "-s"
        ):
            index += 1
            continue
        if current.startswith("--kill-after=") or current.startswith("--signal="):
            index += 1
            continue
        if current.startswith("-"):
            return None
        index += 1
        break
    return index if index < len(tokens) else None


def _next_command_wrapper_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if current == "--":
            index += 1
            break
        if current in {"-p", "-v", "-V"}:
            index += 1
            continue
        if current.startswith("-"):
            return None
        break
    return index if index < len(tokens) else None


def _next_nice_command_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if current == "--":
            index += 1
            break
        if current in {"-n", "--adjustment"}:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if current.startswith("--adjustment=") or _NICE_OLD_STYLE_RE.fullmatch(current):
            index += 1
            continue
        if _NICE_SHORT_ADJUSTMENT_RE.fullmatch(current):
            index += 1
            continue
        if current.startswith("-"):
            return None
        break
    return index if index < len(tokens) else None


def _next_sudo_command_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if current == "--":
            index += 1
            break
        if current in {
            "-A",
            "--askpass",
            "-b",
            "--background",
            "-E",
            "--preserve-env",
            "-H",
            "--set-home",
            "-K",
            "--remove-timestamp",
            "-k",
            "--reset-timestamp",
            "-n",
            "--non-interactive",
            "-P",
            "--preserve-groups",
            "-S",
            "--stdin",
            "-s",
            "--shell",
            "-v",
            "--validate",
            "-V",
            "--version",
        }:
            index += 1
            continue
        if current in {
            "-C",
            "--close-from",
            "-D",
            "--chdir",
            "-g",
            "--group",
            "-h",
            "--host",
            "-p",
            "--prompt",
            "-r",
            "--role",
            "-t",
            "--type",
            "-u",
            "--user",
        }:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if current.startswith("--preserve-env="):
            index += 1
            continue
        if current.startswith("-"):
            return None
        break
    return index if index < len(tokens) else None


def _next_setsid_command_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        current = token.text
        if current == "--":
            index += 1
            break
        if current in {"-c", "--ctty", "-f", "--fork", "-w", "--wait"}:
            index += 1
            continue
        if current.startswith("-"):
            return None
        break
    return index if index < len(tokens) else None


def _next_wrapper_command_index(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
) -> int | None:
    wrapper = _literal_token_basename(tokens[start_index])
    if wrapper == "timeout":
        return _next_timeout_command_index(tokens, start_index=start_index)
    if wrapper == "command":
        return _next_command_wrapper_index(tokens, start_index=start_index)
    if wrapper == "nice":
        return _next_nice_command_index(tokens, start_index=start_index)
    if wrapper == "setsid":
        return _next_setsid_command_index(tokens, start_index=start_index)
    if wrapper == "sudo":
        return _next_sudo_command_index(tokens, start_index=start_index)
    return None


def _command_has_forbidden_nonliteral_executable(
    tokens: tuple[_ShellToken, ...],
) -> bool:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _token_has_shell_syntax(token):
            return True
        if _is_busybox_executable_token(token):
            if index + 1 >= len(tokens):
                return True
            applet = tokens[index + 1]
            if _token_has_shell_syntax(applet):
                return True
            if _is_env_executable_token(applet):
                next_index, suspicious = _parse_env_execution_segment(
                    tokens,
                    start_index=index + 2,
                )
                if suspicious:
                    return True
                if next_index is None:
                    return False
                index = next_index
                continue
            return False
        if _is_env_executable_token(token):
            next_index, suspicious = _parse_env_execution_segment(
                tokens,
                start_index=index + 1,
            )
            if suspicious:
                return True
            if next_index is None:
                return False
            index = next_index
            continue
        basename = _literal_token_basename(token)
        if basename == "flock":
            flock_target = _parse_flock_command_target(tokens, start_index=index)
            if flock_target is None:
                return True
            if flock_target.mode == "command-string":
                return _flock_command_string_is_forbidden(
                    flock_target.command_text or ""
                )
            if flock_target.mode == "argv":
                index = flock_target.argv_index or len(tokens)
                continue
            return False
        if basename in _WRAPPER_BASENAMES:
            next_index = _next_wrapper_command_index(tokens, start_index=index)
            if next_index is None:
                return True
            index = next_index
            continue
        return _unmodeled_literal_prefix_hides_nonliteral_command_surface(
            tokens,
            start_index=index + 1,
        )
    return False


def _command_has_ambiguous_env_shell_surface(tokens: tuple[_ShellToken, ...]) -> bool:
    for index, token in enumerate(tokens):
        if _is_env_executable_token(token):
            _next_index, suspicious = _parse_env_execution_segment(
                tokens,
                start_index=index + 1,
            )
            if suspicious:
                return True
            continue
        if not _is_busybox_executable_token(token):
            continue
        if index + 1 >= len(tokens):
            continue
        applet = tokens[index + 1]
        if not (_token_has_shell_syntax(applet) or _is_env_executable_token(applet)):
            continue
        _next_index, suspicious = _parse_env_execution_segment(
            tokens,
            start_index=index + 2,
        )
        if suspicious:
            return True
    return False


def _shell_semantic_command_pairs(
    script: str,
    *,
    label: str,
) -> tuple[tuple[int, str, tuple[_ShellToken, ...]], ...]:
    pairs = []
    semantic_script = _strip_patch_release_parser_heredoc_bodies(script)
    for command_index, command_text in enumerate(
        split_bash_simple_command_strings(semantic_script, label=label)
    ):
        tokens = _semantic_surface_tokens(
            _parse_shell_tokens(command_text, label=label),
            label=label,
        )
        if tokens:
            pairs.append((command_index, command_text, tokens))
    return tuple(pairs)


def _shell_c_invocation_is_forbidden(command: tuple[_ShellToken, ...]) -> bool:
    tokens = _strip_command_prefixes(command)
    if not tokens:
        return False

    if _command_has_forbidden_nonliteral_executable(tokens):
        return True

    if _command_has_ambiguous_env_shell_surface(tokens):
        return True

    for index, token in enumerate(tokens):
        token = tokens[index]

        if token.text == "-c":
            payload_index = index + 1
        elif (
            token.text.startswith("-")
            and not token.text.startswith("--")
            and "c" in token.text[1:]
        ):
            payload_index = index + 1
        else:
            continue

        if payload_index >= len(tokens):
            return True

        if any(
            _is_shell_interpreter_reference_token(previous)
            for previous in tokens[:index]
        ):
            return True
        if index > 0 and _token_has_shell_syntax(tokens[index - 1]):
            return True

    return False


def _validate_substitution_scan_bounds(
    body: str,
    *,
    label: str,
    depth: int,
    count: int,
) -> None:
    if depth > _SUBSTITUTION_SCAN_MAX_DEPTH:
        raise ValueError(f"{label} substitution nesting differs")
    if count > _SUBSTITUTION_SCAN_MAX_COUNT:
        raise ValueError(f"{label} substitution count differs")
    if not body or len(body) > _SUBSTITUTION_SCAN_MAX_BODY_CHARS:
        raise ValueError(f"{label} substitution body differs")


def _consume_backtick_substitution(
    text: str,
    *,
    start_index: int,
    label: str,
) -> tuple[str, int]:
    index = start_index + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            if index + 1 >= len(text):
                raise ValueError(f"{label} backtick substitution differs")
            index += 2
            continue
        if character == "`":
            return text[start_index + 1 : index], index + 1
        index += 1
    raise ValueError(f"{label} backtick substitution differs")


def _consume_parenthesized_substitution_body(
    text: str,
    *,
    start_index: int,
    initial_depth: int,
    label: str,
) -> tuple[str, int]:
    depth = initial_depth
    quote: str | None = None
    index = start_index

    while index < len(text):
        character = text[index]
        if quote is None:
            if text.startswith("$'", index):
                quote = "ansi"
                index += 2
                continue
            if text.startswith('$"', index):
                quote = "locale"
                index += 2
                continue
            if character == "'":
                quote = "'"
                index += 1
                continue
            if character == '"':
                quote = '"'
                index += 1
                continue
            if character == "\\":
                if index + 1 >= len(text):
                    raise ValueError(f"{label} substitution continuation differs")
                index += 2
                continue
            if character == "`":
                _body, index = _consume_backtick_substitution(
                    text,
                    start_index=index,
                    label=label,
                )
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    body_end = index - (initial_depth - 1)
                    return text[start_index:body_end], index + 1
                if depth < 0:
                    raise ValueError(f"{label} substitution nesting differs")
            index += 1
            continue

        if quote == "'":
            if character == "'":
                quote = None
            index += 1
            continue
        if quote == "ansi":
            if character == "\\":
                if index + 1 >= len(text):
                    raise ValueError(
                        f"{label} substitution continuation differs"
                    )
                index += 2
                continue
            if character == "'":
                quote = None
            index += 1
            continue

        if character == "\\":
            if index + 1 >= len(text):
                raise ValueError(f"{label} substitution continuation differs")
            if text[index + 1] in '$`"\\\n':
                index += 2
                continue
        elif character == '"':
            quote = None
            index += 1
            continue
        elif character == "`":
            _body, index = _consume_backtick_substitution(
                text,
                start_index=index,
                label=label,
            )
            continue
        index += 1

    raise ValueError(f"{label} substitution nesting differs")


def _substitution_body_has_forbidden_surface(
    body: str,
    *,
    label: str,
    depth: int,
) -> bool:
    _validate_substitution_scan_bounds(body, label=label, depth=depth, count=1)
    return _shell_text_has_forbidden_surface(
        body,
        label=label,
        allowed_hidden_indices=frozenset(),
        substitution_depth=depth,
    )


def _text_has_forbidden_executable_substitution_surface(
    text: str,
    *,
    label: str,
    substitution_depth: int,
) -> bool:
    if substitution_depth > _SUBSTITUTION_SCAN_MAX_DEPTH:
        raise ValueError(f"{label} substitution nesting differs")

    quote: str | None = None
    index = 0
    substitution_count = 0

    while index < len(text):
        character = text[index]
        if quote is None:
            if text.startswith("$'", index):
                quote = "ansi"
                index += 2
                continue
            if text.startswith('$"', index):
                quote = "locale"
                index += 2
                continue
            if character == "'":
                quote = "'"
                index += 1
                continue
            if character == '"':
                quote = '"'
                index += 1
                continue
            if character == "\\":
                if index + 1 >= len(text):
                    raise ValueError(f"{label} substitution continuation differs")
                index += 2
                continue
            if character == "`":
                substitution_count += 1
                body, index = _consume_backtick_substitution(
                    text,
                    start_index=index,
                    label=label,
                )
                _validate_substitution_scan_bounds(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                    count=substitution_count,
                )
                if _substitution_body_has_forbidden_surface(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                ):
                    return True
                continue
            if character == "$" and index + 1 < len(text) and text[index + 1] == "(":
                if index + 2 < len(text) and text[index + 2] == "(":
                    arithmetic_body, index = _consume_parenthesized_substitution_body(
                        text,
                        start_index=index + 3,
                        initial_depth=2,
                        label=label,
                    )
                    _validate_substitution_scan_bounds(
                        arithmetic_body,
                        label=label,
                        depth=substitution_depth + 1,
                        count=substitution_count,
                    )
                    if _text_has_forbidden_executable_substitution_surface(
                        arithmetic_body,
                        label=label,
                        substitution_depth=substitution_depth + 1,
                    ):
                        return True
                    continue
                substitution_count += 1
                body, index = _consume_parenthesized_substitution_body(
                    text,
                    start_index=index + 2,
                    initial_depth=1,
                    label=label,
                )
                _validate_substitution_scan_bounds(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                    count=substitution_count,
                )
                if _substitution_body_has_forbidden_surface(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                ):
                    return True
                continue
            if character in "<>" and index + 1 < len(text) and text[index + 1] == "(":
                substitution_count += 1
                body, index = _consume_parenthesized_substitution_body(
                    text,
                    start_index=index + 2,
                    initial_depth=1,
                    label=label,
                )
                _validate_substitution_scan_bounds(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                    count=substitution_count,
                )
                if _substitution_body_has_forbidden_surface(
                    body,
                    label=label,
                    depth=substitution_depth + 1,
                ):
                    return True
                continue
            index += 1
            continue

        if quote == "'":
            if character == "'":
                quote = None
            index += 1
            continue
        if quote == "ansi":
            if character == "\\":
                if index + 1 >= len(text):
                    raise ValueError(
                        f"{label} substitution continuation differs"
                    )
                index += 2
                continue
            if character == "'":
                quote = None
            index += 1
            continue

        if character == "\\":
            if index + 1 >= len(text):
                raise ValueError(f"{label} substitution continuation differs")
            if text[index + 1] in '$`"\\\n':
                index += 2
                continue
        elif character == '"':
            quote = None
            index += 1
            continue
        elif character == "`":
            substitution_count += 1
            body, index = _consume_backtick_substitution(
                text,
                start_index=index,
                label=label,
            )
            _validate_substitution_scan_bounds(
                body,
                label=label,
                depth=substitution_depth + 1,
                count=substitution_count,
            )
            if _substitution_body_has_forbidden_surface(
                body,
                label=label,
                depth=substitution_depth + 1,
            ):
                return True
            continue
        elif character == "$" and index + 1 < len(text) and text[index + 1] == "(":
            if index + 2 < len(text) and text[index + 2] == "(":
                arithmetic_body, index = _consume_parenthesized_substitution_body(
                    text,
                    start_index=index + 3,
                    initial_depth=2,
                    label=label,
                )
                _validate_substitution_scan_bounds(
                    arithmetic_body,
                    label=label,
                    depth=substitution_depth + 1,
                    count=substitution_count,
                )
                if _text_has_forbidden_executable_substitution_surface(
                    arithmetic_body,
                    label=label,
                    substitution_depth=substitution_depth + 1,
                ):
                    return True
                continue
            substitution_count += 1
            body, index = _consume_parenthesized_substitution_body(
                text,
                start_index=index + 2,
                initial_depth=1,
                label=label,
            )
            _validate_substitution_scan_bounds(
                body,
                label=label,
                depth=substitution_depth + 1,
                count=substitution_count,
            )
            if _substitution_body_has_forbidden_surface(
                body,
                label=label,
                depth=substitution_depth + 1,
            ):
                return True
            continue
        index += 1

    if quote is not None:
        raise ValueError(f"{label} has unterminated substitution quoting")
    return False


def _shell_text_has_forbidden_surface(
    script: str,
    *,
    label: str,
    allowed_hidden_indices: frozenset[int],
    substitution_depth: int = 0,
) -> bool:
    for command_index, command_text in enumerate(
        split_bash_simple_command_strings(script, label=label)
    ):
        if _text_has_forbidden_executable_substitution_surface(
            command_text,
            label=label,
            substitution_depth=substitution_depth,
        ):
            return True
        command_tokens = _semantic_surface_tokens(
            _parse_shell_tokens(command_text, label=label),
            label=label,
        )
        if not command_tokens:
            continue
        if _shell_c_invocation_is_forbidden(command_tokens):
            return True
        if _mount_command_targets_supervisor_parent(
            command_tokens,
            allow_reviewed_nonliteral_hidden=command_index in allowed_hidden_indices,
        ):
            return True
        if _command_references_supervisor(command_tokens, command_text) and not _is_reviewed_supervisor_command(
            command_tokens
        ):
            return True
    return False


def reviewed_hidden_mask_loop_source(script: str, *, label: str) -> str:
    if script.count(REVIEWED_HIDDEN_MASK_LOOP_HEADER) != 1:
        raise ValueError(f"{label} hidden-mask loop header differs")
    start = script.index(REVIEWED_HIDDEN_MASK_LOOP_HEADER)
    done_start = script.find("\ndone\n", start)
    if done_start < 0:
        raise ValueError(f"{label} hidden-mask loop terminator differs")
    loop = script[start : done_start + len("\ndone\n")]
    if not loop.endswith("done\n"):
        raise ValueError(f"{label} hidden-mask loop terminator differs")
    return loop


def reviewed_hidden_mask_loop_sha256(script: str, *, label: str) -> str:
    return hashlib.sha256(
        reviewed_hidden_mask_loop_source(script, label=label).encode("utf-8")
    ).hexdigest()


def _authorized_hidden_readonly_mount_indices(
    script: str,
    *,
    label: str,
) -> frozenset[int]:
    try:
        if (
            reviewed_hidden_mask_loop_sha256(script, label=label)
            != REVIEWED_HIDDEN_MASK_LOOP_SHA256
        ):
            return frozenset()
    except ValueError:
        return frozenset()

    commands = split_bash_simple_command_strings(script, label=label)
    window = REVIEWED_HIDDEN_MASK_LOOP_COMMANDS
    loop_starts = []
    for index in range(len(commands) - len(window) + 1):
        if commands[index : index + len(window)] != window:
            continue
        if index == 0 or index + len(window) >= len(commands):
            continue
        if commands[index - 1] != REVIEWED_HIDDEN_MASK_LOOP_PREVIOUS_COMMAND:
            continue
        if commands[index + len(window)] != REVIEWED_HIDDEN_MASK_LOOP_NEXT_COMMAND:
            continue
        loop_starts.append(index)

    if len(loop_starts) != 1:
        return frozenset()
    return frozenset({loop_starts[0] + 4})


def _apply_mount_option_text(
    option_token: _ShellToken,
    *,
    read_only_state: bool | None,
    remount_like: bool,
) -> tuple[bool | None, bool, bool]:
    options_nonliteral = _token_has_shell_syntax(option_token)
    for option in option_token.text.split(","):
        normalized = option.strip().replace("\\", "")
        if normalized.startswith("remount"):
            remount_like = True
        elif normalized == "ro":
            read_only_state = True
        elif normalized == "rw":
            read_only_state = False
    return read_only_state, remount_like, options_nonliteral


def _parse_mount_short_option_token(
    tokens: tuple[_ShellToken, ...],
    *,
    start_index: int,
    read_only_state: bool | None,
    remount_like: bool,
    options_nonliteral: bool,
) -> tuple[int, bool | None, bool, bool] | None:
    token = tokens[start_index]
    token_text = token.text
    if not token_text.startswith("-") or token_text.startswith("--") or token_text == "-":
        return None

    cluster = token_text[1:]
    if not cluster:
        return None

    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option == "o":
            if index + 1 < len(cluster):
                option_token = _ShellToken(
                    text=cluster[index + 1 :],
                    has_shell_syntax=_token_has_shell_syntax(token),
                )
                read_only_state, remount_like, option_nonliteral = _apply_mount_option_text(
                    option_token,
                    read_only_state=read_only_state,
                    remount_like=remount_like,
                )
                return (
                    start_index + 1,
                    read_only_state,
                    remount_like,
                    options_nonliteral or option_nonliteral,
                )
            if start_index + 1 >= len(tokens):
                return None
            read_only_state, remount_like, option_nonliteral = _apply_mount_option_text(
                tokens[start_index + 1],
                read_only_state=read_only_state,
                remount_like=remount_like,
            )
            return (
                start_index + 2,
                read_only_state,
                remount_like,
                options_nonliteral or option_nonliteral,
            )
        if option == "r":
            read_only_state = True
            index += 1
            continue
        if option == "w":
            read_only_state = False
            index += 1
            continue
        if option == "m":
            if index + 1 < len(cluster):
                return start_index + 1, read_only_state, remount_like, options_nonliteral
            index += 1
            continue
        if option in _MOUNT_SHORT_OPTIONS_WITH_ARGS:
            if index + 1 < len(cluster):
                return start_index + 1, read_only_state, remount_like, options_nonliteral
            if start_index + 1 >= len(tokens):
                return None
            return start_index + 2, read_only_state, remount_like, options_nonliteral
        if option in _MOUNT_SHORT_ZERO_ARG_OPTIONS:
            index += 1
            continue
        return None
    return start_index + 1, read_only_state, remount_like, options_nonliteral


def _token_could_be_mount_short_option(token_text: str) -> bool:
    if not token_text.startswith("-") or token_text.startswith("--") or token_text == "-":
        return False

    cluster = token_text[1:]
    if not cluster:
        return False

    index = 0
    while index < len(cluster):
        option = cluster[index]
        if option == "o" or option in _MOUNT_SHORT_OPTIONS_WITH_ARGS:
            return True
        if option == "m":
            return True
        if option in _MOUNT_SHORT_ZERO_ARG_OPTIONS:
            index += 1
            continue
        return False
    return True


def _mount_command_targets_supervisor_parent(
    command: tuple[_ShellToken, ...],
    *,
    allow_reviewed_nonliteral_hidden: bool,
) -> bool:
    tokens = _strip_command_prefixes(command)
    if not tokens:
        return False

    executable_token = tokens[0]
    executable = executable_token.text
    executable_literal = executable == "/usr/bin/mount"
    executable_nonliteral = _token_has_shell_syntax(executable_token)
    read_only_state: bool | None = None
    remount_like = False
    options_nonliteral = False
    positionals: list[_ShellToken] = []
    unknown_flag = False
    malformed_short_flag = False
    index = 1

    while index < len(tokens):
        token = tokens[index]
        token_text = token.text
        if token_text in {"-r", "--read-only"}:
            read_only_state = True
            index += 1
            continue
        if token_text in {"-w", "--rw", "--read-write"}:
            read_only_state = False
            index += 1
            continue
        if token_text in {"-o", "--options"}:
            if index + 1 >= len(tokens):
                return True
            read_only_state, remount_like, option_nonliteral = _apply_mount_option_text(
                tokens[index + 1],
                read_only_state=read_only_state,
                remount_like=remount_like,
            )
            options_nonliteral = options_nonliteral or option_nonliteral
            index += 2
            continue
        if token_text.startswith("--options="):
            read_only_state, remount_like, option_nonliteral = _apply_mount_option_text(
                _ShellToken(
                    text=token_text.split("=", 1)[1],
                    has_shell_syntax=_token_has_shell_syntax(token),
                ),
                read_only_state=read_only_state,
                remount_like=remount_like,
            )
            options_nonliteral = options_nonliteral or option_nonliteral
            index += 1
            continue
        if token_text.startswith("-o") and token_text != "-o":
            read_only_state, remount_like, option_nonliteral = _apply_mount_option_text(
                _ShellToken(
                    text=token_text[2:],
                    has_shell_syntax=_token_has_shell_syntax(token),
                ),
                read_only_state=read_only_state,
                remount_like=remount_like,
            )
            options_nonliteral = options_nonliteral or option_nonliteral
            index += 1
            continue
        if token_text in {
            "--bind",
            "--make-private",
            "--make-rprivate",
            "--make-runbindable",
            "--make-rshared",
            "--make-rslave",
            "--make-shared",
            "--make-slave",
            "--move",
            "--rbind",
        }:
            index += 1
            continue
        if token_text in {"-t", "--types"}:
            if index + 1 >= len(tokens):
                return True
            index += 2
            continue
        if token_text == "--":
            positionals.extend(tokens[index + 1 :])
            break
        if token_text.startswith("-") and not token_text.startswith("--") and token_text != "-":
            parsed = _parse_mount_short_option_token(
                tokens,
                start_index=index,
                read_only_state=read_only_state,
                remount_like=remount_like,
                options_nonliteral=options_nonliteral,
            )
            if parsed is None:
                malformed_short_flag = True
                unknown_flag = True
                index += 1
                continue
            index, read_only_state, remount_like, options_nonliteral = parsed
            continue
        if token_text.startswith("-"):
            unknown_flag = True
            index += 1
            continue
        positionals.append(token)
        index += 1

    read_only_like = read_only_state is True

    nonliteral_positionals = any(_token_has_shell_syntax(token) for token in positionals)
    mount_surface_uses_nonliteral = (
        executable_nonliteral
        or options_nonliteral
        or nonliteral_positionals
    )
    has_mount_flag = any(
        token.text in {
            "-o",
            "--options",
            "-r",
            "--read-only",
            "-w",
            "--rw",
            "--read-write",
            "--bind",
            "--make-private",
            "--make-rprivate",
            "--make-runbindable",
            "--make-rshared",
            "--make-rslave",
            "--make-shared",
            "--make-slave",
            "--move",
            "--rbind",
        }
        or token.text.startswith("--options=")
        or _token_could_be_mount_short_option(token.text)
        for token in tokens[1:]
    )
    looks_like_mount_surface = (
        executable_literal
        or executable in _DISALLOWED_MOUNT_WRAPPERS
        or any(token.text == "/usr/bin/mount" for token in tokens)
        or (executable_nonliteral and has_mount_flag)
    )
    if looks_like_mount_surface and malformed_short_flag:
        return True
    if looks_like_mount_surface and not positionals and (
        read_only_state is not None or remount_like or options_nonliteral
    ):
        return True
    if looks_like_mount_surface and mount_surface_uses_nonliteral:
        if _token_texts(tokens) in APPROVED_SUPERVISOR_COMMAND_TOKENS:
            pass
        elif _token_texts(tokens) in APPROVED_NONLITERAL_MOUNT_COMMANDS:
            pass
        elif (
            _token_texts(tokens) in APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS
            and allow_reviewed_nonliteral_hidden
        ):
            pass
        else:
            return True

    if not (remount_like and read_only_like):
        return False

    if _token_texts(tokens) in APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS:
        return not allow_reviewed_nonliteral_hidden

    if executable in _DISALLOWED_MOUNT_WRAPPERS:
        return True
    if executable_nonliteral:
        return True
    if options_nonliteral or unknown_flag:
        return True
    if not positionals:
        return True

    target_token = positionals[-1]
    target_path = _canonical_literal_path(target_token)
    if target_path == "/mnt/supervisor":
        return True
    if target_path is None:
        return True
    if len(positionals) != 1:
        return True
    return False


def has_forbidden_supervisor_parent_readonly_mount(
    script: str,
    *,
    label: str,
) -> bool:
    try:
        semantic_script = _strip_patch_release_parser_heredoc_bodies(script)
        allowed_hidden_indices = _authorized_hidden_readonly_mount_indices(
            semantic_script,
            label=label,
        )
        return _shell_text_has_forbidden_surface(
            semantic_script,
            label=label,
            allowed_hidden_indices=allowed_hidden_indices,
        )
    except ValueError:
        return True


def _resolve_shell_aliases(text: str, aliases: dict[str, str]) -> str:
    resolved = text
    for _depth in range(8):
        changed = False

        def replace_braced(match: re.Match[str]) -> str:
            nonlocal changed
            body = match.group("body")
            indirect = body.startswith("!")
            length = body.startswith("#")
            subject = body[1:] if indirect or length else body
            name_match = re.match(
                r"(?:[A-Za-z_][A-Za-z0-9_]*|[1-9][0-9]*)",
                subject,
            )
            if name_match is None:
                return match.group(0)
            name = name_match.group(0)
            operator = subject[len(name) :]
            if indirect and (
                operator in {"*", "@"}
                or operator.startswith("[")
            ):
                return match.group(0)
            value = _semantic_alias_value(aliases.get(name))
            if value is None:
                return match.group(0)
            changed = True
            if indirect:
                indirect_value = _semantic_alias_value(
                    aliases.get(value)
                )
                if (
                    _RAW_CGROUP_ROOT_MARKER in value
                    or _AMBIGUOUS_ARRAY_ALIAS_MARKER in value
                    or (
                        indirect_value is not None
                        and (
                            _RAW_CGROUP_ROOT_MARKER in indirect_value
                            or _AMBIGUOUS_ARRAY_ALIAS_MARKER
                            in indirect_value
                        )
                    )
                ):
                    return _AMBIGUOUS_TRACKED_PARAMETER_MARKER
                return value
            if operator.startswith("["):
                if _RAW_CGROUP_ROOT_MARKER in value:
                    return (
                        _AMBIGUOUS_TRACKED_PARAMETER_MARKER
                        + value
                    )
                return _AMBIGUOUS_ARRAY_ALIAS_MARKER + value
            if length or operator:
                if (
                    _RAW_CGROUP_ROOT_MARKER in value
                    or "cgroup.procs" in value
                ):
                    return (
                        _AMBIGUOUS_TRACKED_PARAMETER_MARKER
                        + value
                    )
            return value

        def replace_plain(match: re.Match[str]) -> str:
            nonlocal changed
            value = _semantic_alias_value(
                aliases.get(match.group("name"))
            )
            if value is None:
                return match.group(0)
            changed = True
            return value

        def replace_positional(match: re.Match[str]) -> str:
            nonlocal changed
            value = _semantic_alias_value(
                aliases.get(match.group("position"))
            )
            if value is None:
                return match.group(0)
            changed = True
            return value

        updated = _SHELL_BRACED_PARAMETER_RE.sub(
            replace_braced,
            resolved,
        )
        updated = _SHELL_PLAIN_VARIABLE_REFERENCE_RE.sub(
            replace_plain,
            updated,
        )
        updated = _SHELL_POSITIONAL_REFERENCE_RE.sub(
            replace_positional,
            updated,
        )
        resolved = updated
        if not changed:
            break
    return resolved


def _resolve_shell_token(
    token: _ShellToken,
    aliases: dict[str, str],
) -> str:
    if not token.segments:
        return _resolve_shell_aliases(token.text, aliases)
    return "".join(
        _resolve_shell_aliases(segment, aliases)
        if expansion_active
        else segment.replace("$", _LITERAL_DOLLAR_MARKER)
        for segment, expansion_active, _redirection_active in token.segments
    )


def _slice_shell_token(token: _ShellToken, start: int) -> _ShellToken:
    if not token.segments:
        return _ShellToken(
            text=token.text[start:],
            has_shell_syntax=token.has_shell_syntax,
        )
    consumed = 0
    sliced: list[tuple[str, bool]] = []
    for segment, expansion_active, redirection_active in token.segments:
        end = consumed + len(segment)
        if end <= start:
            consumed = end
            continue
        segment_start = max(0, start - consumed)
        sliced.append(
            (
                segment[segment_start:],
                expansion_active,
                redirection_active,
            )
        )
        consumed = end
    text = token.text[start:]
    return _ShellToken(
        text=text,
        has_shell_syntax=token.has_shell_syntax,
        segments=tuple(sliced),
    )


def _active_shell_token_text(token: _ShellToken) -> str:
    if not token.segments:
        return token.text if token.has_shell_syntax else ""
    return "".join(
        segment
        for segment, expansion_active, _redirection_active in token.segments
        if expansion_active
    )


def _known_array_type_marker(value: str) -> str | None:
    for marker in (
        _ASSOCIATIVE_ARRAY_ALIAS_MARKER,
        _INDEXED_ARRAY_ALIAS_MARKER,
    ):
        if marker in value:
            return marker
    return None


def _typed_array_alias(marker: str, value: str) -> str:
    return _AMBIGUOUS_ARRAY_ALIAS_MARKER + marker + value


def _semantic_alias_value(value: str | None) -> str | None:
    return (
        None
        if value is None
        else value.replace(_READONLY_ALIAS_MARKER, "")
    )


def _alias_value_is_runtime_dynamic(value: str | None) -> bool:
    value = _semantic_alias_value(value)
    return value is None or any(
        marker in value
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
        )
    ) or any(character in value for character in ("$", "`"))


def _readonly_alias(value: str) -> str:
    return (
        value
        if _READONLY_ALIAS_MARKER in value
        else _READONLY_ALIAS_MARKER + value
    )


def _loop_iteration_target_is_forbidden(
    tokens: tuple[_ShellToken, ...],
    aliases: dict[str, str],
) -> bool:
    if len(tokens) < 2 or tokens[0].text not in {"for", "select"}:
        return False
    target = tokens[1]
    if target.text.startswith("(("):
        return any(
            protected in target.text
            for protected in (
                "cgroup_path",
                "supervisor_cgroup",
                "cgroup_members",
                "BASHOPTS",
                "BASH_ENV",
                "ENV",
                "PATH",
                "SHELLOPTS",
            )
        )
    if target.has_shell_syntax or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        target.text,
    ):
        return True
    if target.text in {
        "cgroup_path",
        "supervisor_cgroup",
        "cgroup_members",
    } or target.text in _DISPATCH_STATE_VARIABLES or (
        target.text in _RESERVED_TRANSPORT_OUTPUTS
    ):
        return True
    value = aliases.get(target.text, "")
    return any(
        marker in value
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
            _RAW_CGROUP_ROOT_MARKER,
        )
    )


def _token_has_nested_braced_parameter(token: _ShellToken) -> bool:
    if not token.has_shell_syntax:
        return False
    active_text = _active_shell_token_text(token)
    depth = 0
    index = 0
    while index < len(active_text):
        if active_text.startswith("${", index):
            if depth:
                return True
            depth += 1
            index += 2
            continue
        if active_text[index] == "}" and depth:
            depth -= 1
        index += 1
    return False


def _shell_function_declaration(
    command_text: str,
) -> tuple[bool, str | None, bool]:
    stripped = command_text.strip()
    matches = (
        re.match(
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
            r"\(\s*\)\s*\{(?P<body>.*)\Z",
            stripped,
        ),
        re.match(
            r"function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s*\(\s*\))?\s*\{(?P<body>.*)\Z",
            stripped,
        ),
    )
    for match in matches:
        if match is not None:
            return True, match.group("name"), bool(
                match.group("body").strip()
            )
    if stripped.startswith("function ") or re.search(
        r"\(\s*\)\s*\{",
        stripped,
    ):
        return True, None, True
    return False, None, False


def _normalize_split_function_declarations(
    commands: tuple[str, ...],
) -> tuple[str, ...]:
    normalized: list[str] = []
    index = 0
    while index < len(commands):
        command = commands[index].strip()
        pending = re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)"
            r"|function\s+[A-Za-z_][A-Za-z0-9_]*"
            r"(?:\s*\(\s*\))?)",
            command,
        )
        if pending is not None:
            if index + 1 >= len(commands) or commands[index + 1] != "{":
                raise ValueError("split shell function declaration differs")
            normalized.append(command + " {")
            index += 2
            continue
        normalized.append(commands[index])
        index += 1
    return tuple(normalized)


def _normalize_split_function_command_records(
    records: tuple[_ShellCommandRecord, ...],
) -> tuple[_ShellCommandRecord, ...]:
    normalized: list[_ShellCommandRecord] = []
    index = 0
    while index < len(records):
        record = records[index]
        command = record.text.strip()
        pending = re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)"
            r"|function\s+[A-Za-z_][A-Za-z0-9_]*"
            r"(?:\s*\(\s*\))?)",
            command,
        )
        if pending is not None:
            if (
                index + 1 >= len(records)
                or records[index + 1].text != "{"
                or record.following_operator is not None
                or records[index + 1].preceding_operator is not None
            ):
                raise ValueError("split shell function declaration differs")
            normalized.append(
                _ShellCommandRecord(
                    text=command + " {",
                    preceding_operator=record.preceding_operator,
                    following_operator=records[
                        index + 1
                    ].following_operator,
                    execution_scopes=record.execution_scopes,
                )
            )
            index += 2
            continue
        normalized.append(record)
        index += 1
    return tuple(normalized)


def _annotate_command_control_scopes(
    records: tuple[_ShellCommandRecord, ...],
) -> tuple[_ShellCommandRecord, ...]:
    annotated: list[_ShellCommandRecord] = []
    control_stack: list[str] = []
    for record in records:
        annotated.append(
            _ShellCommandRecord(
                text=record.text,
                preceding_operator=record.preceding_operator,
                following_operator=record.following_operator,
                execution_scopes=record.execution_scopes,
                control_scopes=tuple(control_stack),
            )
        )
        first_word = record.text.split(maxsplit=1)[0]
        if first_word in {
            "case",
            "for",
            "if",
            "select",
            "until",
            "while",
            "{",
        }:
            control_stack.append(first_word)
            continue
        if first_word not in {"done", "esac", "fi", "}"}:
            continue
        if first_word == "}" and (
            not control_stack or control_stack[-1] != "{"
        ):
            continue
        if not control_stack:
            raise ValueError("shell control scope underflow")
        expected = {
            "done": {"for", "select", "until", "while"},
            "esac": {"case"},
            "fi": {"if"},
            "}": {"{"},
        }[first_word]
        if control_stack[-1] not in expected:
            raise ValueError("shell control scope mismatch")
        control_stack.pop()
    if control_stack:
        raise ValueError("unterminated shell control scope")
    return tuple(annotated)


def _shell_function_definitions(
    records: tuple[_ShellCommandRecord, ...],
) -> tuple[
    dict[str, tuple[_ShellCommandRecord, ...]],
    Counter[tuple[str, str]],
]:
    stack: list[
        tuple[str, _ShellCommandRecord, list[_ShellCommandRecord]]
    ] = []
    definitions: list[
        tuple[
            str,
            _ShellCommandRecord,
            tuple[_ShellCommandRecord, ...],
        ]
    ] = []
    for record in records:
        command = record.text
        is_function, function_name, has_inline_body = (
            _shell_function_declaration(command)
        )
        if is_function:
            if function_name is None or has_inline_body:
                raise ValueError("ambiguous shell function definition")
            stack.append((function_name, record, []))
            continue
        if command == "}" and stack:
            function_name, declaration, body = stack.pop()
            definitions.append(
                (function_name, declaration, tuple(body))
            )
            continue
        if stack:
            stack[-1][2].append(record)
    if stack:
        raise ValueError("unterminated shell function definition")
    bodies: dict[str, tuple[_ShellCommandRecord, ...]] = {}
    inventory: Counter[tuple[str, str]] = Counter()
    for function_name, declaration, body in definitions:
        identity_records = (
            (declaration,)
            if function_name == "builder_main"
            else (declaration,) + body
        )
        control_scope: list[str] = []
        identity_rows = []
        for record in identity_records:
            identity_rows.append(
                (
                    record.text,
                    record.preceding_operator,
                    record.following_operator,
                    record.execution_scopes,
                    record.control_scopes,
                    tuple(control_scope),
                )
            )
            first_word = record.text.split(maxsplit=1)[0]
            if first_word in {
                "case",
                "for",
                "if",
                "select",
                "until",
                "while",
                "{",
            }:
                control_scope.append(first_word)
            elif first_word in {"done", "esac", "fi", "}"}:
                if control_scope:
                    control_scope.pop()
        body_digest = hashlib.sha256(
            repr(tuple(identity_rows)).encode("utf-8")
        ).hexdigest()
        inventory[(function_name, body_digest)] += 1
        if function_name in bodies:
            raise ValueError("duplicate shell function definition")
        bodies[function_name] = body
    return bodies, inventory


def _apply_direct_function_alias_writes(
    tokens: tuple[_ShellToken, ...],
    aliases: dict[str, str],
) -> tuple[bool, set[str]]:
    written: set[str] = set()
    leading_assignments = _leading_assignment_indices(tokens)
    for token_index, token in enumerate(tokens):
        if token_index not in leading_assignments:
            continue
        indexed = _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(token.text)
        scalar = _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(token.text)
        assignment = indexed or scalar
        if assignment is None:
            continue
        name = assignment.group("name")
        if name in {
            "cgroup_path",
            "supervisor_cgroup",
        } or name in _DISPATCH_STATE_VARIABLES or (
            name in _RESERVED_TRANSPORT_OUTPUTS
        ):
            return True, written
        existing = _semantic_alias_value(aliases.get(name)) or ""
        if _NAMEREF_ALIAS_MARKER in existing and (
            any(
                target in existing
                for target in (
                    *_DISPATCH_STATE_VARIABLES,
                    *_RESERVED_TRANSPORT_OUTPUTS,
                    "cgroup_path",
                    "supervisor_cgroup",
                )
            )
            or _RAW_CGROUP_ROOT_MARKER in existing
        ):
            return True, written
        if _READONLY_ALIAS_MARKER in aliases.get(name, ""):
            continue
        value_token = _slice_shell_token(
            token,
            assignment.start("value"),
        )
        value = _resolve_shell_token(value_token, aliases)
        if _assignment_value_has_dynamic_shell_syntax(
            _active_shell_token_text(value_token),
            token_has_shell_syntax=value_token.has_shell_syntax,
        ):
            value = _AMBIGUOUS_DYNAMIC_ALIAS_MARKER + value
        if assignment.group("append"):
            value = aliases.get(name, "") + value
        if indexed is not None:
            previous = aliases.get(name, "")
            marker = _known_array_type_marker(previous)
            value = (
                _typed_array_alias(marker, value)
                if marker is not None
                else _AMBIGUOUS_ARRAY_ALIAS_MARKER + value
            )
        elif assignment.group("value").startswith("("):
            value = _typed_array_alias(
                _known_array_type_marker(aliases.get(name, ""))
                or _INDEXED_ARRAY_ALIAS_MARKER,
                value,
            )
        elif (
            marker := _known_array_type_marker(aliases.get(name, ""))
        ) is not None:
            value = _typed_array_alias(marker, value)
        aliases[name] = value
        written.add(name)
    return False, written


def _function_local_declaration_names(
    resolved_tokens: tuple[str, ...],
) -> set[str]:
    normalized = _normalize_shell_builtin_wrappers(resolved_tokens)
    if not normalized:
        return set()
    executable = posixpath.basename(normalized[0])
    if executable not in {"declare", "local", "typeset"}:
        return set()
    arguments = normalized[1:]
    global_declaration = False
    names: set[str] = set()
    after_options = False
    for argument in arguments:
        if argument == "--":
            after_options = True
            continue
        if not after_options and argument.startswith(("-", "+")):
            if executable != "local" and "g" in argument[1:]:
                global_declaration = True
            continue
        scalar = _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(argument)
        indexed = _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(argument)
        if scalar or indexed:
            assignment = scalar or indexed
            assert assignment is not None
            names.add(assignment.group("name"))
            continue
        target = _shell_alias_write_target(argument)
        if target is not None:
            names.add(target[0])
    return set() if global_declaration else names


def _function_body_has_controlled_alias_writes(
    body: tuple[_ShellCommandRecord, ...],
) -> bool:
    has_control_flow = any(
        record.text.split(maxsplit=1)[0] in _SHELL_STRUCTURE_TOKENS
        for record in body
        if record.text.split()
    )
    if not has_control_flow:
        return False
    for record in body:
        command = record.text
        if command.split(maxsplit=1)[0] in {"for", "select"}:
            return True
        tokens = _parse_shell_tokens(
            command,
            label="function control-flow write",
        )
        if tokens and all(
            _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(token.text)
            or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(token.text)
            for token in tokens
        ):
            return True
        normalized = _normalize_shell_builtin_wrappers(
            _token_texts(tokens)
        )
        if normalized and posixpath.basename(normalized[0]) in {
            "declare",
            "export",
            "local",
            "mapfile",
            "printf",
            "read",
            "readarray",
            "readonly",
            "typeset",
        }:
            return True
    return False


def _analyze_function_call(
    function_name: str,
    *,
    function_bodies: dict[str, tuple[_ShellCommandRecord, ...]],
    aliases: dict[str, str],
    arguments: tuple[str, ...],
    trusted_helper_inventory: bool,
    available_functions: set[str],
    call_stack: tuple[str, ...] = (),
) -> tuple[bool, dict[str, str]]:
    if function_name in call_stack:
        return True, {}
    body = function_bodies.get(function_name)
    if body is None:
        return False, {}
    if _function_body_has_controlled_alias_writes(body):
        return True, {}
    function_aliases = {
        name: value
        for name, value in aliases.items()
        if not name.isdigit()
    }
    call_arguments = []
    for argument in arguments:
        if argument in {">", ">>", "<", "<<", "<<<"}:
            break
        call_arguments.append(argument)
    for index, argument in enumerate(call_arguments, start=1):
        function_aliases[str(index)] = argument
    local_names: set[str] = set()
    global_updates: dict[str, str] = {}
    for record in body:
        if (
            not trusted_helper_inventory
            and (
                record.execution_scopes
                or record.preceding_operator in {"&&", "||", "|", "|&", "&"}
                or record.following_operator in {"&&", "||", "|", "|&", "&"}
            )
        ):
            return True, {}
        command = record.text
        tokens = _split_attached_redirections(
            _parse_shell_tokens(
                command,
                label=f"{function_name} call-time body",
            )
        )
        if any(_token_has_nested_braced_parameter(token) for token in tokens):
            return True, {}
        resolved = _resolve_tokens_for_alias_state(
            tokens,
            function_aliases,
        )
        if _arithmetic_writes_protected_state(
            tokens,
            resolved,
            function_aliases,
        ):
            return True, {}
        if _command_mutates_unreviewed_shell_state(
            resolved,
            original_tokens=_token_texts(tokens),
        ):
            return True, {}
        reviewed_transport_write = _is_reviewed_transport_output_write(
            function_name,
            resolved,
        )
        if (
            _active_reserved_transport_references(
                tokens,
                resolved,
                function_aliases,
            )
            and not reviewed_transport_write
        ):
            return True, {}
        for original_token in tokens:
            token = _resolve_shell_token(
                original_token,
                function_aliases,
            )
            if (
                _RAW_CGROUP_ROOT_MARKER in token
                and _raw_cgroup_suffix_has_dynamic_filename(token)
            ):
                return True, {}
            if _ambiguous_alias_suffix_has_dynamic_filename(token):
                return True, {}
            if "cgroup.procs" in token:
                return True, {}
        if (
            _normalized_command_mutates_supervisor(resolved)
            or _normalized_command_mutates_dispatch_state(resolved)
            or _normalized_command_changes_dispatch(resolved)
        ):
            return True, {}
        if (
            _normalized_command_mutates_reserved_transport(resolved)
            and not reviewed_transport_write
        ):
            return True, {}
        normalized = _normalize_shell_builtin_wrappers(resolved)
        if (
            normalized
            and posixpath.basename(normalized[0]) == "trap"
        ):
            return True, {}
        if not resolved:
            continue
        callee = posixpath.basename(resolved[0])
        if callee in function_bodies:
            if callee not in available_functions:
                return True, {}
            failed, updates = _analyze_function_call(
                callee,
                function_bodies=function_bodies,
                aliases=function_aliases,
                arguments=resolved[1:],
                trusted_helper_inventory=trusted_helper_inventory,
                available_functions=available_functions,
                call_stack=call_stack + (function_name,),
            )
            if failed:
                return True, {}
            function_aliases.update(updates)
            global_updates.update(
                {
                    name: value
                    for name, value in updates.items()
                    if name not in local_names
                }
            )
        preexisting_local_names = set(local_names)
        local_names.update(
            _function_local_declaration_names(resolved)
        )
        before = dict(function_aliases)
        failed, directly_written = _apply_direct_function_alias_writes(
            tokens,
            function_aliases,
        )
        if failed:
            return True, {}
        if _apply_shell_builtin_alias_writes(
            resolved,
            function_aliases,
            original_shell_tokens=tokens,
            preexisting_local_names=preexisting_local_names,
        ):
            return True, {}
        changed = directly_written | {
            name
            for name, value in function_aliases.items()
            if before.get(name) != value
        }
        global_updates.update(
            {
                name: function_aliases[name]
                for name in changed
                if name not in local_names and not name.isdigit()
            }
        )
    return False, global_updates


def _helper_call_has_sensitive_arguments(
    tokens: tuple[str, ...],
    *,
    user_functions: set[str],
    function_bodies: dict[str, tuple[_ShellCommandRecord, ...]],
    aliases: dict[str, str],
    trusted_helper_inventory: bool,
) -> bool:
    tokens = _strip_shell_command_prefixes(tokens)
    if not tokens:
        return False
    executable = tokens[0]
    basename = posixpath.basename(executable)
    if (
        basename in function_bodies
        and basename not in user_functions
    ):
        return True
    production_calls = {
        (
            "read_checked_supervisor_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_supervisor_transport_file dev-mount-targets)",
            "1048576",
        ),
        (
            "remove_supervisor_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_supervisor_transport_file dev-mount-targets)",
        ),
        (
            "read_checked_supervisor_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_supervisor_transport_file remaining-dev-mount-targets)",
            "1048576",
        ),
        (
            "remove_supervisor_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_supervisor_transport_file remaining-dev-mount-targets)",
        ),
        (
            "read_checked_runtime_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_runtime_transport_file writable-mount-records)",
            "1048576",
        ),
        (
            "remove_runtime_transport_file",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(create_runtime_transport_file writable-mount-records)",
        ),
        (
            "builder_main",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER + "$@",
        ),
    }
    is_production_call = (
        tokens in production_calls and basename in user_functions
    )
    if is_production_call and basename == "builder_main":
        return False
    if basename in user_functions:
        failed, updates = _analyze_function_call(
            basename,
            function_bodies=function_bodies,
            aliases=aliases,
            arguments=tokens[1:],
            trusted_helper_inventory=trusted_helper_inventory,
            available_functions=user_functions,
        )
        if failed:
            return True
        aliases.update(updates)
        return False
    if is_production_call:
        return False
    raw_root_signatures = {
        (
            "/usr/bin/mount",
            "--bind",
            _RAW_CGROUP_ROOT_MARKER,
            "/mnt/supervisor/cgroup",
        ),
        (
            "test",
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
            + "$(/usr/bin/stat -c %u "
            + _RAW_CGROUP_ROOT_MARKER
            + ")",
            "=",
            "0",
        ),
    }
    if tokens in raw_root_signatures:
        return False
    has_raw_root = any(
        _RAW_CGROUP_ROOT_MARKER in token for token in tokens
    )
    if has_raw_root:
        plain_executable = executable
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
            _RAW_CGROUP_ROOT_MARKER,
        ):
            plain_executable = plain_executable.replace(marker, "")
        if (
            _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(plain_executable)
            or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(plain_executable)
        ):
            return False
        normalized = _normalize_shell_builtin_wrappers(tokens)
        if normalized:
            normalized_executable = posixpath.basename(normalized[0])
            if normalized_executable in {
                "declare",
                "export",
                "local",
                "readonly",
                "typeset",
            }:
                return False
            if normalized_executable == "printf" and "-v" in normalized[1:]:
                return False
        return True
    if (
        basename in _ANALYZED_BUILDER_COMMANDS
        and basename not in user_functions
    ):
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", basename):
        plain_executable = executable
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
            _RAW_CGROUP_ROOT_MARKER,
        ):
            plain_executable = plain_executable.replace(marker, "")
        if (
            _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(plain_executable)
            or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(plain_executable)
        ):
            return False
        return any(
            marker in executable
            for marker in (
                _AMBIGUOUS_ARRAY_ALIAS_MARKER,
                _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
                _RAW_CGROUP_ROOT_MARKER,
            )
        )
    arguments = list(tokens[1:])
    for index, argument in enumerate(arguments):
        if argument in {">", ">>", "<", "<<", "<<<"}:
            arguments = arguments[:index]
            break
    if any(
        marker in argument
        for argument in arguments
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
            _RAW_CGROUP_ROOT_MARKER,
        )
    ):
        return True
    joined = "".join(arguments)
    return "cgroup.procs" in joined or (
        "cgroup" in joined
        and "proc" in joined
        and any(
            marker in joined
            for marker in _RAW_CGROUP_DYNAMIC_FILENAME_MARKERS
        )
    )


def _raw_cgroup_suffix_has_dynamic_filename(text: str) -> bool:
    return any(
        marker in suffix
        for suffix in text.split(_RAW_CGROUP_ROOT_MARKER)[1:]
        for marker in _RAW_CGROUP_DYNAMIC_FILENAME_MARKERS
    )


def _ambiguous_alias_suffix_has_dynamic_filename(text: str) -> bool:
    return any(
        marker in suffix
        for alias_marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
        )
        for suffix in text.split(alias_marker)[1:]
        if "cgroup" in suffix and "proc" in suffix
        for marker in _RAW_CGROUP_DYNAMIC_FILENAME_MARKERS
    )


def _assignment_value_has_dynamic_shell_syntax(
    value: str,
    *,
    token_has_shell_syntax: bool,
) -> bool:
    if not token_has_shell_syntax:
        return False
    if (
        value.startswith("(")
        and value.endswith(")")
        and not any(
            marker in value
            for marker in (
                "$",
                "`",
                "*",
                "?",
                "{",
                "}",
                "~",
                "<(",
                ">(",
                "+(",
                "@(",
                "!(",
            )
        )
    ):
        return False
    dynamic_parameter = False

    def strip_braced(match: re.Match[str]) -> str:
        nonlocal dynamic_parameter
        body = match.group("body")
        if re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*|[1-9][0-9]*)",
            body,
        ):
            return ""
        dynamic_parameter = True
        return ""

    remainder = _SHELL_BRACED_PARAMETER_RE.sub(strip_braced, value)
    remainder = _SHELL_PLAIN_VARIABLE_REFERENCE_RE.sub("", remainder)
    remainder = _SHELL_POSITIONAL_REFERENCE_RE.sub("", remainder)
    if dynamic_parameter:
        return True
    return any(
        marker in remainder
        for marker in _RAW_CGROUP_DYNAMIC_FILENAME_MARKERS
    )


def _is_shell_assignment_word(token: str) -> bool:
    plain = token
    for marker in (
        _AMBIGUOUS_ARRAY_ALIAS_MARKER,
        _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
        _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
        _RAW_CGROUP_ROOT_MARKER,
    ):
        plain = plain.replace(marker, "")
    return bool(
        _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(plain)
        or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(plain)
    )


def _strip_shell_command_prefixes(
    tokens: tuple[str, ...],
) -> tuple[str, ...]:
    stripped = tokens
    while stripped:
        changed = False
        while stripped and stripped[0] in _SIMPLE_COMMAND_PREFIXES:
            stripped = stripped[1:]
            changed = True
        while stripped and _is_shell_assignment_word(stripped[0]):
            stripped = stripped[1:]
            changed = True
        if not changed:
            break
    return stripped


def _leading_assignment_indices(
    tokens: tuple[_ShellToken, ...],
) -> set[int]:
    index = 0
    while (
        index < len(tokens)
        and tokens[index].text in _SIMPLE_COMMAND_PREFIXES
    ):
        index += 1
    indices: set[int] = set()
    while index < len(tokens) and (
        _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(tokens[index].text)
        or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(tokens[index].text)
    ):
        indices.add(index)
        index += 1
    return indices


def _resolve_tokens_for_alias_state(
    tokens: tuple[_ShellToken, ...],
    aliases: dict[str, str],
) -> tuple[str, ...]:
    resolved_tokens = []
    for token_index, token in enumerate(tokens):
        scalar_assignment = _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(
            token.text
        )
        indexed_assignment = _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(
            token.text
        )
        dynamic_token = token
        if scalar_assignment is not None:
            dynamic_token = _slice_shell_token(
                token,
                scalar_assignment.start("value"),
            )
        elif indexed_assignment is not None:
            dynamic_token = _slice_shell_token(
                token,
                indexed_assignment.start("value"),
            )
        dynamic_text = _active_shell_token_text(dynamic_token)
        dynamic = _assignment_value_has_dynamic_shell_syntax(
            dynamic_text,
            token_has_shell_syntax=dynamic_token.has_shell_syntax,
        )
        if (
            token_index == 0
            and token.text.endswith(")")
            and not token.text.startswith(
                ("$(", "`", "<(", ">(", "+(", "@(", "!(")
            )
        ):
            dynamic = False
        resolved_tokens.append(
            (_AMBIGUOUS_DYNAMIC_ALIAS_MARKER if dynamic else "")
            + _resolve_shell_token(token, aliases)
        )
    return tuple(resolved_tokens)


def _normalize_shell_builtin_wrappers(
    tokens: tuple[str, ...],
) -> tuple[str, ...] | None:
    normalized = _strip_shell_command_prefixes(tokens)
    for _depth in range(8):
        normalized = _strip_shell_command_prefixes(normalized)
        if not normalized:
            return ()
        executable = posixpath.basename(normalized[0])
        if executable == "builtin":
            index = 1
            if index < len(normalized) and normalized[index] == "--":
                index += 1
            normalized = normalized[index:]
            continue
        if executable != "command":
            return normalized
        index = 1
        query_only = False
        while index < len(normalized):
            option = normalized[index]
            if option == "--":
                index += 1
                break
            if option.startswith("-") and option != "-":
                flags = option[1:]
                if not flags or any(
                    flag not in {"p", "v", "V"} for flag in flags
                ):
                    return None
                if "v" in flags or "V" in flags:
                    query_only = True
                index += 1
                continue
            break
        if query_only:
            return ()
        normalized = normalized[index:]
    return None


def _normalized_command_mutates_supervisor(
    tokens: tuple[str, ...],
) -> bool:
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None:
        return any("supervisor_cgroup" in token for token in tokens)
    if not normalized:
        return False
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]
    if normalized[0] in {".", "eval", "source"}:
        return True
    if executable == "unset":
        return any(
            argument == "supervisor_cgroup"
            or argument.startswith("supervisor_cgroup[")
            for argument in arguments
            if not argument.startswith("-")
        )
    if executable == "read":
        return any(argument == "supervisor_cgroup" for argument in arguments)
    if executable in {"mapfile", "readarray"}:
        return any(
            argument == "supervisor_cgroup" for argument in arguments
        )
    if executable == "printf":
        for index, argument in enumerate(arguments[:-1]):
            if argument == "-v" and arguments[index + 1] == "supervisor_cgroup":
                return True
    if executable in {
        "declare",
        "export",
        "local",
        "readonly",
        "typeset",
    }:
        return any(
            argument == "supervisor_cgroup"
            or argument.startswith("supervisor_cgroup=")
            or argument.startswith("supervisor_cgroup+=")
            or argument.startswith("supervisor_cgroup[")
            for argument in arguments
            if not argument.startswith("-")
        )
    return False


def _normalized_command_mutates_cgroup_path(
    tokens: tuple[str, ...],
) -> bool:
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None:
        return any(
            "cgroup_path" in token
            or _AMBIGUOUS_ARRAY_ALIAS_MARKER in token
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in token
            for token in tokens
        )
    if not normalized:
        return False
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]

    def target_is_protected(target: str) -> bool:
        target_name = target.split("+=", 1)[0]
        target_name = target_name.split("=", 1)[0]
        target_name = target_name.split("[", 1)[0]
        return (
            target_name == "cgroup_path"
            or _AMBIGUOUS_ARRAY_ALIAS_MARKER in target_name
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in target_name
            or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in target_name
        )

    if normalized[0] in {".", "eval", "source"}:
        return True
    if executable == "unset":
        return any(
            target_is_protected(argument)
            for argument in arguments
            if not argument.startswith(("-", "+"))
        )
    if executable in {"read", "mapfile", "readarray"}:
        mutation_arguments = arguments
        for index, argument in enumerate(mutation_arguments):
            if _is_redirection_token(argument):
                mutation_arguments = mutation_arguments[:index]
                break
        return any(
            target_is_protected(argument)
            for argument in mutation_arguments
            if not argument.startswith(("-", "+"))
        )
    if executable == "printf":
        return any(
            argument == "-v"
            and target_is_protected(arguments[index + 1])
            for index, argument in enumerate(arguments[:-1])
        )
    if executable in {
        "declare",
        "export",
        "local",
        "readonly",
        "typeset",
    }:
        return any(
            target_is_protected(argument)
            for argument in arguments
            if not argument.startswith(("-", "+"))
        )
    return False


def _normalized_command_mutates_reserved_transport(
    tokens: tuple[str, ...],
) -> bool:
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if not normalized:
        return normalized is None
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]

    def reserved(target: str) -> bool:
        name = target.split("+=", 1)[0]
        name = name.split("=", 1)[0]
        name = name.split("[", 1)[0]
        return (
            name in _RESERVED_TRANSPORT_OUTPUTS
            or _AMBIGUOUS_ARRAY_ALIAS_MARKER in name
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in name
            or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in name
        )

    if executable == "unset":
        return any(reserved(arg) for arg in arguments if not arg.startswith("-"))
    if executable in {"read", "mapfile", "readarray"}:
        targets = arguments
        for index, argument in enumerate(targets):
            if _is_redirection_token(argument):
                targets = targets[:index]
                break
        return any(reserved(arg) for arg in targets if not arg.startswith("-"))
    if executable == "printf":
        return any(
            arg == "-v" and reserved(arguments[index + 1])
            for index, arg in enumerate(arguments[:-1])
        )
    if executable in {"declare", "export", "local", "readonly", "typeset"}:
        return any(
            reserved(arg)
            for arg in arguments
            if not arg.startswith(("-", "+"))
        )
    return False


def _is_reviewed_transport_output_write(
    function_name: str,
    tokens: tuple[str, ...],
) -> bool:
    expected_target = {
        "read_checked_runtime_transport_file": (
            "checked_runtime_transport_output"
        ),
        "read_checked_supervisor_transport_file": (
            "checked_supervisor_transport_output"
        ),
    }.get(function_name)
    normalized = _normalize_shell_builtin_wrappers(tokens)
    return bool(
        expected_target
        and normalized
        and len(normalized) >= 5
        and normalized[:5]
        == ("mapfile", "-d", "", "-t", expected_target)
        and "<" in normalized[5:]
    )


def _active_reserved_transport_references(
    tokens: tuple[_ShellToken, ...],
    resolved_tokens: tuple[str, ...],
    aliases: dict[str, str],
) -> frozenset[str]:
    references: set[str] = set()
    for token, resolved in zip(tokens, resolved_tokens):
        active = _active_shell_token_text(token)
        braced_parameters = tuple(
            re.findall(
                r"\$\{(?P<prefix>[!#]?)(?P<name>"
                r"[A-Za-z_][A-Za-z0-9_]*|[1-9][0-9]*)"
                r"(?P<suffix>[^}]*)\}",
                active,
            )
        )
        for name in _RESERVED_TRANSPORT_OUTPUTS:
            escaped = re.escape(name)
            if re.search(
                rf"\${escaped}(?![A-Za-z0-9_])",
                active,
            ) or any(
                subject == name
                and not (
                    prefix == "!"
                    and suffix in {"*", "@"}
                )
                for prefix, subject, suffix in braced_parameters
            ):
                references.add(name)
                continue
            for prefix, target, suffix in braced_parameters:
                if prefix != "!" or suffix in {"*", "@"}:
                    continue
                value = _semantic_alias_value(aliases.get(target))
                if suffix.startswith("["):
                    if (
                        value is not None
                        and _NAMEREF_ALIAS_MARKER in value
                        and (
                            re.search(rf"\b{escaped}\b", value)
                            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in value
                            or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in value
                        )
                    ):
                        references.add(name)
                    continue
                if _alias_value_is_runtime_dynamic(value):
                    references.add(name)
                    break
                if re.fullmatch(
                    rf"{escaped}(?:\[[^\]]*\])?",
                    value,
                ):
                    references.add(name)
                    break
            if name in references:
                continue
            if (
                _NAMEREF_ALIAS_MARKER in resolved
                and (
                    re.search(rf"\b{escaped}\b", resolved)
                    or any(
                        marker in resolved
                        for marker in (
                            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
                            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
                        )
                    )
                    or any(
                        character in resolved
                        for character in ("$", "`")
                    )
                )
            ):
                references.add(name)
                continue
            arithmetic_context = any(
                opener in active
                for opener in ("$((", "((", "$[")
            )
            subscript_owners = re.findall(
                r"\$\{[!#]?([A-Za-z_][A-Za-z0-9_]*)\[",
                active,
            )
            assignment_owner = re.match(
                r"([A-Za-z_][A-Za-z0-9_]*)\[[^\]]*\](?:\+)?=",
                active,
            )
            if assignment_owner is not None:
                subscript_owners.append(assignment_owner.group(1))
            arithmetic_subscript = bool(subscript_owners) and not all(
                _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                in aliases.get(owner, "")
                for owner in subscript_owners
            )
            if arithmetic_context and re.search(
                rf"\b{escaped}\b",
                resolved,
            ):
                references.add(name)
                continue
            if arithmetic_subscript and re.search(
                rf"\b{escaped}\b",
                resolved,
            ):
                references.add(name)
                continue
            if arithmetic_context or arithmetic_subscript:
                dereference_text = active if arithmetic_context else (
                    " ".join(re.findall(r"\[([^\]]*)\]", active))
                )
                for identifier in re.findall(
                    r"\b[A-Za-z_][A-Za-z0-9_]*\b",
                    dereference_text,
                ):
                    value = _semantic_alias_value(
                        aliases.get(identifier)
                    )
                    if value is None:
                        value = identifier
                    if _alias_value_is_runtime_dynamic(
                        value
                    ) or re.fullmatch(
                        rf"{escaped}(?:\[[^\]]*\])?",
                        value,
                    ):
                        references.add(name)
                        break
    return frozenset(references)


def _arithmetic_expressions(
    tokens: tuple[_ShellToken, ...],
    resolved_tokens: tuple[str, ...],
) -> tuple[str, ...]:
    expressions: list[str] = []
    normalized = _strip_shell_command_prefixes(resolved_tokens)
    if normalized and posixpath.basename(normalized[0]) == "let":
        expressions.extend(normalized[1:])
    if (
        normalized
        and posixpath.basename(normalized[0])
        in {"declare", "local", "readonly", "typeset"}
        and any(
            option.startswith("-") and "i" in option[1:]
            for option in normalized[1:]
        )
    ):
        expressions.extend(
            argument.split("=", 1)[1]
            for argument in normalized[1:]
            if "=" in argument
        )
    for token, resolved in zip(tokens, resolved_tokens):
        active = _active_shell_token_text(token)
        if active.startswith("((") and active.endswith("))"):
            start = resolved.find("((")
            end = resolved.rfind("))")
            expressions.append(resolved[start + 2 : end])
        expressions.extend(
            match.group(1) or match.group(2)
            for match in re.finditer(
                r"\$\(\((.*)\)\)|\$\[(.*)\]",
                resolved if "$" in active else "",
            )
        )
    return tuple(expressions)


def _arithmetic_writes_protected_state(
    tokens: tuple[_ShellToken, ...],
    resolved_tokens: tuple[str, ...],
    aliases: dict[str, str],
) -> bool:
    protected = {
        "cgroup_path",
        "supervisor_cgroup",
        *_DISPATCH_STATE_VARIABLES,
        *_RESERVED_TRANSPORT_OUTPUTS,
    }
    write_re = re.compile(
        r"(?:"
        r"(?:\+\+|--)\s*(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)"
        r"|(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\[[^\]]*\])?\s*"
        r"(?:\+\+|--|<<=|>>=|\*\*=|[+\-*/%&|^]=|=(?!=))"
        r")"
    )
    security_markers = (
        _RAW_CGROUP_ROOT_MARKER,
        _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
        _AMBIGUOUS_ARRAY_ALIAS_MARKER,
        _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
    )
    marker_write_re = re.compile(
        "(?:"
        + "|".join(re.escape(marker) for marker in security_markers)
        + r")+(?:\[[^\]]*\])?\s*"
        + r"(?:\+\+|--|<<=|>>=|\*\*=|[+\-*/%&|^]=|=(?!=))"
    )
    for expression in _arithmetic_expressions(tokens, resolved_tokens):
        if marker_write_re.search(expression):
            return True
        scan_expressions = (expression,) + tuple(
            re.findall(r"\[([^\]]*)\]", expression)
        )
        for scan_expression in scan_expressions:
            for match in write_re.finditer(scan_expression):
                name = match.group("prefix") or match.group("suffix")
                attributed = aliases.get(name, "")
                if _READONLY_ALIAS_MARKER in attributed:
                    continue
                group_name = (
                    "prefix" if match.group("prefix") else "suffix"
                )
                if (
                    name in protected
                    or (
                        match.start(group_name) > 0
                        and scan_expression[match.start(group_name) - 1]
                        == "$"
                        and name not in aliases
                    )
                ):
                    return True
                value = _semantic_alias_value(attributed) or ""
                if (
                    any(marker in value for marker in security_markers)
                    or _NAMEREF_ALIAS_MARKER in value
                    and any(target in value for target in protected)
                    or value in _RESERVED_TRANSPORT_OUTPUTS
                ):
                    return True
    return False


def _command_mutates_unreviewed_shell_state(
    tokens: tuple[str, ...],
    *,
    original_tokens: tuple[str, ...],
) -> bool:
    stripped = _strip_shell_command_prefixes(tokens)
    normalized = _normalize_shell_builtin_wrappers(stripped)
    if not normalized:
        return normalized is None and any(
            posixpath.basename(token) in {
                "coproc",
                "getopts",
                "set",
                "shift",
            }
            for token in stripped
        )
    executable = posixpath.basename(normalized[0])
    if executable == "wait":
        if original_tokens in {
            (
                "wait",
                "$builder_supervisor_wait_pid",
                ">",
                "/dev/null",
                "2>",
            ),
            ("wait", "$builder_supervisor_pid"),
        }:
            return False
        for argument in normalized[1:]:
            if _is_redirection_token(argument) or argument == "--":
                break
            if any(
                marker in argument
                for marker in (
                    _AMBIGUOUS_ARRAY_ALIAS_MARKER,
                    _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                    _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
                )
            ) or any(character in argument for character in ("$", "`")):
                return True
            if argument.startswith("-"):
                if "p" in argument[1:]:
                    return True
                continue
            break
        return False
    if executable in {"coproc", "getopts", "shift"}:
        return True
    if executable != "set":
        return False
    arguments = normalized[1:]
    for index, argument in enumerate(arguments):
        if _is_redirection_token(argument):
            arguments = arguments[:index]
            break
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return True
        if argument in {"-", "+"}:
            index += 1
            continue
        if argument.startswith("--"):
            return True
        if not argument.startswith(("-", "+")):
            return True
        options = argument[1:]
        if "o" in options:
            option_index = options.index("o")
            if any(
                option not in "abefhkmnptuvxBCEHPT"
                for option in options[:option_index]
            ):
                return True
            option_name = options[option_index + 1 :]
            if not option_name and index + 1 < len(arguments):
                candidate = arguments[index + 1]
                if not candidate.startswith(("-", "+")):
                    option_name = candidate
                    index += 1
            if (
                option_name
                and option_name not in _VALID_SET_OPTION_NAMES
            ):
                return True
        elif any(
            option not in "abefhkmnptuvxBCEHPT"
            for option in options
        ):
            return True
        index += 1
    return False


def _normalized_command_creates_nameref(
    tokens: tuple[str, ...],
) -> bool:
    normalized = _normalize_shell_builtin_wrappers(tokens)
    declaration_builtins = {
        "declare",
        "export",
        "local",
        "readonly",
        "typeset",
    }
    if normalized is None:
        return (
            any(
                posixpath.basename(token) in declaration_builtins
                for token in tokens
            )
            and any(
                _AMBIGUOUS_ARRAY_ALIAS_MARKER in token
                or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in token
                or (
                    token.startswith(("-", "+"))
                    and "n" in token[1:]
                )
                for token in tokens
            )
        )
    if not normalized:
        return False
    if posixpath.basename(normalized[0]) not in declaration_builtins:
        return False
    for option in normalized[1:]:
        if option == "--":
            break
        if (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER in option
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in option
        ):
            return True
        if option.startswith(("-", "+")) and option not in {"-", "+"}:
            if "n" in option[1:]:
                return True
            continue
        break
    return False


def _normalized_command_changes_dispatch(
    tokens: tuple[str, ...],
) -> bool:
    dispatch_builtins = {
        "alias",
        "enable",
        "hash",
        "shopt",
        "unalias",
    }
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None:
        return any(
            posixpath.basename(token) in dispatch_builtins
            or _AMBIGUOUS_ARRAY_ALIAS_MARKER in token
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in token
            for token in tokens
        )
    if not normalized:
        return False
    executable = posixpath.basename(normalized[0])
    if executable in dispatch_builtins:
        return True
    if executable == "env":
        return any(
            "=" in argument
            and _dispatch_state_target_is_forbidden(argument)
            for argument in normalized[1:]
        )
    if executable != "set":
        return False
    arguments = normalized[1:]
    for index, argument in enumerate(arguments):
        if (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER in argument
            or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in argument
        ):
            return True
        if argument in {"-h", "+h"}:
            return True
        if argument in {"-o", "+o"}:
            if index + 1 >= len(arguments):
                continue
            option = arguments[index + 1]
            if (
                _AMBIGUOUS_ARRAY_ALIAS_MARKER in option
                or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in option
                or option in _DISPATCH_SET_OPTIONS
            ):
                return True
            continue
        if argument.startswith(("-o", "+o")) and len(argument) > 2:
            option = argument[2:]
            if option in _DISPATCH_SET_OPTIONS:
                return True
        if (
            argument.startswith(("-", "+"))
            and not argument.startswith(("-o", "+o"))
            and "h" in argument[1:]
        ):
            return True
    return False


def _dispatch_state_target_is_forbidden(target: str) -> bool:
    target_name = target.split("+=", 1)[0]
    target_name = target_name.split("=", 1)[0]
    base = target_name.split("[", 1)[0]
    if (
        _AMBIGUOUS_ARRAY_ALIAS_MARKER in base
        or _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in base
        or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in base
    ):
        return True
    return base in _DISPATCH_STATE_VARIABLES


def _normalized_command_mutates_dispatch_state(
    tokens: tuple[str, ...],
) -> bool:
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None:
        return any(
            _dispatch_state_target_is_forbidden(token)
            for token in tokens
        )
    if not normalized:
        return False
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]
    if executable == "unset":
        return any(
            _dispatch_state_target_is_forbidden(argument)
            for argument in arguments
            if not argument.startswith("-")
        )
    if executable == "read":
        mutation_arguments = arguments
        if "<" in mutation_arguments:
            mutation_arguments = mutation_arguments[
                : mutation_arguments.index("<")
            ]
        return any(
            _dispatch_state_target_is_forbidden(argument)
            for argument in mutation_arguments
            if not argument.startswith("-")
        )
    if executable in {"mapfile", "readarray"}:
        mutation_arguments = arguments
        if "<" in mutation_arguments:
            mutation_arguments = mutation_arguments[
                : mutation_arguments.index("<")
            ]
        return any(
            _dispatch_state_target_is_forbidden(argument)
            for argument in mutation_arguments
            if not argument.startswith("-")
        )
    if executable == "printf":
        for index, argument in enumerate(arguments[:-1]):
            if argument == "-v" and _dispatch_state_target_is_forbidden(
                arguments[index + 1]
            ):
                return True
        return False
    if executable in {
        "declare",
        "export",
        "local",
        "readonly",
        "typeset",
    }:
        return any(
            _dispatch_state_target_is_forbidden(argument)
            for argument in arguments
            if not argument.startswith(("-", "+"))
        )
    return False


def _ambiguous_array_command_is_forbidden(
    tokens: tuple[str, ...],
) -> bool:
    ambiguous_indices = tuple(
        index
        for index, token in enumerate(tokens)
        if _AMBIGUOUS_ARRAY_ALIAS_MARKER in token
    )
    if not ambiguous_indices:
        return False
    if 0 in ambiguous_indices:
        plain_executable = tokens[0]
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
        ):
            plain_executable = plain_executable.replace(marker, "")
        if not (
            _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(plain_executable)
            or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(plain_executable)
        ):
            return True
    if tokens and posixpath.basename(tokens[0]) in {
        "builtin",
        "command",
    }:
        return True
    return any(
        "supervisor_cgroup" in token or "cgroup.procs" in token
        for token in tokens
    )


def _ambiguous_dynamic_command_is_forbidden(
    tokens: tuple[str, ...],
) -> bool:
    ambiguous_indices = tuple(
        index
        for index, token in enumerate(tokens)
        if _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in token
    )
    if not ambiguous_indices:
        return False
    if 0 in ambiguous_indices:
        return True
    if tokens and posixpath.basename(tokens[0]) in {
        "builtin",
        "command",
    }:
        return True
    if any(
        "supervisor_cgroup" in token or "cgroup.procs" in token
        for token in tokens
    ):
        return True
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None or not normalized:
        return bool(normalized is None)
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]
    if executable in {
        "declare",
        "eval",
        "export",
        "local",
        "readonly",
        "source",
        "typeset",
        "unset",
    } or normalized[0] == ".":
        return any(
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in argument
            for argument in arguments
        )
    if executable in {"mapfile", "read", "readarray"}:
        mutation_arguments = arguments
        if "<" in mutation_arguments:
            mutation_arguments = mutation_arguments[
                : mutation_arguments.index("<")
            ]
        return any(
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in argument
            for argument in mutation_arguments
        )
    if executable == "printf":
        if arguments and _AMBIGUOUS_DYNAMIC_ALIAS_MARKER in arguments[0]:
            return True
        for index, argument in enumerate(arguments[:-1]):
            if argument == "-v" and (
                _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
                in arguments[index + 1]
            ):
                return True
    return False


def _shell_alias_write_target(
    target: str,
) -> tuple[str, bool] | None:
    if any(
        marker in target
        for marker in (
            _AMBIGUOUS_ARRAY_ALIAS_MARKER,
            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
            _AMBIGUOUS_TRACKED_PARAMETER_MARKER,
            _RAW_CGROUP_ROOT_MARKER,
        )
    ):
        raise ValueError("dynamic shell alias write target")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
        return target, False
    indexed = re.fullmatch(
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\[[^\]]+\]",
        target,
    )
    if indexed is not None:
        return indexed.group("name"), True
    return None


def _set_written_shell_alias(
    aliases: dict[str, str],
    target: str,
    value: str,
    *,
    array: bool = False,
    associative: bool = False,
) -> bool:
    parsed = _shell_alias_write_target(target)
    if parsed is None:
        return False
    name, indexed = parsed
    if name in {
        "cgroup_path",
        "supervisor_cgroup",
    } or name in _DISPATCH_STATE_VARIABLES:
        raise ValueError("trusted shell state write")
    if _READONLY_ALIAS_MARKER in aliases.get(name, ""):
        return True
    if array:
        marker = (
            _ASSOCIATIVE_ARRAY_ALIAS_MARKER
            if associative
            else _INDEXED_ARRAY_ALIAS_MARKER
        )
        aliases[name] = _typed_array_alias(marker, value)
    elif indexed:
        previous = aliases.get(name, "")
        marker = _known_array_type_marker(previous)
        aliases[name] = (
            _typed_array_alias(marker, value)
            if marker is not None
            else _AMBIGUOUS_ARRAY_ALIAS_MARKER + value
        )
    else:
        aliases[name] = value
    return True


def _read_builtin_targets(
    arguments: tuple[str, ...],
) -> tuple[tuple[str, bool], ...] | None:
    targets: list[tuple[str, bool]] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if _is_redirection_token(argument):
            break
        if argument == "--":
            index += 1
            break
        if not argument.startswith("-") or argument == "-":
            break
        flags = argument[1:]
        flag_index = 0
        while flag_index < len(flags):
            flag = flags[flag_index]
            if flag not in {"a", "d", "e", "i", "n", "N", "p", "r", "s", "t", "u"}:
                return None
            if flag in {"a", "d", "i", "n", "N", "p", "t", "u"}:
                attached = flags[flag_index + 1 :]
                if attached:
                    option_value = attached
                else:
                    index += 1
                    if index >= len(arguments):
                        return None
                    option_value = arguments[index]
                if flag == "a":
                    targets.append((option_value, True))
                break
            flag_index += 1
        index += 1
    while index < len(arguments):
        argument = arguments[index]
        if _is_redirection_token(argument):
            break
        targets.append((argument, False))
        index += 1
    if not targets:
        targets.append(("REPLY", False))
    return tuple(targets)


def _mapfile_builtin_target(
    arguments: tuple[str, ...],
) -> str | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if _is_redirection_token(argument):
            break
        if argument == "--":
            index += 1
            break
        if not argument.startswith("-") or argument == "-":
            break
        flags = argument[1:]
        flag_index = 0
        while flag_index < len(flags):
            flag = flags[flag_index]
            if flag not in {"C", "O", "c", "d", "n", "s", "t", "u"}:
                raise ValueError("ambiguous mapfile option")
            if flag == "C":
                raise ValueError("mapfile callback is forbidden")
            if flag in {"C", "O", "c", "d", "n", "s", "u"}:
                if not flags[flag_index + 1 :]:
                    index += 1
                    if index >= len(arguments):
                        raise ValueError("missing mapfile option value")
                break
            flag_index += 1
        index += 1
    if index >= len(arguments) or _is_redirection_token(arguments[index]):
        return "MAPFILE"
    return arguments[index]


def _apply_shell_builtin_alias_writes(
    tokens: tuple[str, ...],
    aliases: dict[str, str],
    *,
    original_shell_tokens: tuple[_ShellToken, ...] | None = None,
    preexisting_local_names: set[str] | None = None,
) -> bool:
    if original_shell_tokens is not None:
        lexed = _split_attached_redirections(original_shell_tokens)
        tokens = _resolve_tokens_for_alias_state(lexed, aliases)
    normalized = _normalize_shell_builtin_wrappers(tokens)
    if normalized is None:
        return any(
            posixpath.basename(token)
            in {
                "declare",
                "export",
                "local",
                "mapfile",
                "printf",
                "read",
                "readarray",
                "readonly",
                "typeset",
            }
            for token in tokens
        )
    if not normalized:
        return False
    executable = posixpath.basename(normalized[0])
    arguments = normalized[1:]
    try:
        if executable == "unset":
            for argument in arguments:
                if argument.startswith("-"):
                    continue
                parsed = _shell_alias_write_target(argument)
                if parsed is None:
                    return True
                name, indexed = parsed
                if _READONLY_ALIAS_MARKER in aliases.get(name, ""):
                    continue
                if indexed:
                    marker = _known_array_type_marker(
                        aliases.get(name, "")
                    )
                    if marker is not None:
                        aliases[name] = _typed_array_alias(
                            marker,
                            _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                        )
                else:
                    aliases.pop(name, None)
            return False
        if executable == "printf":
            variable_option = next(
                (
                    index
                    for index, argument in enumerate(arguments[:-1])
                    if argument == "-v"
                ),
                None,
            )
            if variable_option is None:
                return False
            target = arguments[variable_option + 1]
            output_arguments = arguments[variable_option + 2 :]
            if not output_arguments:
                value = ""
            else:
                format_string = output_arguments[0]
                values = output_arguments[1:]
                if re.fullmatch(r"(?:%s)+", format_string):
                    value = "".join(values)
                elif (
                    "%" not in format_string
                    and "\\" not in format_string
                ):
                    value = format_string
                else:
                    value = (
                        _AMBIGUOUS_DYNAMIC_ALIAS_MARKER
                        + format_string
                        + "".join(values)
                    )
            _set_written_shell_alias(aliases, target, value)
            return False
        if executable == "read":
            targets = _read_builtin_targets(arguments)
            if targets is None:
                return True
            for target, array in targets:
                _set_written_shell_alias(
                    aliases,
                    target,
                    _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                    array=array,
                )
            return False
        if executable in {"mapfile", "readarray"}:
            target = _mapfile_builtin_target(arguments)
            if target is None:
                return True
            _set_written_shell_alias(
                aliases,
                target,
                _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                array=True,
            )
            return False
        if executable in {
            "declare",
            "export",
            "local",
            "readonly",
            "typeset",
        }:
            array = False
            associative = False
            nameref = False
            make_readonly = executable == "readonly"
            remove_readonly = False
            global_declaration = False
            after_options = False
            for argument in arguments:
                if argument == "--":
                    after_options = True
                    continue
                if not after_options and argument.startswith(("-", "+")):
                    if "a" in argument[1:] or "A" in argument[1:]:
                        array = True
                    if "A" in argument[1:]:
                        associative = True
                    if "n" in argument[1:]:
                        nameref = argument.startswith("-")
                    if "r" in argument[1:]:
                        make_readonly = argument.startswith("-")
                        remove_readonly = argument.startswith("+")
                    if "g" in argument[1:]:
                        global_declaration = argument.startswith("-")
                    continue
                scalar_assignment = _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(
                    argument
                )
                indexed_assignment = _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(
                    argument
                )
                if scalar_assignment or indexed_assignment:
                    assignment = scalar_assignment or indexed_assignment
                    assert assignment is not None
                    name = assignment.group("name")
                    value = assignment.group("value")
                    if nameref:
                        value = _NAMEREF_ALIAS_MARKER + value
                    if assignment.group("append"):
                        value = aliases.get(name, "") + value
                    target = name
                    if indexed_assignment is not None:
                        target += (
                            "["
                            + indexed_assignment.group("index")
                            + "]"
                        )
                    existing_type = _known_array_type_marker(
                        aliases.get(name, "")
                    )
                    existing_readonly = (
                        _READONLY_ALIAS_MARKER
                        in aliases.get(name, "")
                    )
                    local_shadow = (
                        preexisting_local_names is not None
                        and name not in preexisting_local_names
                        and not global_declaration
                        and executable in {"declare", "local", "typeset"}
                    )
                    requested_type = (
                        _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                        if associative
                        else _INDEXED_ARRAY_ALIAS_MARKER
                    )
                    if (
                        existing_readonly
                        or remove_readonly
                        or (
                        array
                        and existing_type is not None
                        and existing_type != requested_type
                        and not local_shadow
                        )
                    ):
                        continue
                    effective_type = (
                        requested_type
                        if array
                        else (
                            existing_type
                            if existing_type is not None
                            and not local_shadow
                            else None
                        )
                    )
                    _set_written_shell_alias(
                        aliases,
                        target,
                        value,
                        array=effective_type is not None,
                        associative=(
                            effective_type
                            == _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                        ),
                    )
                    if make_readonly:
                        aliases[name] = _readonly_alias(aliases[name])
                    continue
                name = argument.split("[", 1)[0]
                existing_type = _known_array_type_marker(
                    aliases.get(name, "")
                )
                existing_value = aliases.get(name)
                if (
                    existing_value is not None
                    and _READONLY_ALIAS_MARKER in existing_value
                ) or remove_readonly:
                    continue
                local_shadow = (
                    preexisting_local_names is not None
                    and name not in preexisting_local_names
                    and not global_declaration
                    and executable in {"declare", "local", "typeset"}
                )
                requested_type = (
                    _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                    if associative
                    else _INDEXED_ARRAY_ALIAS_MARKER
                )
                if (
                    array
                    and existing_type is not None
                    and existing_type != requested_type
                    and not local_shadow
                ):
                    continue
                effective_type = (
                    requested_type
                    if array
                    else (
                        existing_type
                        if existing_type is not None and not local_shadow
                        else None
                    )
                )
                if make_readonly and existing_value is not None:
                    aliases[name] = _readonly_alias(existing_value)
                    continue
                _set_written_shell_alias(
                    aliases,
                    argument,
                    (
                        _NAMEREF_ALIAS_MARKER
                        if nameref
                        else ""
                    )
                    + _AMBIGUOUS_DYNAMIC_ALIAS_MARKER,
                    array=effective_type is not None,
                    associative=(
                        effective_type
                        == _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                    ),
                )
                if make_readonly:
                    aliases[name] = _readonly_alias(aliases[name])
            return False
    except ValueError:
        return True
    return False


def has_forbidden_raw_builder_cgroup_membership_read(
    script: str,
    *,
    label: str,
    require_production_helpers: bool = False,
) -> bool:
    alias_scopes = [
        {
            "1": _RAW_CGROUP_ROOT_MARKER,
            "cgroup_path": _RAW_CGROUP_ROOT_MARKER,
        }
    ]
    execution_alias_scopes: list[dict[str, str]] = []
    active_execution_scopes: tuple[str, ...] = ()
    function_depth = 0
    function_stack: list[str] = []
    user_functions: set[str] = set()
    encountered_functions: set[str] = set()
    supervisor_assignments = 0
    saw_supervisor_bind = False
    saw_supervisor_readonly_remount = False
    saw_supervisor_inode_verification = False
    reviewed_trap_count = 0
    cgroup_path_initializations = 0
    cgroup_path_initialized = False
    membership_checker_count = 0
    raw_join_count = 0
    supervisor_bind_count = 0
    supervisor_remount_count = 0
    supervisor_inode_check_count = 0
    transport_producer_calls = {
        "read_checked_runtime_transport_file": 0,
        "read_checked_supervisor_transport_file": 0,
    }
    transport_phase_events: list[tuple[str, ...]] = []
    control_stack: list[str] = []
    try:
        semantic_script = _strip_patch_release_parser_heredoc_bodies(
            script
        )
        command_records = _annotate_command_control_scopes(
            _normalize_split_function_command_records(
                split_bash_command_records(
                    semantic_script,
                    label=label,
                )
            )
        )
        commands = tuple(record.text for record in command_records)
        if re.search(r"\bcgroup_members\b", semantic_script):
            return True
        function_bodies, function_inventory = (
            _shell_function_definitions(command_records)
        )
        user_functions = set(function_bodies)
        helper_inventory_is_reviewed = function_inventory == Counter(
            REVIEWED_BUILDER_HELPER_INVENTORY
        )
        if require_production_helpers and not helper_inventory_is_reviewed:
            return True
        for command_record in command_records:
            command_text = command_record.text
            is_function, function_name, has_inline_body = (
                _shell_function_declaration(command_text)
            )
            if is_function:
                if (
                    function_name is None
                    or has_inline_body
                    or command_record.control_scopes
                    or command_record.execution_scopes
                    or command_record.preceding_operator
                    in {"&&", "||", "|", "|&", "&"}
                    or command_record.following_operator
                    in {"&&", "||", "|", "|&", "&"}
                    or function_name in _SECURITY_SENSITIVE_FUNCTION_NAMES
                    or function_name in encountered_functions
                ):
                    return True
                encountered_functions.add(function_name)
                nested_aliases = dict(alias_scopes[-1])
                for name in tuple(nested_aliases):
                    if name.isdigit():
                        nested_aliases.pop(name)
                alias_scopes.append(nested_aliases)
                function_depth += 1
                function_stack.append(function_name)
                continue
            if (
                command_text == "}"
                and control_stack
                and control_stack[-1] == "{"
            ):
                control_stack.pop()
                continue
            if command_text == "}":
                if (
                    function_stack
                    and function_stack[-1] == "builder_main"
                    and control_stack
                ):
                    return True
                if function_depth > 0:
                    alias_scopes.pop()
                function_depth = max(0, function_depth - 1)
                if function_stack:
                    function_stack.pop()
                continue
            if function_stack and function_stack[-1] != "builder_main":
                continue
            first_word = command_text.split(maxsplit=1)[0]
            if first_word in {
                "case",
                "for",
                "if",
                "select",
                "until",
                "while",
                "{",
            }:
                control_stack.append(first_word)
            elif first_word in {"done", "esac", "fi"}:
                if not control_stack:
                    return True
                expected = {
                    "done": {"for", "select", "until", "while"},
                    "esac": {"case"},
                    "fi": {"if"},
                }[first_word]
                if control_stack[-1] not in expected:
                    return True
                control_stack.pop()
            mandatory_context_is_unconditional = (
                _mandatory_action_context_is_unconditional(
                    command_record,
                    control_stack=control_stack,
                    function_stack=function_stack,
                    require_production_helpers=require_production_helpers,
                )
            )
            common_execution_depth = 0
            for previous, current in zip(
                active_execution_scopes,
                command_record.execution_scopes,
            ):
                if previous != current:
                    break
                common_execution_depth += 1
            del execution_alias_scopes[common_execution_depth:]
            for _scope in command_record.execution_scopes[
                common_execution_depth:
            ]:
                execution_alias_scopes.append(
                    dict(
                        execution_alias_scopes[-1]
                        if execution_alias_scopes
                        else alias_scopes[-1]
                    )
                )
            active_execution_scopes = command_record.execution_scopes
            aliases = (
                execution_alias_scopes[-1]
                if execution_alias_scopes
                else alias_scopes[-1]
            )
            tokens = _split_attached_redirections(
                _parse_shell_tokens(command_text, label=label)
            )
            if any(
                _token_has_nested_braced_parameter(token)
                for token in tokens
            ):
                return True
            if _loop_iteration_target_is_forbidden(tokens, aliases):
                return True
            token_texts = _token_texts(tokens)
            if command_text == 'cgroup_path="$1"':
                if (
                    cgroup_path_initializations != 0
                    or control_stack
                    or not mandatory_context_is_unconditional
                    or (
                        require_production_helpers
                        and function_stack != ["builder_main"]
                    )
                ):
                    return True
                cgroup_path_initializations = 1
                cgroup_path_initialized = True
                aliases["cgroup_path"] = _RAW_CGROUP_ROOT_MARKER
                continue
            if token_texts == (
                "printf",
                "%s\\n",
                "$$",
                ">",
                "$cgroup_path/cgroup.procs",
            ):
                if (
                    not mandatory_context_is_unconditional
                    or raw_join_count != 0
                    or (
                    require_production_helpers
                    and not cgroup_path_initialized
                    )
                ):
                    return True
                raw_join_count = 1
                continue
            if token_texts == (
                "/usr/bin/mount",
                "--bind",
                "$cgroup_path",
                "/mnt/supervisor/cgroup",
            ):
                if (
                    not mandatory_context_is_unconditional
                    or supervisor_bind_count != 0
                    or (
                    require_production_helpers
                    and not cgroup_path_initialized
                    )
                ):
                    return True
                supervisor_bind_count = 1
                saw_supervisor_bind = True
            elif token_texts == (
                "/usr/bin/mount",
                "-o",
                "remount,bind,ro,nosuid,nodev,noexec",
                "/mnt/supervisor/cgroup",
            ):
                if (
                    not mandatory_context_is_unconditional
                    or supervisor_remount_count != 0
                ):
                    return True
                supervisor_remount_count = 1
                saw_supervisor_readonly_remount = (
                    saw_supervisor_bind
                )
            elif token_texts == (
                "test",
                "$(/usr/bin/stat -Lc %d:%i $cgroup_path)",
                "=",
                "$(/usr/bin/stat -Lc %d:%i $supervisor_cgroup)",
            ):
                if (
                    not mandatory_context_is_unconditional
                    or supervisor_inode_check_count != 0
                ):
                    return True
                supervisor_inode_check_count = 1
                saw_supervisor_inode_verification = (
                    saw_supervisor_bind
                    and saw_supervisor_readonly_remount
                    and supervisor_assignments == 1
                    and aliases.get("supervisor_cgroup")
                    == "/mnt/supervisor/cgroup"
                )
                if saw_supervisor_inode_verification:
                    continue
                return True
            if token_texts == (
                "/usr/bin/python3",
                "-I",
                "-S",
                "-",
                "$$",
                "<<",
                "PY",
            ):
                membership_checker_count += 1
                if (
                    membership_checker_count != 1
                    or control_stack
                    or not mandatory_context_is_unconditional
                    or function_stack != ["builder_main"]
                ):
                    return True
                if (
                    supervisor_assignments == 1
                    and aliases.get("supervisor_cgroup")
                    == "/mnt/supervisor/cgroup"
                    and saw_supervisor_inode_verification
                ):
                    continue
                return True
            if (
                token_texts
                and posixpath.basename(token_texts[0]) == "unset"
                and "supervisor_cgroup" in token_texts[1:]
            ):
                return True
            resolved_token_texts = _resolve_tokens_for_alias_state(
                tokens,
                aliases,
            )
            if _arithmetic_writes_protected_state(
                tokens,
                resolved_token_texts,
                aliases,
            ):
                return True
            if _command_mutates_unreviewed_shell_state(
                resolved_token_texts,
                original_tokens=token_texts,
            ):
                return True
            executable_tokens = _strip_shell_command_prefixes(
                resolved_token_texts
            )
            transport_event: tuple[str, ...] | None = None
            if executable_tokens:
                executable_name = posixpath.basename(executable_tokens[0])
                if executable_name in transport_producer_calls:
                    if not mandatory_context_is_unconditional:
                        return True
                    transport_producer_calls[executable_name] += 1
                    transport_event = ("producer",) + token_texts
            if (
                transport_event is None
                and _active_reserved_transport_references(
                    tokens,
                    resolved_token_texts,
                    aliases,
                )
            ):
                transport_event = ("consumer",) + token_texts
            if transport_event is not None:
                event_index = len(transport_phase_events)
                if (
                    event_index >= len(_REVIEWED_TRANSPORT_PHASE_EVENTS)
                    or transport_event
                    != _REVIEWED_TRANSPORT_PHASE_EVENTS[event_index]
                ):
                    return True
                transport_phase_events.append(transport_event)
            if (
                require_production_helpers
                and not cgroup_path_initialized
                and any(
                    _RAW_CGROUP_ROOT_MARKER in token
                    for token in resolved_token_texts
                )
            ):
                return True
            if not mandatory_context_is_unconditional and any(
                _RAW_CGROUP_ROOT_MARKER in token
                for token in resolved_token_texts
            ):
                return True
            normalized_command = _normalize_shell_builtin_wrappers(
                resolved_token_texts
            )
            if (
                normalized_command
                and posixpath.basename(normalized_command[0]) == "trap"
            ):
                if token_texts != (
                    "trap",
                    "isolated_stage_failure",
                    "ERR",
                ):
                    return True
                if "isolated_stage_failure" not in encountered_functions:
                    return True
                if not mandatory_context_is_unconditional:
                    return True
                reviewed_trap_count += 1
                if reviewed_trap_count != 1:
                    return True
            if _helper_call_has_sensitive_arguments(
                resolved_token_texts,
                user_functions=encountered_functions,
                function_bodies=function_bodies,
                aliases=aliases,
                trusted_helper_inventory=helper_inventory_is_reviewed,
            ):
                return True
            assignment_only = bool(tokens) and all(
                _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(token.text)
                or _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(token.text)
                for token in tokens
            )
            if _ambiguous_array_command_is_forbidden(
                resolved_token_texts
            ):
                return True
            if _normalized_command_changes_dispatch(
                resolved_token_texts
            ):
                return True
            if _normalized_command_mutates_dispatch_state(
                resolved_token_texts
            ):
                return True
            if _normalized_command_creates_nameref(
                resolved_token_texts
            ):
                return True
            if not assignment_only and _ambiguous_dynamic_command_is_forbidden(
                resolved_token_texts
            ):
                return True
            if _normalized_command_mutates_supervisor(
                resolved_token_texts
            ):
                return True
            if _normalized_command_mutates_cgroup_path(
                resolved_token_texts
            ):
                return True
            if _normalized_command_mutates_reserved_transport(
                resolved_token_texts
            ):
                return True
            array_declaration = (
                bool(token_texts)
                and posixpath.basename(token_texts[0])
                in {"declare", "local", "readonly", "typeset"}
                and any(
                    token.startswith("-")
                    and ("a" in token[1:] or "A" in token[1:])
                    for token in token_texts[1:]
                )
            )
            associative_declaration = (
                array_declaration
                and any(
                    token.startswith("-") and "A" in token[1:]
                    for token in token_texts[1:]
                )
            )
            leading_assignments = _leading_assignment_indices(tokens)
            for token_index, token in enumerate(tokens):
                resolved = _resolve_shell_token(token, aliases)
                if _AMBIGUOUS_TRACKED_PARAMETER_MARKER in resolved:
                    return True
                if (
                    _RAW_CGROUP_ROOT_MARKER in resolved
                    and _raw_cgroup_suffix_has_dynamic_filename(resolved)
                ):
                    return True
                if _ambiguous_alias_suffix_has_dynamic_filename(
                    resolved
                ):
                    return True
                if (
                    _AMBIGUOUS_ARRAY_ALIAS_MARKER in resolved
                    and "cgroup.procs" in resolved
                ):
                    return True
                if (
                    _SHELL_ARRAY_EXPANSION_RE.search(token.text)
                    and "cgroup.procs" in resolved
                ):
                    return True
                if "cgroup.procs" in resolved:
                    return True
                indexed_assignment = (
                    _SHELL_INDEXED_ASSIGNMENT_RE.fullmatch(token.text)
                )
                if (
                    indexed_assignment is not None
                    and token_index not in leading_assignments
                ):
                    indexed_assignment = None
                if indexed_assignment is not None:
                    name = indexed_assignment.group("name")
                    if (
                        name in _DISPATCH_STATE_VARIABLES
                        or name in _RESERVED_TRANSPORT_OUTPUTS
                    ):
                        return True
                    if name in {"cgroup_path", "supervisor_cgroup"}:
                        return True
                    if _READONLY_ALIAS_MARKER in aliases.get(name, ""):
                        continue
                    value_token = _slice_shell_token(
                        token,
                        indexed_assignment.start("value"),
                    )
                    index = _resolve_shell_aliases(
                        indexed_assignment.group("index"),
                        aliases,
                    )
                    value = _resolve_shell_token(value_token, aliases)
                    if _assignment_value_has_dynamic_shell_syntax(
                        _active_shell_token_text(value_token),
                        token_has_shell_syntax=value_token.has_shell_syntax,
                    ):
                        value = _AMBIGUOUS_DYNAMIC_ALIAS_MARKER + value
                    if (
                        _RAW_CGROUP_ROOT_MARKER in index
                        or _RAW_CGROUP_ROOT_MARKER in value
                        or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in index
                        or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in value
                    ):
                        return True
                    previous = aliases.get(
                        name,
                        _AMBIGUOUS_ARRAY_ALIAS_MARKER,
                    )
                    array_marker = next(
                        (
                            marker
                            for marker in (
                                _ASSOCIATIVE_ARRAY_ALIAS_MARKER,
                                _INDEXED_ARRAY_ALIAS_MARKER,
                            )
                            if marker in previous
                        ),
                        _AMBIGUOUS_ARRAY_ALIAS_MARKER,
                    )
                    if indexed_assignment.group("append"):
                        aliases[name] = (
                            _AMBIGUOUS_ARRAY_ALIAS_MARKER
                            + array_marker
                            + previous
                            + value
                        )
                    else:
                        aliases[name] = (
                            _AMBIGUOUS_ARRAY_ALIAS_MARKER
                            + array_marker
                            + value
                        )
                    continue
                assignment = _SHELL_ALIAS_ASSIGNMENT_RE.fullmatch(
                    token.text
                )
                if (
                    assignment is None
                    or token_index not in leading_assignments
                ):
                    continue
                name = assignment.group("name")
                if (
                    name in _DISPATCH_STATE_VARIABLES
                    or name in _RESERVED_TRANSPORT_OUTPUTS
                ):
                    return True
                if name == "cgroup_path":
                    return True
                if _READONLY_ALIAS_MARKER in aliases.get(name, ""):
                    continue
                value_token = _slice_shell_token(
                    token,
                    assignment.start("value"),
                )
                value = _resolve_shell_token(value_token, aliases)
                if _assignment_value_has_dynamic_shell_syntax(
                    _active_shell_token_text(value_token),
                    token_has_shell_syntax=value_token.has_shell_syntax,
                ):
                    value = _AMBIGUOUS_DYNAMIC_ALIAS_MARKER + value
                is_array_literal = assignment.group("value").startswith(
                    "("
                )
                if name == "supervisor_cgroup":
                    if not mandatory_context_is_unconditional:
                        return True
                    supervisor_assignments += 1
                    if (
                        supervisor_assignments != 1
                        or assignment.group("append")
                        or array_declaration
                        or is_array_literal
                        or value != "/mnt/supervisor/cgroup"
                        or not saw_supervisor_bind
                        or not saw_supervisor_readonly_remount
                    ):
                        return True
                    aliases[name] = value
                    continue
                if (
                    (array_declaration or is_array_literal)
                    and (
                        _RAW_CGROUP_ROOT_MARKER in value
                        or _AMBIGUOUS_TRACKED_PARAMETER_MARKER in value
                    )
                ):
                    return True
                existing_type = _known_array_type_marker(
                    aliases.get(name, "")
                )
                if assignment.group("append"):
                    aliases[name] = aliases.get(name, "") + value
                elif array_declaration:
                    requested_type = (
                        _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                        if associative_declaration
                        else _INDEXED_ARRAY_ALIAS_MARKER
                    )
                    if (
                        existing_type is None
                        or existing_type == requested_type
                    ):
                        aliases[name] = _typed_array_alias(
                            requested_type,
                            value,
                        )
                elif is_array_literal:
                    aliases[name] = _typed_array_alias(
                        existing_type or _INDEXED_ARRAY_ALIAS_MARKER,
                        value,
                    )
                elif existing_type is not None:
                    aliases[name] = _typed_array_alias(
                        existing_type,
                        value,
                    )
                else:
                    aliases[name] = value
            if array_declaration:
                for token in tokens[1:]:
                    if re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*",
                        token.text,
                    ):
                        if token.text == "supervisor_cgroup":
                            return True
                        aliases.setdefault(
                            token.text,
                            (
                                _AMBIGUOUS_ARRAY_ALIAS_MARKER
                                + (
                                    _ASSOCIATIVE_ARRAY_ALIAS_MARKER
                                    if associative_declaration
                                    else _INDEXED_ARRAY_ALIAS_MARKER
                                )
                            ),
                        )
            if _apply_shell_builtin_alias_writes(
                resolved_token_texts,
                aliases,
                original_shell_tokens=tokens,
            ):
                return True
        if require_production_helpers:
            if reviewed_trap_count != 1:
                return True
            if (
                cgroup_path_initializations != 1
                or aliases.get("cgroup_path")
                != _RAW_CGROUP_ROOT_MARKER
            ):
                return True
            if membership_checker_count != 1 or control_stack:
                return True
            if (
                raw_join_count != 1
                or supervisor_bind_count != 1
                or supervisor_remount_count != 1
                or supervisor_inode_check_count != 1
            ):
                return True
            if transport_producer_calls != {
                "read_checked_runtime_transport_file": 1,
                "read_checked_supervisor_transport_file": 2,
            }:
                return True
            if tuple(transport_phase_events) != _REVIEWED_TRANSPORT_PHASE_EVENTS:
                return True
        return False
    except ValueError:
        return True
