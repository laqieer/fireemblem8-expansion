"""Generated-registry declarations observed in the caller's shared source view."""

from __future__ import annotations

from .authority import AuthorityLoader, parse_json, relative_path
from .budget import MakeProbeError
from .make_probe import ProbeSession, probe_generated_registry
from .python_commands import (
    generated_registry_command,
    python_code_closure,
    python_command,
)


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
    if "scripts/generated_data/registry.py" not in session.snapshot.files:
        raise MakeProbeError("generated-data registry has no captured Python authority")
    return python_code_closure(session, REGISTRY_DECLARATIONS)


def observe_declarations(loader: AuthorityLoader, session: ProbeSession):
    code = registry_code(loader, session)
    output = session.command(python_command(session, REGISTRY_DECLARATIONS, code=code))
    return parse_json(output.stdout, "candidate generated-data registry declarations")


def observe_source_paths(loader: AuthorityLoader, session: ProbeSession, record):
    source = relative_path(record["default_source"])
    result = probe_generated_registry(
        loader, session=session,
        command=generated_registry_command(session, record["name"], source),
    )
    if result["name"] != record["name"] or result["version"] != record["version"]:
        raise MakeProbeError("resolved registry identity differs from its declaration")
    return result["source_paths"]
