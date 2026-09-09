"""Generated-registry declarations observed in the caller's shared source view."""

from __future__ import annotations

from .authority import AuthorityLoader, parse_json, relative_path
from .budget import MakeProbeError, text
from .make_probe import Command, ProbeSession, TRUSTED_ROOT, probe_generated_registry
from .graph_commands import python_import_directories


REGISTRY_DECLARATIONS = """
import json,sys
sys.path.insert(0, "/repo")
from scripts.generated_data.registry import REGISTRY
records = []
for name in REGISTRY.all_names():
    schema = REGISTRY.resolve(name)
    records.append({
        "default_hand_source": schema.default_hand_source,
        "default_inventory_path": schema.default_inventory_path,
        "default_output_name": schema.default_output_name,
        "default_source": schema.default_source,
        "dependencies": sorted(schema.dependencies()),
        "dependency_tables": list(schema.dependency_tables()),
        "name": name,
        "version": schema.version,
    })
print(json.dumps(records, sort_keys=True, separators=(",", ":")))
"""


def registry_code(loader: AuthorityLoader, session: ProbeSession):
    if (
        not isinstance(session, ProbeSession) or session.snapshot is None
        or session.loader is not loader or session.budget is not loader.budget
    ):
        raise MakeProbeError("registry declarations require the selected shared report view")
    code = tuple(sorted(
        path for path in session.snapshot.files
        if path.endswith(".py") and (
            path.startswith(("scripts/generated_data/", "scripts/assets/"))
            or path == "scripts/__init__.py"
        )
    ))
    if "scripts/generated_data/registry.py" not in code:
        raise MakeProbeError("generated-data registry has no captured Python authority")
    return code


def observe_declarations(loader: AuthorityLoader, session: ProbeSession):
    code = registry_code(loader, session)
    output = session.command(Command(
        ("/usr/bin/python3", "-I", "-S", "-B", "-c", REGISTRY_DECLARATIONS),
        code=code, directories=python_import_directories(code),
    ))
    return parse_json(output.stdout, "candidate generated-data registry declarations")


def observe_directory_sources(loader: AuthorityLoader, session: ProbeSession, record):
    code = registry_code(loader, session)
    source = relative_path(record["default_source"])
    pool = tuple(sorted(path for path in session.snapshot.files if path.startswith(source + "/")))
    if not pool:
        raise MakeProbeError("registry directory has no captured source candidates")
    directories = python_import_directories((*code, *pool))
    driver = text(
        session.budget.read_bytes(TRUSTED_ROOT / "generated_registry_probe.py", "control"),
        "generated registry driver",
    )
    argv = ("/usr/bin/python3", "-I", "-S", "-B", "-c", driver, record["name"], source)
    observed = session.command(Command(
        (*argv, "--source-paths"), code=code, directories=directories,
    ))
    paths = parse_json(observed.stdout, "registry directory source discovery")
    if (
        not isinstance(paths, list) or not paths
        or any(not isinstance(path, str) for path in paths)
        or paths != sorted(set(paths))
        or not set(paths) <= set(pool)
    ):
        raise MakeProbeError("registry directory discovery names invalid source candidates")
    result = probe_generated_registry(loader, session=session, command=Command(
        argv, code=code, sources=tuple(paths), directories=directories,
    ))
    if result["name"] != record["name"] or result["version"] != record["version"]:
        raise MakeProbeError("resolved registry identity differs from its declaration")
    return result["source_paths"]
