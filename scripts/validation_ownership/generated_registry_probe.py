#!/usr/bin/env python3
"""Emit the candidate generated-data registry as bounded typed JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, "/repo")

from scripts.generated_data.registry import REGISTRY


REPOSITORY_ROOT = Path("/repo")


def _source_paths(schema) -> list[str]:
    source = schema.default_source
    if source is None:
        return []
    source_path = Path(source)
    records = schema.load_records(source)
    if source_path.is_file():
        return sorted(
            [
                source_path.as_posix(),
                *getattr(schema, "default_additional_sources", ()),
            ]
        )
    if not source_path.is_dir():
        raise RuntimeError(
            f"generated-data source {source!r} is neither a file nor a directory"
        )
    resolved = getattr(records, "source_paths", None)
    if resolved is None and isinstance(records, dict):
        resolved = records.get("source_paths")
    if resolved is None:
        raise RuntimeError(
            f"generated-data directory source {source!r} does not expose source_paths"
        )
    paths = []
    for item in resolved:
        path = Path(item).resolve(strict=True)
        try:
            relative = path.relative_to(REPOSITORY_ROOT)
        except ValueError as error:
            raise RuntimeError(
                f"generated-data source {path} escapes the repository"
            ) from error
        if not path.is_file():
            raise RuntimeError(
                f"generated-data source {relative.as_posix()!r} is not a file"
            )
        paths.append(relative.as_posix())
    paths.extend(getattr(schema, "default_additional_sources", ()))
    return sorted(paths)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "list":
        payload = REGISTRY.all_names()
    elif len(sys.argv) == 3 and sys.argv[1] == "metadata":
        name = sys.argv[2]
        schema = REGISTRY.resolve(name)
        payload = {
            "default_additional_sources": list(
                getattr(schema, "default_additional_sources", ())
            ),
            "default_hand_source": schema.default_hand_source,
            "default_inventory_path": schema.default_inventory_path,
            "default_output_name": schema.default_output_name,
            "default_source": schema.default_source,
            "default_source_pattern": getattr(
                schema,
                "default_source_pattern",
                None,
            ),
            "dependencies": sorted(schema.dependencies()),
            "dependency_tables": list(schema.dependency_tables()),
            "name": name,
            "version": schema.version,
        }
    elif len(sys.argv) == 3 and sys.argv[1] == "load":
        payload = _source_paths(REGISTRY.resolve(sys.argv[2]))
    else:
        raise RuntimeError(
            "generated registry probe requires list, metadata NAME, or load NAME"
        )
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
