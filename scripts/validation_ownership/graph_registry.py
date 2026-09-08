"""Generated-registry declarations observed in the caller's shared source view."""

from __future__ import annotations

from .authority import AuthorityLoader, parse_json
from .budget import MakeProbeError
from .make_probe import Command, ProbeSession
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


def observe_declarations(loader: AuthorityLoader, session: ProbeSession):
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
    output = session.command(Command(
        ("/usr/bin/python3", "-I", "-S", "-B", "-c", REGISTRY_DECLARATIONS),
        code=code, directories=python_import_directories(code),
    ))
    return parse_json(output.stdout, "candidate generated-data registry declarations")
