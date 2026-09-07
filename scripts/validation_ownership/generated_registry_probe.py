"""Trusted driver supplied as argv, never imported from a candidate tree."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/repo")
from scripts.generated_data.registry import REGISTRY

schema = REGISTRY.resolve(sys.argv[1])
source = Path("/repo") / sys.argv[2]
records = schema.load_records(str(source))
paths = getattr(records, "source_paths", None)
if paths is None and isinstance(records, dict):
    paths = records.get("source_paths")
if paths is None:
    paths = [str(source)]
concrete = sorted(Path(path).relative_to("/repo").as_posix() for path in paths)
sys.stdout.write(json.dumps({
    "name": schema.name,
    "version": schema.version,
    "source_paths": concrete,
    "record_count": len(records),
}, sort_keys=True, separators=(",", ":")))
