"""The original toolchain check's closed grammar and single-dispatch authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import weakref

if __package__:
    from .authority import ENVIRONMENT, encoded, relative_path
    from .budget import MakeProbeError
    from .producer_channel import ChannelError
    from .private_install import directory_identity, launch_scope
    from . import arm_headers
else:
    from authority import ENVIRONMENT, encoded, relative_path
    from budget import MakeProbeError
    from producer_channel import ChannelError
    from private_install import directory_identity, launch_scope
    import arm_headers


CONTRACT = "modern-toolchain-dry-run-recipe"
TARGET = "expansion-modern-toolchain-check"
STAGES = ("version", "target", "assembler", "syntax", "compile")
QUERIES = ("--version", "-dumpmachine", "-print-prog-name=as")
SYNTAX_INPUT = '#include "global.h"\n'
COMPILE_INPUT = "void modern_arm7tdmi_thumb_probe(void) {}\n"
INPUTS = ("", "", "", SYNTAX_INPUT, COMPILE_INPUT)
EXEC_PREFIX = "toolchain-exec:"
INPUT_PREFIX = "toolchain-stdin:"
LAUNCH_FIELDS = (
    "root", "mode", "argv", "environment", "code", "sources", "enumerations",
    "executables", "mounts", "dependency",
)
SYNTAX_FAILURE = (
    "error: modern GCC cannot parse include/global.h and its standard headers.\n"
    "Install newlib headers (Ubuntu: libnewlib-arm-none-eabi) or set MODERN_NEWLIB_INCLUDE.\n"
)
COMPILE_FAILURE = (
    "error: ARM7TDMI Thumb/interwork compile probe failed.\n"
    "Check the GCC/binutils pairing and MODERN_BINUTILS_DIR.\n"
)


def input_bytes(profile):
    if profile["stdin"] != INPUTS[profile["stage"]]:
        raise MakeProbeError("toolchain input differs from its exact selected original")
    return profile["stdin"].encode("utf-8")


@dataclass(frozen=True)
class Recipe:
    compiler: str
    binutils: tuple[str, ...]
    syntax: tuple[str, ...]
    compile: tuple[str, ...]
    config: str
    abi: str
    includes: tuple[str, ...]
    system: tuple[str, ...]

    def arguments(self, stage):
        return (*self.binutils, QUERIES[stage]) if stage < 3 else (
            self.syntax if stage == 3 else self.compile
        )


def options(arguments, *, syntax):
    suffix = ("-fsyntax-only", "-x", "c", "-") if syntax else (
        "-ffreestanding", "-fno-pic", "-fno-pie", "-c", "-x", "c", "-o", "/dev/null", "-",
    )
    if tuple(arguments[-len(suffix):]) != suffix:
        raise MakeProbeError("toolchain probe lost its original input/output/mode")
    flags, includes, system, binutils = set(), [], [], []
    words = iter(arguments[:-len(suffix)])
    for word in words:
        if word in {*arm_headers.ARCH_FLAGS, "-std=gnu11", "-fgnu89-inline", "-mabi=aapcs", "-mabi=apcs-gnu"}:
            if word in flags or syntax and word.startswith("-mabi=") or not syntax and word.startswith(("-std=", "-fgnu")):
                raise MakeProbeError("toolchain probe has a repeated or foreign language/ABI flag")
            flags.add(word)
            continue
        if word.startswith("-B"):
            if word not in {"-B/bin/", "-B/usr/bin/"} or binutils:
                raise MakeProbeError("toolchain probe escaped its exact binutils search")
            binutils.append(word)
            continue
        option = next((item for item in ("-isystem", "-iquote", "-I", "-D", "-U") if word.startswith(item)), None)
        if option is None or not syntax and option != "-isystem":
            raise MakeProbeError("unsupported toolchain probe flag")
        value = word[len(option):] if word != option else next(words, "")
        if not value or value.startswith(("-", "@")) or any(char in value for char in "\n\r\0"):
            raise MakeProbeError("invalid toolchain probe operand")
        if option == "-isystem":
            if value != arm_headers.NEWLIB or system:
                raise MakeProbeError("toolchain probe escaped its exact C SDK")
            system.append(value)
        elif option in {"-I", "-iquote"}:
            if value.startswith(("=", "$SYSROOT")):
                raise MakeProbeError("toolchain probe has a sysroot-special include")
            includes.append(value if value == "." else relative_path(value))
        else:
            name, separator, _ = value.partition("=")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or option == "-U" and separator:
                raise MakeProbeError("toolchain probe requires symbolic ordered macros")
    if not set(arm_headers.ARCH_FLAGS) <= flags or syntax and "-std=gnu11" not in flags:
        raise MakeProbeError("toolchain probe lost its original ARM7TDMI/language contract")
    return tuple(includes), tuple(system), tuple(binutils), flags


def parse_recipe(command):
    from .graph_commands import _literal_header_words, _shell_tokens

    tokens = _shell_tokens(command, "original toolchain recipe")
    position = 0

    def pattern_operators(token):
        quote, index, operators = None, 0, []
        while index < len(token.raw):
            character = token.raw[index]
            if character == "\\" and quote != "'" and (
                quote is None or token.raw[index + 1:index + 2] in {'$', '`', '"', "\\"}
            ):
                index += 2
                continue
            if character == quote:
                quote = None
            elif quote is None and character in "'\"":
                quote = character
            elif quote is None and character in "*?[]":
                operators.append(character)
            index += 1
        return tuple(operators)

    def signature(token, role="word"):
        # Only the declared grammar position gives a word a syntactic role.
        # For example, test-command argv may quote !; pipeline negation may not.
        active = token.raw if "$" in token.raw or "`" in token.raw else None
        lexical = (
            token.raw if role == "syntax" else token.assignment if role == "assignment"
            else pattern_operators(token) if role == "pattern" else None
        )
        return token.value, token.operator, token.io_number, active, lexical

    def expect(fragment, *, syntax=(), patterns=(), assignments=()):
        nonlocal position
        expected = _shell_tokens(fragment, "toolchain grammar")
        actual = tokens[position:position + len(expected)]
        if len(actual) != len(expected):
            raise MakeProbeError("toolchain recipe differs from its complete original control flow")
        for item, wanted in zip(actual, expected):
            role = (
                "syntax" if wanted.value in syntax else "pattern" if wanted.value in patterns
                else "assignment" if wanted.value.partition("=")[0] in assignments else "word"
            )
            if signature(item, role) != signature(wanted, role):
                raise MakeProbeError("toolchain recipe differs from its complete original control flow")
        position += len(expected)

    def literals(*, assignment=False):
        nonlocal position
        start = position
        while position < len(tokens) and not tokens[position].operator and not tokens[position].io_number:
            position += 1
        selected = tokens[start:position]
        if assignment and (
            len(selected) != 1
            or signature(selected[0], "assignment") != signature(selected[0]._replace(assignment=True), "assignment")
        ):
            raise MakeProbeError("toolchain recipe requires an unquoted assignment name and equals")
        return tuple(_literal_header_words(selected))

    def errors(messages, *, group=False):
        for message in messages:
            expect("printf '%s\\n' " + repr(message) + " >&2;")
        expect("exit 1; " + ("};" if group else "fi;"), syntax=("}", "fi"))

    expect("set -eu;")
    assignment = literals(assignment=True)
    if len(assignment) != 1 or not assignment[0].startswith("cc=") or not assignment[0][3:]:
        raise MakeProbeError("toolchain recipe lost its literal compiler assignment")
    compiler = assignment[0][3:]
    expect('; cc_path=$(command -v "$cc" 2>/dev/null || true);', assignments=("cc_path",))
    expect('if [ -z "$cc_path" ] || [ ! -x "$cc_path" ]; then', syntax=("if", "then"))
    expect('printf \'%s\\n\' "error: modern compiler not found: $cc" >&2;')
    errors(("Install gcc-arm-none-eabi or set MODERN_TOOLCHAIN_ROOT/MODERN_CC.",))
    query_flags = []
    for variable, query, failure in (
        ("version", QUERIES[0], 'printf \'%s\\n\' "error: failed to run modern compiler: $cc" >&2;'),
        ("target", QUERIES[1], 'printf \'%s\\n\' "error: could not query modern compiler target: $cc" >&2;'),
        ("assembler", QUERIES[2], "printf '%s\\n' 'error: could not resolve the assembler used by modern GCC' >&2;"),
    ):
        expect(variable + '=$("$cc"', assignments=(variable,))
        arguments = literals()
        if not arguments or arguments[-1] != query:
            raise MakeProbeError("toolchain recipe changed its real compiler query")
        query_flags.append(arguments[:-1])
        expect("2>&1) || {", syntax=("{",))
        expect(failure)
        expect("exit 1; };", syntax=("}",))
        if variable == "version":
            expect("""printf 'Modern compiler: %s\\n' "$(printf '%s\\n' "$version" | sed -n '1p')";""")
        elif variable == "target":
            expect('if [ "$target" != arm-none-eabi ]; then', syntax=("if", "then"))
            expect("""printf "error: modern compiler targets '%s'; expected 'arm-none-eabi'\\n" "$target" >&2;""")
            expect('exit 1; fi; printf \'Modern target: %s\\n\' "$target";', syntax=("fi",))
    expect('case "$assembler" in /*) resolved_as="$assembler" ;;',
           syntax=("case", "in"), patterns=("/*",), assignments=("resolved_as",))
    expect('*) resolved_as=$(command -v "$assembler" 2>/dev/null || true) ;; esac;',
           syntax=("esac",), patterns=("*",), assignments=("resolved_as",))
    expect('if [ -z "$resolved_as" ] || [ ! -x "$resolved_as" ]; then', syntax=("if", "then"))
    expect("""printf "error: modern GCC resolved assembler '%s', but it is not executable\\n" "$assembler" >&2;""")
    errors(("Install binutils-arm-none-eabi or set MODERN_BINUTILS_DIR.",))
    expect('printf \'Modern assembler: %s\\n\' "$resolved_as";')
    probes = []
    for stdin, failure in ((SYNTAX_INPUT, SYNTAX_FAILURE), (COMPILE_INPUT, COMPILE_FAILURE)):
        expect("if ! printf '%s\\n' " + repr(stdin.rstrip("\n")) + ' | "$cc"', syntax=("if", "!"))
        probes.append(literals())
        expect("; then", syntax=("then",))
        errors(tuple(failure.rstrip("\n").split("\n")))
    expect("printf 'Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\\n'")
    selected = literals()
    if position != len(tokens) or len(selected) != 2 or selected[0] not in {"release", "debug"} or selected[1] not in {"aapcs", "apcs-gnu"}:
        raise MakeProbeError("toolchain recipe has trailing syntax or an unsupported configuration")
    includes, system, binutils, _ = options(probes[0], syntax=True)
    _, _, compile_binutils, compile_flags = options(probes[1], syntax=False)
    abi_flags = {value for value in compile_flags if value.startswith("-mabi=")}
    if (
        any(flags != binutils for flags in query_flags) or compile_binutils != binutils
        or (abi_flags not in (set(), {"-mabi=aapcs"}) if selected[1] == "aapcs"
            else abi_flags != {"-mabi=apcs-gnu"})
    ):
        raise MakeProbeError("toolchain recipe queries/driver/ABI disagree")
    return Recipe(compiler, binutils, *probes, *selected, includes, system)


def environment(values, *, launch=False):
    arm_headers.environment(values)
    if values.get("PATH") != ENVIRONMENT["PATH"] or values.get("TMPDIR") not in (
        ("/work",) if launch else (None, "/work")
    ):
        raise MakeProbeError("toolchain probe requires its exact search and private temporary environment")
    if any(values.get(name) for name in (
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "GCC_COMPARE_DEBUG", "GCC_EXTRA_DIAGNOSTIC_OUTPUT",
        "COLLECT_GCC", "COLLECT_GCC_OPTIONS", "LANGUAGE", "TMP", "TEMP",
    )):
        raise MakeProbeError("toolchain probe has a foreign driver/runtime environment")


def assembler_path(value):
    from .make_probe import _trusted_runtime_path
    import shutil

    spelling = value if value.startswith("/") else shutil.which(value, path=ENVIRONMENT["PATH"])
    if not spelling:
        raise MakeProbeError("modern GCC resolved an unavailable assembler")
    if not spelling.startswith(("/usr/", "/bin/")):
        raise MakeProbeError("modern GCC assembler escaped the supported system roots")
    canonical = str(_trusted_runtime_path(str(Path(spelling).resolve(strict=True)), compiler=True))
    if canonical not in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"} or not os.access(canonical, os.X_OK):
        raise MakeProbeError("modern GCC resolved an unsupported target assembler: " + spelling + " -> " + canonical)
    return spelling, canonical


def launch_binding(config):
    return hashlib.sha256(encoded({name: config[name] for name in LAUNCH_FIELDS})).hexdigest()


def validate_launch(config):
    dependency = config.get("dependency") or {}
    profile = dependency.get("toolchain_probe")
    grant = config.get("toolchain_runtime")
    if profile is None:
        if grant is not None:
            raise ChannelError("unrelated command carries a toolchain launch")
        return None
    if (
        not isinstance(profile, dict) or set(profile) != {"version", "stage", "stdin", "inputs", "driver_identity", "images", "workspace"}
        or type(profile["version"]) is not int or profile["version"] != 1
        or type(profile["stage"]) is not int or profile["stage"] not in range(5)
        or profile["stdin"] != INPUTS[profile["stage"]]
        or not isinstance(profile["inputs"], list)
        or not isinstance(profile["driver_identity"], list) or len(profile["driver_identity"]) != 6
        or any(type(value) is not int or value < 0 for value in profile["driver_identity"])
        or not isinstance(profile["workspace"], list) or len(profile["workspace"]) != 3
        or any(type(value) is not int or value < 0 for value in profile["workspace"])
        or config["mode"] != "compile" or config.get("header_runtime") is not None
        or config.get("private_install") is not None or config.get("producer_endpoint") is not None
        or not isinstance(grant, dict) or set(grant) != {"version", "scope", "binding"}
        or type(grant["version"]) is not int or grant["version"] != 1
        or grant["scope"] != launch_scope(config["root"]) or grant["binding"] != launch_binding(config)
        or config["executables"] != dependency["executables"]
        or not config["argv"] or not config["executables"] or config["argv"][0] != config["executables"][0]
        or config["executables"][0] != "/usr/bin/arm-none-eabi-gcc"
        or config["sources"] or config["enumerations"]
    ):
        raise ChannelError("toolchain launch is unbound, malformed or outside its selected profile")
    stage = profile["stage"]
    try:
        environment(config["environment"], launch=True)
        if stage < 3:
            if config["argv"][1:] not in (
                [QUERIES[stage]], ["-B/bin/", QUERIES[stage]], ["-B/usr/bin/", QUERIES[stage]],
            ):
                raise MakeProbeError("toolchain query changed its exact arguments")
        else:
            options(config["argv"][1:], syntax=stage == 3)
    except MakeProbeError as error:
        raise ChannelError(str(error)) from error
    executables = config["executables"]
    if (
        len(executables) != (1 if stage < 3 else 2 if stage == 3 else 3)
        or stage >= 3 and not re.fullmatch(r"/usr/lib/gcc/arm-none-eabi/[A-Za-z0-9_.+-]+/cc1", executables[1])
        or stage == 4 and executables[2] not in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"}
        or (stage >= 3) != ("header_search" in dependency)
        or stage != 3 and (config["code"] or profile["inputs"])
    ):
        raise ChannelError("toolchain launch has foreign executable or source authority")
    if (
        not isinstance(profile["images"], list) or len(profile["images"]) != len(executables)
        or any(
            not isinstance(row, list) or len(row) != 7 or row[0] != path
            or any(type(value) is not int or value < 0 for value in row[1:])
            for row, path in zip(profile["images"], executables)
        )
        or profile["images"][0][1:] != profile["driver_identity"]
    ):
        raise ChannelError("toolchain launch lacks its actual executable identities")
    inputs = {}
    for row in profile["inputs"]:
        if (
            not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str)
            or not row[0].endswith((".h", ".inc")) or row[0] in inputs
            or type(row[1]) is not int or not 0 <= row[1] <= 0o777
            or type(row[2]) is not int or not 0 <= row[2] <= config["file_limit"]
            or not isinstance(row[3], str) or not re.fullmatch("[0-9a-f]{64}", row[3])
        ):
            raise ChannelError("toolchain launch has malformed source identities")
        try:
            relative_path(row[0])
        except MakeProbeError as error:
            raise ChannelError(str(error)) from error
        inputs[row[0]] = row
    if set(inputs) != set(config["code"]) or stage == 3 and "include/global.h" not in inputs:
        raise ChannelError("toolchain launch lost its exact repository header closure")
    return profile


def verify_workspace(path, expected):
    try:
        if list(directory_identity(path.stat(follow_symlinks=False))) != expected:
            raise MakeProbeError("toolchain workspace changed its actual owned directory")
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                raise MakeProbeError("toolchain workspace is not the original empty private namespace")
    except OSError as error:
        raise MakeProbeError("toolchain workspace is no longer its issued directory") from error


def records(values, profile, executables, *, returncode, argv, environment):
    from json import loads

    executions, inputs = [], []
    for value in values:
        prefix = next((item for item in (EXEC_PREFIX, INPUT_PREFIX) if value.startswith(item)), None)
        if prefix is None:
            continue
        try:
            row = loads(value[len(prefix):])
        except (ValueError, RecursionError) as error:
            raise MakeProbeError("malformed actual toolchain observation") from error
        if prefix == EXEC_PREFIX:
            if (
                not isinstance(row, dict) or set(row) != {"stage", "sequence", "path", "identity", "argv", "environment"}
                or row["stage"] != STAGES[profile["stage"]] or type(row["sequence"]) is not int
                or not 1 <= row["sequence"] <= len(executables)
                or row["path"] != executables[row["sequence"] - 1]
                or not isinstance(row["identity"], list) or len(row["identity"]) != 6
                or any(type(value) is not int or value < 0 for value in row["identity"])
                or row["identity"] != profile["images"][row["sequence"] - 1][1:]
                or not isinstance(row["argv"], list) or not row["argv"]
                or any(not isinstance(value, str) or "\0" in value for value in row["argv"])
                or not row["argv"][0].startswith(("/usr/", "/bin/"))
                or str(Path(row["argv"][0]).resolve(strict=True)) != row["path"]
                or not isinstance(row["environment"], dict)
                or any(not isinstance(key, str) or not isinstance(value, str) for key, value in row["environment"].items())
                or row["sequence"] == 1 and (row["argv"] != list(argv) or row["environment"] != environment)
            ):
                raise MakeProbeError("unbound actual toolchain executable observation")
            executions.append(row)
        else:
            if (
                not isinstance(row, dict) or set(row) != {"stage", "stdin", "eof"}
                or row["stage"] != STAGES[profile["stage"]] or type(row["eof"]) is not bool
                or not isinstance(row["stdin"], str) or not profile["stdin"].startswith(row["stdin"])
                or not returncode and (row["stdin"] != profile["stdin"] or not row["eof"])
            ):
                raise MakeProbeError("unbound actual toolchain stdin observation")
            inputs.append(row)
    executions.sort(key=lambda row: row["sequence"])
    if (
        [row["sequence"] for row in executions] != list(range(1, len(executions) + 1))
        or not executions or not returncode and len(executions) != len(executables)
        or len(inputs) != (1 if profile["stdin"] else 0)
    ):
        raise MakeProbeError("toolchain result omitted actual executable/stdin observations")
    return tuple([*executions, *inputs])


@dataclass(eq=False)
class _RecipeGrant:
    command: object
    recipe: Recipe
    context: object
    binding: bytes
    inputs: tuple
    stage: int = 0
    assembler: str | None = None
    sdk: tuple | None = None
    driver_identity: tuple | None = None


@dataclass(eq=False)
class _Step:
    command: object
    parent: _RecipeGrant
    binding: bytes
    stage: int
    launched: bool = False
    dependency: bytes | None = None


class _Launch:
    pass


class Controller:
    def __init__(self, session):
        self.session = session
        self.commands = {}
        self.steps = {}
        self.launches = {}
        self.issued = weakref.WeakSet()
        self.active = None

    def close(self):
        self.commands.clear()
        self.steps.clear()
        self.launches.clear()
        self.issued.clear()
        self.active = None

    def bind(self, command):
        tool = command.runtime_tool
        return encoded((
            self.session._install_command_binding(command).decode("ascii"),
            None if tool is None else (tool.path, tool.canonical, tool.mode, tool.digest),
        ))

    def register(self, original, compiler):
        from .make_probe import Command, _event_command
        from .graph_commands import _resolve_modern_compiler

        session = self.session
        context = session._require_live_dispatch()
        if (
            context.job[:3] != ("recipe", TARGET, 1) or context.cwd != "/repo"
            or _event_command({"arguments": list(context.arguments)}) != original
        ):
            raise MakeProbeError("toolchain check lacks its actual original scheduled job")
        environment(dict(context.environment))
        recipe = parse_recipe(original)
        if _resolve_modern_compiler(session, recipe.compiler) is not compiler:
            raise MakeProbeError("toolchain recipe differs from its exact selected compiler")
        for flag in recipe.binutils:
            from .graph_commands import _resolve_modern_binutils_flag
            _resolve_modern_binutils_flag(flag)
        roots = {"include", *(path for path in recipe.includes if path != ".")}
        headers = tuple(sorted(
            path for path in session.snapshot.files.keys() | session.published_sources.keys()
            if path.endswith((".h", ".inc")) and any(path.startswith(root + "/") for root in roots)
        ))
        if "include/global.h" not in headers:
            raise MakeProbeError("toolchain check lacks its actual global.h input")
        command = session._native_context_command(Command(
            (compiler.path, *recipe.arguments(0)), code=headers, runtime_tool=compiler,
        ))
        key = id(command)
        grant = _RecipeGrant(
            weakref.ref(command, lambda ref: self.commands.pop(key, None)),
            recipe, context, self.bind(command), tuple(session.source_owners(headers)),
        )
        session.budget.charge("cache", len(grant.binding) + len(encoded(grant.inputs)))
        self.commands[key] = grant
        self.issued.add(grant)
        return command

    def require(self, command):
        session = self.session
        session.budget.remaining()
        grant = self.commands.get(id(command))
        if (
            type(grant) is not _RecipeGrant or grant not in self.issued or grant.command() is not command
            or grant.context is not session._require_live_dispatch()
            or not session._command_dispatches or session._command_dispatches[-1] != (command, grant.context)
            or grant.binding != self.bind(command)
            or tuple(session.source_owners(command.code)) != grant.inputs
        ):
            raise MakeProbeError("toolchain command is unissued, changed, replayed or outside its lifetime")
        environment(session._command_environment(command))
        return grant

    def require_step(self, command):
        step = self.steps.get(id(command))
        if step is None:
            return None
        if (
            type(step) is not _Step or step not in self.issued or step.command is not command
            or step.parent is not self.active or self.require(step.parent.command()) is not step.parent
            or step.stage != step.parent.stage or step.binding != self.bind(command)
            or self.driver_identity(command.runtime_tool) != step.parent.driver_identity
            or step.launched
        ):
            raise MakeProbeError("toolchain substep is unissued, changed or outside its actual parent")
        return step

    def driver_identity(self, tool):
        from .make_probe import _trusted_runtime_path

        if str(_trusted_runtime_path(tool.path)) != tool.canonical:
            raise MakeProbeError("toolchain driver changed its captured canonical path")
        return self.image_identity(tool.path)

    def image_identity(self, path):
        from .make_probe import _trusted_runtime_path

        if str(_trusted_runtime_path(path, compiler=True)) != path:
            raise MakeProbeError("toolchain executable lost its canonical trusted identity")
        info = Path(path).lstat()
        identity = info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        self.session.budget.charge("control", len(encoded(identity)))
        return identity

    def launch(self, command, config):
        step = self.require_step(command)
        if step is None:
            raise MakeProbeError("toolchain launch has no issued substep")
        session = self.session
        root = session.base / f"command-root-{session.serial + 1}"
        output = session.base / f"command-{session.serial + 1}" / "output"
        mounts = [session._mount(session.tree, "/repo"), session._mount(Path("/usr"), "/usr", executable=True)]
        if step.stage >= 3:
            backing, sdk = step.parent.sdk
            mounts.extend(
                session._mount(backing / str(index), path)
                for index, (path, present) in enumerate(sdk["roots"]) if present
            )
        mounts.extend((
            session._mount(output, "/work", writable=True),
            session._mount(Path("/dev/null"), "/dev/null", writable=True),
        ))
        if (
            config["argv"] != list(command.argv) or config["code"] != sorted(set(command.code))
            or config["sources"] or config["enumerations"] or config["root"] != str(root)
            or config["mounts"] != mounts
            or config["environment"] != {**session._command_environment(step.parent.command()), "TMPDIR": "/work"}
            or config["dependency"]["toolchain_probe"]["stage"] != step.stage
            or config["dependency"]["toolchain_probe"]["driver_identity"] != list(step.parent.driver_identity)
            or step.dependency is None or encoded(config["dependency"]) != step.dependency
        ):
            raise MakeProbeError("toolchain issuance differs from its original substep and owned namespace")
        verify_workspace(output, config["dependency"]["toolchain_probe"]["workspace"])
        session.budget.charge("control", len(encoded(config["dependency"]["toolchain_probe"]["workspace"])))
        token = _Launch()
        binding = launch_binding(config)
        validate_launch({
            **config, "file_limit": session.budget.limits.file_bytes,
            "toolchain_runtime": {"version": 1, "scope": launch_scope(root), "binding": binding},
        })
        self.launches[id(token)] = token, command, step, binding
        self.issued.add(token)
        return token

    def consume_launch(self, token, config):
        record = self.launches.pop(id(token), None)
        if type(token) is not _Launch or token not in self.issued or record is None or record[0] is not token:
            raise MakeProbeError("toolchain launch is missing, copied, forged, expired or already consumed")
        self.issued.discard(token)
        _, command, step, binding = record
        if self.require_step(command) is not step or binding != launch_binding(config):
            raise MakeProbeError("toolchain launch changed its job, arguments, environment, input or workspace")
        output, = [Path(item["source"]) for item in config["mounts"] if item["target"] == "/work"]
        verify_workspace(output, config["dependency"]["toolchain_probe"]["workspace"])
        self.session.budget.charge("control", len(encoded(config["dependency"]["toolchain_probe"]["workspace"])))
        step.launched = True
        return {"version": 1, "scope": launch_scope(config["root"]), "binding": binding}

    def execute(self, command):
        from .make_probe import Command, ProcessOutput

        grant = self.require(command)
        if self.active is not None or grant.stage:
            raise MakeProbeError("toolchain command is nested or replayed")
        self.active = grant
        results, stdout, stderr = [], bytearray(), bytearray()
        status = 0
        try:
            self.session._verify_runtime_tool(command.runtime_tool)
            grant.driver_identity = self.driver_identity(command.runtime_tool)
            for stage in range(5):
                if stage != grant.stage:
                    raise MakeProbeError("toolchain substep order changed")
                subcommand = Command(
                    (command.runtime_tool.path, *grant.recipe.arguments(stage)),
                    code=command.code if stage == 3 else (), runtime_tool=command.runtime_tool,
                )
                step = _Step(subcommand, grant, self.bind(subcommand), stage)
                self.steps[id(subcommand)] = step
                self.issued.add(step)
                try:
                    result = self.session._command(subcommand)
                finally:
                    self.steps.pop(id(subcommand), None)
                    self.issued.discard(step)
                results.append(result)
                grant.stage += 1
                if stage < 3:
                    value = result.stdout.rstrip(b"\n")
                    if result.returncode:
                        status = 1
                        stderr.extend((
                            "error: failed to run modern compiler: " + grant.recipe.compiler + "\n",
                            "error: could not query modern compiler target: " + grant.recipe.compiler + "\n",
                            "error: could not resolve the assembler used by modern GCC\n",
                        )[stage].encode())
                        break
                    if stage == 0:
                        stdout.extend(b"Modern compiler: " + value.split(b"\n")[0] + b"\n")
                    elif stage == 1:
                        if value != b"arm-none-eabi":
                            stderr.extend(b"error: modern compiler targets '" + value + b"'; expected 'arm-none-eabi'\n")
                            status = 1
                            break
                        stdout.extend(b"Modern target: " + value + b"\n")
                    else:
                        spelling, grant.assembler = assembler_path(value.decode("utf-8", "strict"))
                        stdout.extend(b"Modern assembler: " + spelling.encode() + b"\n")
                else:
                    stdout.extend(result.stdout)
                    stderr.extend(result.stderr)
                    if result.returncode:
                        status = 1
                        stderr.extend((SYNTAX_FAILURE if stage == 3 else COMPILE_FAILURE).encode())
                        break
            if not status:
                stdout.extend((
                    "Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\n"
                    % (grant.recipe.config, grant.recipe.abi)
                ).encode())
            self.session._verify_runtime_tool(command.runtime_tool)
            if self.driver_identity(command.runtime_tool) != grant.driver_identity:
                raise MakeProbeError("toolchain driver changed during its actual original recipe")
            return ProcessOutput(
                bytes(stdout), bytes(stderr), (), tuple(sorted({name for item in results for name in item.code_consumed})),
                input_identities=tuple(sorted({row for item in results for row in item.input_identities})),
                executed=tuple(name for item in results for name in item.executed),
                runtime_receipt=tuple(sorted({tuple(row) for item in results for row in item.runtime_receipt})),
                runtime_sources=tuple(sorted({row for item in results for row in item.runtime_sources})),
                runtime_probes=tuple(row for item in results for row in item.runtime_probes),
                returncode=status,
            )
        finally:
            self.active = None
            self.commands.pop(id(command), None)
            self.issued.discard(grant)
