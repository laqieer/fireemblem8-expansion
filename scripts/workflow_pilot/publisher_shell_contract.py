"""Closed contract for the trusted patch-release builder-isolation shell."""

from __future__ import annotations

import ast
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
_SIMPLE_COMMAND_PREFIXES = frozenset({"if", "then", "do", "elif", "while", "until", "!"})
_CONTROL_OPERATORS = frozenset({";", "&&", "||", "|", "&"})
_DISALLOWED_MOUNT_WRAPPERS = frozenset({"env", "command", "eval"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_BUSYBOX_BASENAME = "busybox"
_ENV_BASENAME = "env"
_SHELL_INTERPRETER_BASENAMES = frozenset({"bash", "sh", "dash"})
_ENV_ZERO_ARG_OPTIONS = frozenset({"-i", "--ignore-environment"})
_ENV_OPTIONS_WITH_ARGS = frozenset({"-C", "--chdir", "-u", "--unset"})
_LITERAL_RUN_HEADER_RE = re.compile(
    r"^(?P<indent> {6})run:[ \t]*(?P<style>\|[1-9+-]*)(?:[ \t]*(?:#.*)?)?(?:\r?\n|\Z)$"
)


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


def _is_shell_interpreter_token(token: str) -> bool:
    return (
        not _token_has_shell_syntax(token)
        and posixpath.basename(token) in _SHELL_INTERPRETER_BASENAMES
    )


def _literal_token_basename(token: str) -> str | None:
    if _token_has_shell_syntax(token) or _ASSIGNMENT_RE.fullmatch(token):
        return None
    return posixpath.basename(posixpath.normpath(token))


def _is_env_executable_token(token: str) -> bool:
    return _literal_token_basename(token) == _ENV_BASENAME


def _is_busybox_executable_token(token: str) -> bool:
    return _literal_token_basename(token) == _BUSYBOX_BASENAME


def _is_shell_interpreter_reference_token(token: str) -> bool:
    if _ASSIGNMENT_RE.fullmatch(token):
        return False
    if _token_has_shell_syntax(token):
        return True
    normalized = token.strip("\"'`()")
    return posixpath.basename(normalized) in _SHELL_INTERPRETER_BASENAMES


def _strip_command_prefixes(command: tuple[str, ...]) -> tuple[str, ...]:
    tokens = list(command)
    while tokens and tokens[0] in _SIMPLE_COMMAND_PREFIXES:
        tokens.pop(0)
    while tokens and _ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    return tuple(tokens)


def _reject_env_split_string_option(token: str) -> bool:
    return (
        token == "-S"
        or token.startswith("-S")
        or token == "--split-string"
        or token.startswith("--split-string=")
    )


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


def _env_wrapper_followed_by_ambiguous_surface(
    tokens: tuple[str, ...],
    *,
    start_index: int,
) -> bool:
    index = start_index
    while index < len(tokens):
        current = tokens[index]
        if current == "--":
            return False
        if _ASSIGNMENT_RE.fullmatch(current):
            index += 1
            continue
        if current in _ENV_ZERO_ARG_OPTIONS:
            index += 1
            continue
        if _reject_env_split_string_option(current):
            return True
        if current in _ENV_OPTIONS_WITH_ARGS:
            if index + 1 >= len(tokens):
                return True
            index += 2
            continue
        if current.startswith("--chdir=") or current.startswith("--unset="):
            index += 1
            continue
        if current.startswith("--"):
            return True
        ambiguous, consumes_arg = _parse_env_short_option_token(
            current,
            has_next_token=index + 1 < len(tokens),
        )
        if ambiguous:
            return True
        if consumes_arg:
            index += 2
            continue
        if current.startswith("-"):
            return True
        return False
    return False


def _command_has_ambiguous_env_shell_surface(tokens: tuple[str, ...]) -> bool:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_env_executable_token(token):
            if _env_wrapper_followed_by_ambiguous_surface(
                tokens,
                start_index=index + 1,
            ):
                return True
        elif _is_busybox_executable_token(token):
            if index + 1 >= len(tokens):
                index += 1
                continue
            applet = tokens[index + 1]
            if _token_has_shell_syntax(applet):
                if index + 2 < len(tokens) and _reject_env_split_string_option(
                    tokens[index + 2]
                ):
                    return True
                index += 1
                continue
            if _is_env_executable_token(applet) and _env_wrapper_followed_by_ambiguous_surface(
                tokens,
                start_index=index + 2,
            ):
                return True
        elif _token_has_shell_syntax(token) and index + 1 < len(tokens):
            following = tokens[index + 1]
            if _reject_env_split_string_option(following):
                return True
        index += 1
    return False


def _shell_c_invocation_is_forbidden(command: tuple[str, ...]) -> bool:
    tokens = _strip_command_prefixes(command)
    if not tokens:
        return False

    if _command_has_ambiguous_env_shell_surface(tokens):
        return True

    for index, token in enumerate(tokens):
        if token == "-c":
            payload_index = index + 1
        elif token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
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
    if looks_like_mount_surface and mount_surface_uses_nonliteral:
        if tokens in APPROVED_SUPERVISOR_COMMAND_TOKENS:
            pass
        elif tokens in APPROVED_NONLITERAL_MOUNT_COMMANDS:
            pass
        elif (
            tokens in APPROVED_NONLITERAL_READONLY_MOUNT_COMMANDS
            and allow_reviewed_nonliteral_hidden
        ):
            pass
        else:
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
    allowed_hidden_indices = _authorized_hidden_readonly_mount_indices(
        script,
        label=label,
    )
    for command_index, command_text in enumerate(
        split_bash_simple_command_strings(script, label=label)
    ):
        command_tokens = tuple(shlex.split(command_text))
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
