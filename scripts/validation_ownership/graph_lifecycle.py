"""Verified consumer dispatch plus nonrecursive, already-measured checks."""

from dataclasses import dataclass
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import posixpath
import tempfile
import weakref

from scripts.bash_parser import BashToken, normalize_bash_script_commands, tokenize_bash_command
from .authority import ENVIRONMENT, encoded, parse_json
from .budget import MakeProbeError


@dataclass(frozen=True, eq=False)
class _Bindings:
    session: object
    loader: object
    snapshot: object
    model: dict
    graph_bytes: bytes
    authority_bytes: bytes
    routes: tuple


_issued = weakref.WeakSet()


def _command_words(command):
    try:
        lines = normalize_bash_script_commands(command, "lifecycle dispatch")
        if len(lines) != 1:
            raise MakeProbeError("lifecycle dispatch must be one mandatory command")
        tokens = tokenize_bash_command(lines[0])
    except ValueError as error:
        raise MakeProbeError("lifecycle dispatch has invalid shell syntax") from error
    if tokens[-2:] == (BashToken(">", True), BashToken("/dev/null", False)):
        tokens = tokens[:-2]
    words = [token.value for token in tokens]
    if (
        not words or any(token.operator for token in tokens)
        or any("$" in word or "`" in word for word in words)
    ):
        raise MakeProbeError("lifecycle dispatch is conditional, redirected or unproven")
    return words


def _source_path(spelling, *, source_root, snapshot, directory=False):
    if not spelling:
        raise MakeProbeError("lifecycle path has an empty original spelling")
    if spelling.startswith("/"):
        if spelling == source_root:
            spelling = ""
        elif spelling.startswith(source_root + "/"):
            spelling = spelling[len(source_root) + 1:]
        else:
            raise MakeProbeError("lifecycle path leaves its selected source namespace")
    components = []
    parts = spelling.split("/")
    for index, part in enumerate(parts):
        snapshot.budget.remaining()
        if part == "..":
            if not components:
                raise MakeProbeError("lifecycle path leaves its selected source namespace")
            components.pop()
        elif part not in {"", "."}:
            components.append(part)
        name = "/".join(components)
        is_directory = (
            not name or name in snapshot.gitlink_roots
            or any(path.startswith(name + "/") for path in snapshot.files)
        )
        if index < len(parts) - 1 or directory:
            if not is_directory:
                raise MakeProbeError("lifecycle path has a missing or non-directory component")
        elif name not in snapshot.files:
            raise MakeProbeError("lifecycle checker path is not a selected regular source")
    return source_root + ("/" + "/".join(components) if components else "")


def _checker_dispatch(arguments, *, cwd, source_root, snapshot):
    from . import isolated_launcher

    if not arguments or arguments[0] not in {"/usr/bin/python3", "python3"}:
        raise MakeProbeError("lifecycle dispatch does not invoke the trusted Python checker")
    index, flags = 1, set()
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index][1:]
        if not option or set(option) - {"I", "S", "B"}:
            raise MakeProbeError("lifecycle Python invocation is not isolated/no-site")
        flags.update(option)
        index += 1
    if flags != {"I", "S", "B"} or len(arguments) <= index + 1:
        raise MakeProbeError("lifecycle Python invocation lacks its complete checker route")
    if cwd != source_root:
        raise MakeProbeError("lifecycle dispatch changed its selected source directory")
    program = _source_path(arguments[index], source_root=source_root, snapshot=snapshot)
    if program != source_root + "/scripts/validation_ownership/isolated_launcher.py":
        raise MakeProbeError("lifecycle dispatch uses a substituted checker")
    mode = arguments[index + 1]
    if mode != "check":
        raise MakeProbeError("lifecycle dispatch is not the artifact-consuming check")
    try:
        with redirect_stdout(StringIO()):
            _, parsed = isolated_launcher.graph_dispatch(mode, arguments[index + 2:])
    except (ValueError, SystemExit) as error:
        raise MakeProbeError("lifecycle dispatch does not reach the graph checker") from error
    actual_root = _source_path(
        parsed.repository_root, source_root=source_root, snapshot=snapshot, directory=True,
    )
    if actual_root != source_root or parsed.revision != "HEAD" or parsed.base_revision is not None:
        raise MakeProbeError("lifecycle dispatch selects a different root or graph context")
    return ("/usr/bin/python3", program, mode, actual_root, parsed.revision)


def _startup_environment(dispatch, arguments, *, shell):
    environment = dispatch.get("environment")
    if not isinstance(environment, dict):
        raise MakeProbeError("lifecycle dispatch lacks its captured startup environment")
    loader_controls = {
        name for name in environment
        if name.startswith(("LD_", "MALLOC_"))
        or name in {"GLIBC_TUNABLES", "GCONV_PATH", "LOCPATH", "NLSPATH"}
    }
    if loader_controls:
        raise MakeProbeError("lifecycle dispatch has unsupported loader controls: " + ", ".join(sorted(loader_controls)))
    shell_controls = {
        name for name in environment
        if name in {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"} or name.startswith("BASH_FUNC_")
    }
    if shell and shell_controls:
        raise MakeProbeError("lifecycle dispatch has unsupported shell startup controls: " + ", ".join(sorted(shell_controls)))
    if arguments[0] == "python3" and environment.get("PATH") != ENVIRONMENT["PATH"]:
        raise MakeProbeError("lifecycle unqualified checker lacks its captured controlled PATH")


def _consumer_routes(graph, make_authorities, tester_cases, runtime_programs, source_root, snapshot):
    definition = graph["artifact"]
    target, case_id = definition["executable_consumer"], definition["consistency_check"]
    if target not in make_authorities or case_id not in tester_cases:
        raise MakeProbeError("lifecycle binding lacks its captured consumer authority")
    make_routes = []
    for variant in make_authorities[target]["record"]["variants"]:
        record = variant["record"]
        files = record["files"]
        if len(files) != 1 or files[0]["target"] != target or files[0]["prerequisites"]:
            raise MakeProbeError("lifecycle Make consumer requires a direct, mandatory checker recipe")
        recipe = files[0]["recipe"].lstrip(" \t\n")
        while recipe.startswith("@"):
            recipe = recipe[1:].lstrip(" \t\n")
        if not recipe or recipe.startswith(("-", "+")):
            raise MakeProbeError("lifecycle Make consumer suppresses or changes failure propagation")
        if "$(CURDIR)" in recipe or "${CURDIR}" in recipe:
            directory = files[0]["variables"].get("CURDIR")
            if not isinstance(directory, dict) or directory.get("value") != "/repo":
                raise MakeProbeError("lifecycle Make root lacks its native variable observation")
            recipe = recipe.replace("$(CURDIR)", "/repo").replace("${CURDIR}", "/repo")
        if "$" in recipe:
            raise MakeProbeError("lifecycle Make consumer has unproven conditional or variable dispatch")
        declared = _checker_dispatch(
            _command_words(recipe), cwd="/repo", source_root="/repo", snapshot=snapshot,
        )
        dispatches = record.get("recipe_dispatches")
        if not isinstance(dispatches, list) or len(dispatches) != 1:
            raise MakeProbeError("lifecycle Make consumer did not actually dispatch its checker")
        dispatch = dispatches[0]
        if dispatch["ignore_errors"] or dispatch["cwd"] != "/repo":
            raise MakeProbeError("lifecycle Make consumer ignores failure or changes its root")
        arguments = dispatch["arguments"]
        executable = runtime_programs.get(dispatch["executable"], dispatch["executable"])
        shell = executable in {"/bin/sh", "/bin/bash"}
        if shell:
            if len(arguments) != 3 or arguments[1] not in {"-c", "-ec"}:
                raise MakeProbeError("lifecycle shell dispatch is not a supported command")
            arguments = _command_words(arguments[2])
        elif executable != "/usr/bin/python3":
            raise MakeProbeError("lifecycle native dispatch is not the checker: " + dispatch["executable"])
        _startup_environment(dispatch, arguments, shell=shell)
        actual = _checker_dispatch(arguments, cwd="/repo", source_root="/repo", snapshot=snapshot)
        if actual != declared:
            raise MakeProbeError("lifecycle native dispatch differs from the captured recipe")
        make_routes.append(actual)
    if not make_routes:
        raise MakeProbeError("lifecycle Make consumer has no measured dispatch")
    automation = tester_cases[case_id].get("automation")
    if not isinstance(automation, list) or not automation:
        raise MakeProbeError("lifecycle consistency case has no executable automation")
    case_routes = []
    for entry in automation:
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            raise MakeProbeError("lifecycle consistency automation is malformed")
        words = _command_words(entry["command"])
        checker = source_root + "/scripts/validation_ownership/isolated_launcher.py"
        positions = [
            index for index, word in enumerate(words)
            if posixpath.normpath(posixpath.join(source_root, word)) == checker
        ]
        if not positions or words[positions[0] + 1:positions[0] + 2] != ["check"]:
            continue
        case_routes.append(_checker_dispatch(
            words, cwd=source_root, source_root=source_root, snapshot=snapshot,
        ))
    if not case_routes:
        raise MakeProbeError("lifecycle consistency case lacks a mandatory artifact-consuming check")
    return ((target, tuple(make_routes)), (case_id, tuple(case_routes)))


def bind(graph, *, session, model, make_authorities, tester_cases):
    from . import ci_verifier, isolated_launcher, reporter

    if (
        session is None or session.base is None or model.get("graph") is not graph
        or reporter._binding_models.get(id(model)) is not model
        or model.get("lifecycle_authorities") != (make_authorities, tester_cases)
    ):
        raise MakeProbeError("lifecycle binding requires the active validated graph model")
    runtime_programs = {
        item.canonical: item.path for item in session.runtime_inputs
        if item.data is not None and item.path in {"/bin/sh", "/bin/bash", "/usr/bin/python3"}
    }
    for item in session.runtime_inputs:
        for alias, destination in item.aliases:
            for program in ("/bin/sh", "/bin/bash", "/usr/bin/python3"):
                if program.startswith(alias + "/"):
                    mapped = posixpath.normpath(posixpath.join(
                        posixpath.dirname(alias), destination, program[len(alias) + 1:],
                    ))
                    runtime_programs[mapped] = program
    routes = _consumer_routes(
        graph, make_authorities, tester_cases, runtime_programs, session.loader.root.as_posix(), session.snapshot,
    )
    # The parser/dispatch conclusion applies only to the implementation whose
    # actual loaded sources match this immutable selected source view.
    ci_verifier._verify_loaded_modules(isolated_launcher.ROOT, session.loader)
    graph_bytes, authority_bytes = encoded(graph), encoded(model["authorities"])
    session.budget.charge("cache", len(graph_bytes) + len(authority_bytes) + len(encoded(routes)))
    binding = _Bindings(
        weakref.ref(session), weakref.ref(session.loader), weakref.ref(session.snapshot),
        weakref.ref(model), graph_bytes, authority_bytes, routes,
    )
    _issued.add(binding)
    model["lifecycle_bindings"] = binding


def _require_bindings(graph, session, model):
    binding = model.get("lifecycle_bindings")
    if (
        type(binding) is not _Bindings or binding not in _issued
        or binding.session() is not session or binding.loader() is not session.loader
        or binding.snapshot() is not session.snapshot or binding.model() is not model
        or binding.graph_bytes != encoded(graph)
        or binding.authority_bytes != encoded(model.get("authorities"))
    ):
        raise MakeProbeError("lifecycle requires verified source/session/graph dispatch bindings")
    session.budget.remaining()
    return binding


def check(artifact_root, check_id, *, session, graph, schema, oracle, model):
    from . import reporter

    if session is None or session.base is None or model.get("graph") != graph:
        raise MakeProbeError("lifecycle requires the active validated report model")
    _require_bindings(graph, session, model)
    definition = graph["artifact"]
    if check_id not in (definition["executable_consumer"], definition["consistency_check"]):
        raise MakeProbeError("lifecycle check is not allowlisted")
    artifact_root = Path(artifact_root).resolve(strict=True)
    allowed = (
        session.base,
        session.loader.root / "build/test-artifacts/validation-ownership",
    )
    if not any(root == artifact_root or root in artifact_root.parents for root in allowed):
        raise MakeProbeError("lifecycle artifact root is outside the owned report workspace")
    artifact = artifact_root / reporter.GRAPH_PATH
    if not artifact.is_file() or artifact.is_symlink():
        raise MakeProbeError(
            "validation ownership graph artifact is missing: " + reporter.LIFECYCLE_FAILURE_REASON
        )
    actual = parse_json(session.budget.read_bytes(artifact, "control"), "lifecycle graph")
    reporter.validate_json_schema(actual, schema, schema, budget=session.budget)
    if actual != graph:
        raise MakeProbeError("lifecycle artifact differs from the measured graph")
    reporter.validate_probe_oracle(oracle, actual, model["entries"])
    reporter._measure(oracle, actual, model)
    if (check_id == definition["consistency_check"]
            and check_id not in reporter._load_test_case_registry(session.loader)):
        raise MakeProbeError("ownership consistency tester case is stale")
    return 0


def prove(root, graph, *, session, schema, oracle, model):
    from . import reporter

    if session is None or session.base is None:
        raise MakeProbeError("lifecycle proof requires the report's active session")
    _require_bindings(graph, session, model)
    triggers = {event["id"]: event for event in graph["lifecycle_events"]
                if event["type"] in reporter.REQUIRED_PROOF_KINDS}
    proofs = [event for event in graph["lifecycle_events"] if event["type"] == "deletion_proof"]
    checks = (graph["artifact"]["executable_consumer"], graph["artifact"]["consistency_check"])
    results = []
    for event in proofs:
        with tempfile.TemporaryDirectory(prefix="graph-lifecycle-", dir=session.base) as directory:
            artifact_root = Path(directory)
            path = artifact_root / reporter.GRAPH_PATH
            path.parent.mkdir()
            payload = encoded(graph)
            session.budget.charge("control", len(payload))
            path.write_bytes(payload)
            for check_id in checks:
                check(artifact_root, check_id, session=session, graph=graph,
                      schema=schema, oracle=oracle, model=model)
            backup = artifact_root / "graph.backup"
            path.replace(backup)
            try:
                for check_id in checks:
                    try:
                        check(artifact_root, check_id, session=session, graph=graph,
                              schema=schema, oracle=oracle, model=model)
                    except MakeProbeError as error:
                        if reporter.LIFECYCLE_FAILURE_REASON not in str(error):
                            raise
                    else:
                        raise MakeProbeError("lifecycle artifact removal did not fail: " + check_id)
            finally:
                backup.replace(path)
            for check_id in checks:
                check(artifact_root, check_id, session=session, graph=graph,
                      schema=schema, oracle=oracle, model=model)
        results.append({
            "trigger_event_id": event["trigger_event_id"],
            "trigger_type": triggers[event["trigger_event_id"]]["type"],
            "proof_id": event["id"], "removal": "fail",
            "reason": reporter.LIFECYCLE_FAILURE_REASON, "restoration": "pass",
            "semantics": "verified-dispatch-and-shared-checker",
            "verified_routes": list(checks),
        })
    return results
