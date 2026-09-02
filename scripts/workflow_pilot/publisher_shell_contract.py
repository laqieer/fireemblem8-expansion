"""Closed contract for the trusted patch-release builder-isolation shell."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import posixpath
import re
import shlex
from typing import Iterable


REVIEWED_PATCH_RELEASE_RUN_SHA256 = (
    "896a703f3173c77758d1b8a6c18190fe89997c749264a81a468fa6839b9bfc6a"
)
REVIEWED_BUILDER_ISOLATION_SHA256 = (
    "6088db198a46f7617eef83daf2366c33055c597df1740ccb87de803ede0034ad"
)
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
_SIMPLE_COMMAND_PREFIXES = frozenset(
    {"if", "then", "do", "elif", "while", "until", "!", "else", "{"}
)
_CONTROL_OPERATORS = frozenset({";", "&&", "||", "|", "&"})
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
_WRAPPER_BASENAMES = frozenset({"command", "nice", "sudo", "timeout"})


@dataclass(frozen=True)
class _ShellToken:
    text: str
    has_shell_syntax: bool


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


def _bash_line_state(line: str, state: str) -> tuple[str, bool]:
    index = 0
    word_start = state == "normal"
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character in " \t":
                word_start = True
            elif character == "#" and word_start:
                break
            elif character == "'":
                state = "single"
                word_start = False
            elif character == '"':
                state = "double"
                word_start = False
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                index += 2
                word_start = False
                continue
            elif character in "&|;":
                if (
                    character in "&|"
                    and index + 1 < len(line)
                    and line[index + 1] == character
                ):
                    index += 1
                word_start = True
            else:
                word_start = False
        elif state == "single":
            if character == "'":
                state = "normal"
        else:
            if character == '"':
                state = "normal"
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                if line[index + 1] in '$`"\\':
                    index += 2
                    continue
        index += 1
    return state, False


def bash_logical_lines(script: str, *, label: str) -> tuple[str, ...]:
    state = "normal"
    logical_lines: list[str] = []
    current = ""
    for line in script.splitlines():
        current += line
        state, continued = _bash_line_state(line, state)
        if continued:
            current = current[:-1]
            continue
        if state != "normal":
            current += "\n"
            continue
        logical_lines.append(current)
        current = ""
    if current:
        raise ValueError(f"{label} has unterminated quoting or continuation")
    return tuple(logical_lines)


def split_bash_simple_command_strings(script: str, *, label: str) -> tuple[str, ...]:
    commands: list[str] = []
    for logical in bash_logical_lines(script, label=label):
        current: list[str] = []
        quote: str | None = None
        word_start = True
        index = 0
        while index < len(logical):
            character = logical[index]
            if quote is None:
                if character in " \t":
                    current.append(character)
                    word_start = True
                elif character == "#" and word_start:
                    break
                elif character == "'":
                    current.append(character)
                    quote = "'"
                    word_start = False
                elif character == '"':
                    current.append(character)
                    quote = '"'
                    word_start = False
                elif character in "&|;":
                    operator = character
                    if (
                        character in "&|"
                        and index + 1 < len(logical)
                        and logical[index + 1] == character
                    ):
                        operator += character
                        index += 1
                    if operator in _CONTROL_OPERATORS:
                        command = "".join(current).strip()
                        if command:
                            commands.append(command)
                        current = []
                        word_start = True
                    else:
                        current.append(operator)
                        word_start = False
                else:
                    current.append(character)
                    word_start = False
            elif quote == "'":
                current.append(character)
                if character == "'":
                    quote = None
            else:
                current.append(character)
                if character == "\\" and index + 1 < len(logical):
                    current.append(logical[index + 1])
                    index += 1
                elif character == '"':
                    quote = None
            index += 1
        command = "".join(current).strip()
        if command:
            commands.append(command)
    return tuple(commands)


def raw_patch_release_parser_sources(script: str) -> tuple[tuple[str, str], ...]:
    matches = list(_PATCH_RELEASE_PARSER_HEREDOC_RE.finditer(script))
    if script.count(PATCH_RELEASE_PARSER_HEREDOC_INTRODUCER) != len(
        PATCH_RELEASE_PARSER_HEREDOC_NAMES
    ):
        raise ValueError("patch-release parser heredoc count differs")
    if len(matches) != len(PATCH_RELEASE_PARSER_HEREDOC_NAMES):
        raise ValueError("patch-release parser heredoc structure differs")
    names = tuple(match.group("name") for match in matches)
    if names != PATCH_RELEASE_PARSER_HEREDOC_NAMES:
        raise ValueError("patch-release parser function association differs")
    return tuple((match.group("name"), match.group("body") + "\n") for match in matches)


def validate_patch_release_parser_heredocs(script: str, *, label: str) -> None:
    for _name, body in raw_patch_release_parser_sources(script):
        try:
            ast.parse(body)
        except SyntaxError as error:
            raise ValueError(
                f"{label} patch-release parser Python body is invalid"
            ) from error


def _strip_patch_release_parser_heredoc_bodies(script: str) -> str:
    return _PATCH_RELEASE_PARSER_HEREDOC_RE.sub(
        lambda match: (
            f"{match.group('name')}() {{\n"
            f"{PATCH_RELEASE_PARSER_HEREDOC_INTRODUCER}\n"
            "PY\n"
            "}\n"
        ),
        script,
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


def _parse_shell_tokens(command_text: str, *, label: str) -> tuple[_ShellToken, ...]:
    syntax_flags = _token_shell_syntax_flags(command_text, label=label)
    try:
        values = tuple(shlex.split(command_text))
    except ValueError as error:
        raise ValueError(f"{label} shell tokenization differs") from error
    if len(values) != len(syntax_flags):
        raise ValueError(f"{label} shell token boundaries differ")
    return tuple(
        _ShellToken(text=value, has_shell_syntax=has_shell_syntax)
        for value, has_shell_syntax in zip(values, syntax_flags)
    )


def _token_texts(tokens: Iterable[_ShellToken]) -> tuple[str, ...]:
    return tuple(token.text for token in tokens)


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
    if "/mnt/supervisor" in command_text:
        return True
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


def _is_case_pattern_token(token: _ShellToken) -> bool:
    if not token.text.endswith(")"):
        return False
    prefix = token.text[:-1]
    return (
        token.text == "*)"
        or prefix.startswith("/")
        or prefix.startswith("*")
        or "|" in prefix
        or " " in prefix
    )


def _semantic_surface_tokens(command: tuple[_ShellToken, ...]) -> tuple[_ShellToken, ...]:
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
        if tokens[0].text == ")" or tokens[0].text.endswith("))"):
            return ()
        if _is_case_pattern_token(tokens[0]):
            tokens = tokens[1:]
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
        if basename in _WRAPPER_BASENAMES:
            next_index = _next_wrapper_command_index(tokens, start_index=index)
            if next_index is None:
                return True
            index = next_index
            continue
        return False
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
        tokens = _semantic_surface_tokens(_parse_shell_tokens(command_text, label=label))
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
    executable_nonliteral = not executable_literal
    read_only_like = False
    remount_like = False
    options_nonliteral = False
    positionals: list[_ShellToken] = []
    unknown_flag = False
    index = 1

    while index < len(tokens):
        token = tokens[index]
        token_text = token.text
        if token_text in {"-r", "--read-only"}:
            read_only_like = True
            index += 1
            continue
        if token_text in {"-o", "--options"}:
            if index + 1 >= len(tokens):
                return True
            option_text = tokens[index + 1]
            if _token_has_shell_syntax(option_text):
                options_nonliteral = True
            for option in option_text.text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
            index += 2
            continue
        if token_text.startswith("--options="):
            option_text = token_text.split("=", 1)[1]
            if _token_has_shell_syntax(token):
                options_nonliteral = True
            for option in option_text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
            index += 1
            continue
        if token_text.startswith("-o") and token_text != "-o":
            option_text = token_text[2:]
            if _token_has_shell_syntax(token):
                options_nonliteral = True
            for option in option_text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
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
        if token_text.startswith("-"):
            unknown_flag = True
            index += 1
            continue
        positionals.append(token)
        index += 1

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
        or (token.text.startswith("-o") and token.text != "-o")
        for token in tokens[1:]
    )
    looks_like_mount_surface = (
        executable_literal
        or executable in _DISALLOWED_MOUNT_WRAPPERS
        or any(token.text == "/usr/bin/mount" for token in tokens)
        or (executable_nonliteral and has_mount_flag)
    )
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
        semantic_pairs = _shell_semantic_command_pairs(script, label=label)
        allowed_hidden_indices = _authorized_hidden_readonly_mount_indices(
            script,
            label=label,
        )
    except ValueError:
        return True
    for command_index, command_text, command_tokens in semantic_pairs:
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
