"""Closed contract for the trusted patch-release builder-isolation shell."""

from __future__ import annotations

import ast
import hashlib
import posixpath
import re
import shlex
from typing import Iterable


REVIEWED_BUILDER_ISOLATION_SHA256 = (
    "fe0cd00a4a122cfe3481713292989a69e7c5182bb8807539ba61ca4a1eaabee4"
)
APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS = {
    ("/usr/bin/mount", "-o", "remount,ro,nosuid,nodev,noexec", "$hidden"),
}
APPROVED_NONLITERAL_MOUNT_COMMANDS = APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS | {
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
_SIMPLE_COMMAND_PREFIXES = frozenset({"if", "then", "do", "elif", "while", "until", "!"})
_CONTROL_OPERATORS = frozenset({";", "&&", "||", "|", "&"})
_DISALLOWED_MOUNT_WRAPPERS = frozenset({"env", "command", "eval"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


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
    while index < len(line):
        character = line[index]
        if state == "normal":
            if character == "'":
                state = "single"
            elif character == '"':
                state = "double"
            elif character == "\\":
                if index == len(line) - 1:
                    return state, True
                index += 2
                continue
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


def _canonical_literal_path(token: str) -> str | None:
    if (
        not token
        or not token.startswith("/")
        or any(marker in token for marker in ("$", "`", "$(", "${", "<(", ">("))
    ):
        return None
    return posixpath.normpath(token)


def _token_has_shell_syntax(token: str) -> bool:
    return any(marker in token for marker in ("$", "`", "$(", "${", "<(", ">("))


def _command_references_supervisor(tokens: Iterable[str], command_text: str) -> bool:
    if "/mnt/supervisor" in command_text:
        return True
    for token in tokens:
        path = _canonical_literal_path(token)
        if path == "/mnt/supervisor" or (
            path is not None and path.startswith("/mnt/supervisor/")
        ):
            return True
        if token.startswith("path=$(/usr/bin/mktemp /mnt/supervisor/"):
            return True
    return False


def _is_reviewed_supervisor_command(tokens: tuple[str, ...]) -> bool:
    return tokens in APPROVED_SUPERVISOR_COMMAND_TOKENS or (
        len(tokens) == 1
        and tokens[0].startswith("path=$(/usr/bin/mktemp /mnt/supervisor/")
    )


def _strip_command_prefixes(command: tuple[str, ...]) -> tuple[str, ...]:
    tokens = list(command)
    while tokens and tokens[0] in _SIMPLE_COMMAND_PREFIXES:
        tokens.pop(0)
    while tokens and _ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    return tuple(tokens)


def _mount_command_targets_supervisor_parent(
    command: tuple[str, ...],
    *,
    allow_reviewed_nonliteral_hidden: bool,
) -> bool:
    tokens = _strip_command_prefixes(command)
    if not tokens:
        return False

    executable = tokens[0]
    executable_literal = executable == "/usr/bin/mount"
    executable_nonliteral = not executable_literal
    read_only_like = False
    remount_like = False
    options_nonliteral = False
    positionals: list[str] = []
    unknown_flag = False
    index = 1

    while index < len(tokens):
        token = tokens[index]
        if token in {"-r", "--read-only"}:
            read_only_like = True
            index += 1
            continue
        if token in {"-o", "--options"}:
            if index + 1 >= len(tokens):
                return True
            option_text = tokens[index + 1]
            if _token_has_shell_syntax(option_text):
                options_nonliteral = True
            for option in option_text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
            index += 2
            continue
        if token.startswith("--options="):
            option_text = token.split("=", 1)[1]
            if _token_has_shell_syntax(option_text):
                options_nonliteral = True
            for option in option_text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
            index += 1
            continue
        if token.startswith("-o") and token != "-o":
            option_text = token[2:]
            if _token_has_shell_syntax(option_text):
                options_nonliteral = True
            for option in option_text.split(","):
                normalized = option.strip().replace("\\", "")
                if normalized.startswith("remount"):
                    remount_like = True
                if normalized == "ro":
                    read_only_like = True
            index += 1
            continue
        if token in {
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
        if token in {"-t", "--types"}:
            if index + 1 >= len(tokens):
                return True
            index += 2
            continue
        if token == "--":
            positionals.extend(tokens[index + 1 :])
            break
        if token.startswith("-"):
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
        token in {
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
        or token.startswith("--options=")
        or (token.startswith("-o") and token != "-o")
        for token in tokens[1:]
    )
    looks_like_mount_surface = (
        executable_literal
        or executable in _DISALLOWED_MOUNT_WRAPPERS
        or any(token == "/usr/bin/mount" for token in tokens)
        or (executable_nonliteral and has_mount_flag)
    )
    if (
        looks_like_mount_surface
        and mount_surface_uses_nonliteral
        and tokens not in APPROVED_SUPERVISOR_COMMAND_TOKENS
        and tokens not in APPROVED_NONLITERAL_MOUNT_COMMANDS
    ):
        return True

    if not (remount_like and read_only_like):
        return False

    if tokens in APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS:
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
    allow_reviewed_nonliteral_hidden = (
        reviewed_builder_isolation_sha256(script) == REVIEWED_BUILDER_ISOLATION_SHA256
    )
    for command_text in split_bash_simple_command_strings(script, label=label):
        command_tokens = tuple(shlex.split(command_text))
        if _mount_command_targets_supervisor_parent(
            command_tokens,
            allow_reviewed_nonliteral_hidden=allow_reviewed_nonliteral_hidden,
        ):
            return True
        if _command_references_supervisor(command_tokens, command_text) and not _is_reviewed_supervisor_command(
            command_tokens
        ):
            return True
    return False
