#!/usr/bin/env python3
"""Emit the candidate generated-data registry as bounded typed JSON."""

from __future__ import annotations

import json
import sys


sys.path.insert(0, "/repo")

from scripts.generated_data.registry import REGISTRY


def main() -> int:
    records = []
    for name in REGISTRY.all_names():
        schema = REGISTRY.resolve(name)
        records.append(
            {
                "default_hand_source": schema.default_hand_source,
                "default_inventory_path": schema.default_inventory_path,
                "default_output_name": schema.default_output_name,
                "default_source": schema.default_source,
                "dependencies": sorted(schema.dependencies()),
                "dependency_tables": list(schema.dependency_tables()),
                "name": name,
                "version": schema.version,
            }
        )
    sys.stdout.write(
        json.dumps(
            records,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
