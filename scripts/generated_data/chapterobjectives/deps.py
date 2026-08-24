"""Discover every live input used to validate chapter objectives."""

from __future__ import annotations

import argparse
import glob
import os
import sys

from . import enabled, generate, inventory, schema as objectives_schema
from ..chapterbundle import schema as bundle_schema


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _owner_source(source):
    if os.path.isabs(source):
        return _canonical(source)
    return _canonical(os.path.join(bundle_schema.REPO_ROOT, source))


def _implementation_module_paths():
    """Return every loaded generated-data implementation module, excluding tests."""
    package_root = _canonical(os.path.join(bundle_schema.REPO_ROOT, "scripts", "generated_data"))
    modules = (objectives_schema, generate, inventory, enabled, bundle_schema)
    bundle_schema.dependency_module_paths()
    paths = set()
    for module in (*modules, *tuple(sys.modules.values())):
        module_path = getattr(module, "__file__", None)
        if module_path is None:
            continue
        module_path = _canonical(module_path)
        if module_path.startswith(package_root + os.sep) and "/tests/" not in module_path:
            paths.add(module_path)
    return tuple(sorted(paths))


def collect_input_paths(objectives_source, bundle_source):
    """Return a sorted complete prerequisite set for objective generation."""
    objective_records = objectives_schema.load_records(objectives_source)
    bundles = bundle_schema.load_records(bundle_source)
    paths = set(objective_records.source_paths)
    paths.update(bundles.source_paths)
    paths.add(_canonical(objectives_source))
    paths.add(_canonical(bundle_source))
    if os.path.isdir(objectives_source):
        paths.add(_canonical(objectives_source))
    if os.path.isdir(bundle_source):
        paths.add(_canonical(bundle_source))
    paths.update(
        (
            _canonical(objectives_schema.CHAPTERS_HEADER),
            _canonical(objectives_schema.EVENT_FLAGS_HEADER),
            _canonical(objectives_schema.character_refs.CHARACTERS_HEADER),
            _canonical(bundle_schema.CHAPTER_SETTINGS_JSON),
            _canonical(bundle_schema.CHAPTER_DATA_ASSET_TABLE_SOURCE),
            _canonical(bundle_schema.ASSET_MANIFEST_PATH),
        )
    )
    paths.update(_implementation_module_paths())
    for bundle in bundles:
        for table in bundle.tables:
            paths.add(_owner_source(table.source))
        paths.add(_owner_source(bundle.support_owners.source))
    paths.add(_canonical(os.path.dirname(bundle_schema.ASSET_MANIFEST_PATH)))
    paths.add(_canonical(bundle_schema.MAP_LAYOUT_DIR))
    paths.add(_canonical(os.path.join(bundle_schema.REPO_ROOT, "assets", "tmx")))
    paths.update(_canonical(path) for path in glob.glob(os.path.join(bundle_schema.MAP_LAYOUT_DIR, "*.json")))
    paths.update(
        _canonical(path)
        for path in glob.glob(os.path.join(bundle_schema.REPO_ROOT, "assets", "tmx", "*.tmx"))
    )
    return tuple(sorted(paths))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--bundle-source", required=True)
    args = parser.parse_args(argv)
    print("\n".join(collect_input_paths(args.source, args.bundle_source)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
