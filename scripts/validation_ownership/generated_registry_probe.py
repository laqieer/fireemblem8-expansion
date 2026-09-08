"""Trusted driver supplied as argv, never imported from a candidate tree."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/repo")
from scripts.generated_data.registry import REGISTRY


def repository_path(value, *, reported=False):
    path = Path(value)
    if ".." in path.parts or path.is_absolute() and not reported:
        raise ValueError("registry source must be repository-relative without parent components")
    return path.relative_to("/repo") if path.is_absolute() else path


source = Path("/repo") / repository_path(sys.argv[2])
schema = REGISTRY.resolve(sys.argv[1])
records = schema.load_records(str(source))
paths = getattr(records, "source_paths", None)
if paths is None and isinstance(records, dict):
    paths = records.get("source_paths")
if paths is None:
    paths = [str(source)]
concrete = sorted(repository_path(path, reported=True).as_posix() for path in paths)
sys.stdout.write(json.dumps({
    "name": schema.name,
    "version": schema.version,
    "source_paths": concrete,
    "record_count": schema.manifest_record_count(records),
}, sort_keys=True, separators=(",", ":")))
