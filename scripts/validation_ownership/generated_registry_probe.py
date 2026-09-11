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
    if str(value) != path.as_posix():
        raise ValueError("registry source must use a canonical path")
    return path.relative_to("/repo") if path.is_absolute() else path


def support_paths(schema):
    # Older schemas with no count-support inputs retain the empty declaration.
    selector = getattr(schema, "manifest_support_paths", lambda: ())
    paths = selector()
    if not isinstance(paths, (list, tuple)):
        raise ValueError("registry count-support selector must return a list or tuple")
    return sorted(
        repository_path(path, reported=True).as_posix() for path in paths
    )


source = Path("/repo") / repository_path(sys.argv[2])
schema = REGISTRY.resolve(sys.argv[1])
if sys.argv[3:] == ["--source-paths"]:
    paths = schema.source_paths(str(source))
    sys.stdout.write(json.dumps(sorted(
        repository_path(path, reported=True).as_posix() for path in paths
    ), separators=(",", ":")))
elif sys.argv[3:] in (["--manifest-inputs", "file"], ["--manifest-inputs", "directory"]):
    paths = schema.source_paths(str(source)) if sys.argv[4] == "directory" else (str(source),)
    sys.stdout.write(json.dumps({
        "source_paths": sorted(
            repository_path(path, reported=True).as_posix() for path in paths
        ),
        "support_paths": support_paths(schema),
    }, sort_keys=True, separators=(",", ":")))
elif len(sys.argv) == 3 or sys.argv[3:4] == ["--support-paths"]:
    support = [repository_path(path).as_posix() for path in sys.argv[4:]]
    if support != sorted(set(support)):
        raise ValueError("registry count-support arguments must be sorted and unique")
    records = schema.load_records(str(source))
    paths = getattr(records, "source_paths", None)
    if paths is None and isinstance(records, dict):
        paths = records.get("source_paths")
    if paths is None:
        paths = [str(source)]
    record_count = schema.manifest_record_count(records)
    concrete = sorted(
        [repository_path(path, reported=True).as_posix() for path in paths]
        + support
    )
    sys.stdout.write(json.dumps({
        "name": schema.name,
        "version": schema.version,
        "source_paths": concrete,
        "record_count": record_count,
    }, sort_keys=True, separators=(",", ":")))
else:
    raise ValueError("unsupported generated registry probe arguments")
