import json
import os
import subprocess
import sys
import unittest

from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.schema import DependencyGraph, SchemaRegistry, TableSchema


class DummySchema(TableSchema):
    name = "dummy"
    version = 1


class StrSubclass(str):
    pass


class UnhashableStrSubclass(str):
    __hash__ = None


class IntSubclass(int):
    pass


class DependencyGraphTests(unittest.TestCase):
    def test_topo_order_respects_dependencies(self):
        graph = DependencyGraph()
        graph.add_dependency("supports", "characters")
        graph.add_dependency("supports", "types")
        graph.add_dependency("characters", "types")
        order = graph.topo_order()
        self.assertLess(order.index("types"), order.index("characters"))
        self.assertLess(order.index("characters"), order.index("supports"))

    def test_topo_order_is_deterministic(self):
        graph = DependencyGraph()
        graph.add_dependency("z_table", "shared_header")
        graph.add_dependency("a_table", "shared_header")
        # Both z_table and a_table become ready simultaneously; tie-break
        # must be alphabetical for determinism across runs.
        order = graph.topo_order()
        self.assertEqual(order, ["shared_header", "a_table", "z_table"])

    def test_cycle_detected(self):
        graph = DependencyGraph()
        graph.add_dependency("a", "b")
        graph.add_dependency("b", "a")
        with self.assertRaises(GeneratedDataError):
            graph.topo_order()

    def test_digest_stable_and_order_independent(self):
        graph1 = DependencyGraph()
        graph1.add_dependency("supports", "characters")
        graph1.add_dependency("supports", "types")

        graph2 = DependencyGraph()
        graph2.add_dependency("supports", "types")
        graph2.add_dependency("supports", "characters")

        self.assertEqual(graph1.digest(), graph2.digest())

    def test_digest_changes_with_shape(self):
        graph1 = DependencyGraph()
        graph1.add_dependency("supports", "characters")

        graph2 = DependencyGraph()
        graph2.add_dependency("supports", "characters")
        graph2.add_dependency("supports", "types")

        self.assertNotEqual(graph1.digest(), graph2.digest())


class SchemaRegistryTests(unittest.TestCase):
    def test_register_and_resolve(self):
        registry = SchemaRegistry()
        schema = registry.register(DummySchema())
        self.assertIs(registry.resolve("dummy", 1), schema)
        self.assertIs(registry.resolve("dummy"), schema)  # latest version by default

    def test_lazy_registration_uses_declared_keys_without_constructing_other_schemas(self):
        registry = SchemaRegistry()
        eager = registry.register(DummySchema())
        calls = []

        class DummySchemaV2(TableSchema):
            name = "dummy"
            version = 2

        class OtherSchema(TableSchema):
            name = "other"
            version = 1

        def newer():
            calls.append("dummy")
            return DummySchemaV2()

        def other():
            calls.append("other")
            return OtherSchema()

        registered = registry.register_factory("dummy", 2, newer)
        registry.register_factory("other", 1, other)
        self.assertIs(registered, newer)
        self.assertEqual(registry.all_names(), ["dummy", "other"])
        self.assertEqual(calls, [])
        self.assertIs(registry.resolve("dummy", 1), eager)
        self.assertEqual(calls, [])
        latest = registry.resolve("dummy")
        self.assertEqual((latest.name, latest.version), ("dummy", 2))
        self.assertEqual(calls, ["dummy"])
        self.assertIs(registry.resolve("dummy"), latest)
        self.assertIs(registry.resolve("dummy", 2), latest)
        self.assertEqual(calls, ["dummy"])
        other_schema = registry.resolve("other")
        self.assertEqual((other_schema.name, other_schema.version), ("other", 1))
        self.assertEqual(calls, ["dummy", "other"])

    def test_duplicate_registration_rejected(self):
        registry = SchemaRegistry()
        registry.register(DummySchema())
        with self.assertRaises(GeneratedDataError):
            registry.register(DummySchema())

    def test_duplicate_eager_and_lazy_registration_rejected(self):
        registry = SchemaRegistry()
        registry.register(DummySchema())

        def factory():
            return DummySchema()

        with self.assertRaises(GeneratedDataError):
            registry.register_factory("dummy", 1, factory)

        registry = SchemaRegistry()
        registry.register_factory("dummy", 1, factory)
        with self.assertRaises(GeneratedDataError):
            registry.register(DummySchema())
        with self.assertRaises(GeneratedDataError):
            registry.register_factory("dummy", 1, factory)

    def test_noncallable_factory_rejected(self):
        registry = SchemaRegistry()
        with self.assertRaises(GeneratedDataError):
            registry.register_factory("dummy", 1, object())

    def test_register_factory_rejects_malformed_declared_keys_without_mutation(self):
        registry = SchemaRegistry()
        schema = registry.register(DummySchema())

        class OtherSchema(TableSchema):
            name = "other"
            version = 1

        for name, version in (
            ("", 1),
            ([], 1),
            ({}, 1),
            (1, 1),
            (1.5, 1),
            (StrSubclass("other"), 1),
            (UnhashableStrSubclass("other"), 1),
            ("other", []),
            ("other", {}),
            ("other", "1"),
            ("other", 1.5),
            ("other", IntSubclass(1)),
            ("other", True),
            ("other", False),
            ("other", 0),
            ("other", -1),
        ):
            with self.subTest(name=name, version=version):
                with self.assertRaises(GeneratedDataError):
                    registry.register_factory(name, version, lambda: OtherSchema())
                self.assertEqual(registry.all_names(), ["dummy"])
                self.assertIs(registry.resolve("dummy"), schema)
        factory = registry.register_factory("other", 1, OtherSchema)
        self.assertIs(factory, OtherSchema)
        self.assertEqual(registry.all_names(), ["dummy", "other"])
        self.assertEqual((registry.resolve("other").name, registry.resolve("other").version), ("other", 1))

    def test_lazy_factory_wrong_identity_is_not_cached(self):
        registry = SchemaRegistry()
        calls = []

        class DummySchemaV2(TableSchema):
            name = "dummy"
            version = 2

        def factory():
            calls.append("dummy")
            if len(calls) == 1:
                return DummySchemaV2()
            return DummySchema()

        registry.register_factory("dummy", 1, factory)
        with self.assertRaises(GeneratedDataError):
            registry.resolve("dummy")
        self.assertEqual(calls, ["dummy"])
        resolved = registry.resolve("dummy")
        self.assertIsInstance(resolved, DummySchema)
        self.assertEqual(calls, ["dummy", "dummy"])
        self.assertIs(registry.resolve("dummy"), resolved)
        self.assertEqual(calls, ["dummy", "dummy"])

    def test_lazy_factory_rejects_malformed_returned_key_attributes_without_caching(self):
        registry = SchemaRegistry()
        calls = []

        class ReturnedSchema(TableSchema):
            pass

        invalid = [
            (StrSubclass("dummy"), 1),
            (UnhashableStrSubclass("dummy"), 1),
            ("dummy", IntSubclass(1)),
            ("dummy", True),
            ("dummy", 1.0),
            ("dummy", 0),
            ("dummy", -1),
        ]

        for name, version in invalid:
            calls.clear()

            def invalid_factory(name=name, version=version):
                calls.append((name, version))
                schema = ReturnedSchema()
                schema.name = name
                schema.version = version
                return schema

            registry = SchemaRegistry()
            registry.register_factory("dummy", 1, invalid_factory)
            with self.subTest(name=name, version=version):
                with self.assertRaises(GeneratedDataError):
                    registry.resolve("dummy")
                self.assertEqual(calls, [(name, version)])

                def valid_factory():
                    calls.append(("dummy", 1))
                    schema = ReturnedSchema()
                    schema.name = "dummy"
                    schema.version = 1
                    return schema

                registry._factories[("dummy", 1)] = valid_factory
                resolved = registry.resolve("dummy")
                self.assertEqual((resolved.name, resolved.version), ("dummy", 1))
                self.assertEqual(calls[-1], ("dummy", 1))
                self.assertIs(registry.resolve("dummy"), resolved)

    def test_lazy_factory_failure_is_not_cached(self):
        registry = SchemaRegistry()
        calls = []

        def factory():
            calls.append("dummy")
            if len(calls) == 1:
                raise RuntimeError("boom")
            return DummySchema()

        registry.register_factory("dummy", 1, factory)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            registry.resolve("dummy")
        self.assertEqual(calls, ["dummy"])
        resolved = registry.resolve("dummy")
        self.assertIsInstance(resolved, DummySchema)
        self.assertEqual(calls, ["dummy", "dummy"])
        self.assertIs(registry.resolve("dummy"), resolved)
        self.assertEqual(calls, ["dummy", "dummy"])

    def test_lazy_factory_same_key_recursion_is_explicit_and_clears(self):
        registry = SchemaRegistry()
        calls = []

        def factory():
            calls.append("dummy")
            if len(calls) == 1:
                registry.resolve("dummy")
            return DummySchema()

        registry.register_factory("dummy", 1, factory)
        with self.assertRaisesRegex(GeneratedDataError, "cannot resolve recursively"):
            registry.resolve("dummy")
        self.assertEqual(calls, ["dummy"])
        resolved = registry.resolve("dummy")
        self.assertIsInstance(resolved, DummySchema)
        self.assertEqual(calls, ["dummy", "dummy"])
        self.assertIs(registry.resolve("dummy"), resolved)
        self.assertEqual(calls, ["dummy", "dummy"])

    def test_unknown_table_raises_with_known_list(self):
        registry = SchemaRegistry()
        registry.register(DummySchema())
        with self.assertRaises(GeneratedDataError) as ctx:
            registry.resolve("nonexistent")
        self.assertIn("unknown schema 'nonexistent'", str(ctx.exception))
        self.assertIn("dummy", str(ctx.exception))

    def test_unknown_lazy_version_uses_declared_keys(self):
        registry = SchemaRegistry()
        registry.register_factory("dummy", 2, DummySchema)
        with self.assertRaises(GeneratedDataError) as ctx:
            registry.resolve("dummy", 1)
        self.assertIn("unknown schema 'dummy' version 1", str(ctx.exception))
        self.assertIn("('dummy', 2)", str(ctx.exception))

    def test_resolve_rejects_malformed_requested_keys_without_mutation(self):
        registry = SchemaRegistry()
        schema = registry.register(DummySchema())
        for name, version in (
            ("", None),
            ([], None),
            ({}, None),
            (StrSubclass("dummy"), None),
            (UnhashableStrSubclass("dummy"), None),
            ("dummy", []),
            ("dummy", {}),
            ("dummy", "1"),
            ("dummy", 1.5),
            ("dummy", IntSubclass(1)),
            ("dummy", True),
            ("dummy", False),
            ("dummy", 0),
            ("dummy", -1),
        ):
            with self.subTest(name=name, version=version):
                with self.assertRaises(GeneratedDataError):
                    if version is None:
                        registry.resolve(name)
                    else:
                        registry.resolve(name, version)
                self.assertEqual(registry.all_names(), ["dummy"])
                self.assertIs(registry.resolve("dummy"), schema)

    def test_real_registry_declares_same_16_table_names(self):
        from scripts.generated_data import registry  # noqa: F401  (registers schemas)
        from scripts.generated_data.schema import REGISTRY

        self.assertEqual(REGISTRY.all_names(), [
            "autoplaystrategies",
            "chapterbundle",
            "chapterobjectives",
            "characters",
            "classes",
            "eventlists",
            "eventscripts",
            "items",
            "movecost",
            "shops",
            "supports",
            "terrainstats",
            "traps",
            "ui_presentation",
            "units",
            "weapontriangle",
        ])

    def test_registry_discovery_and_primary_loads_do_not_initialize_map_parser(self):
        repository_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        program = """
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])

class UnavailableMapParser:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scripts.assets.tmx":
            raise ImportError("TMX initialization is unavailable during discovery/loading")

sys.meta_path.insert(0, UnavailableMapParser())
from scripts.generated_data.registry import REGISTRY
from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.chapterbundle.schema import read_chapter_map_dimensions

root = Path(sys.argv[1])
declarations = [(name, REGISTRY.resolve(name).version) for name in REGISTRY.all_names()]
loads = {}
with tempfile.TemporaryDirectory() as temporary:
    scratch = Path(temporary)
    malformed = scratch / "invalid.json"
    malformed.write_text("{", encoding="utf-8")
    for name, relative_source in (
        ("chapterbundle", "src/data/ch2_bundle.json"),
        ("chapterobjectives", "src/data/chapter_objectives.json"),
        ("autoplaystrategies", "src/data/autoplay_strategies.json"),
    ):
        schema = REGISTRY.resolve(name)
        source = root / relative_source
        file_records = schema.load_records(str(source))
        directory_paths = schema.source_paths(str(source.parent))
        directory_records = schema.load_records(str(source.parent))
        assert schema.build_inventory(file_records) == schema.build_inventory(directory_records)
        try:
            schema.load_records(str(malformed))
        except GeneratedDataError as error:
            assert str(malformed) in str(error)
        else:
            raise AssertionError("malformed primary input was accepted")
        loads[name] = {
            "source": relative_source,
            "directory_paths": [os.path.relpath(path, root) for path in directory_paths],
            "file_count": schema.manifest_record_count(file_records),
            "directory_count": schema.manifest_record_count(directory_records),
        }
    (scratch / "Ch3Map.json").write_text(
        json.dumps({"width": 17, "height": 16}), encoding="utf-8"
    )
    fallback = read_chapter_map_dimensions(
        3,
        chapter_settings_path=str(root / "src/data/chapter_settings.json"),
        asset_table_path=str(root / "src/data/data_8B363C.c"),
        asset_manifest_path=str(scratch / "missing-manifest.json"),
        map_layout_dir=str(scratch),
    )
print(json.dumps({"declarations": declarations, "loads": loads, "fallback": fallback}))
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", program, repository_root],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        from scripts.generated_data.registry import REGISTRY

        self.assertEqual(result["declarations"], [[name, 1] for name in REGISTRY.all_names()])
        for name, source, count in (
            ("chapterbundle", "src/data/ch2_bundle.json", 1),
            ("chapterobjectives", "src/data/chapter_objectives.json", 0),
            ("autoplaystrategies", "src/data/autoplay_strategies.json", 2),
        ):
            self.assertEqual(
                result["loads"][name],
                {
                    "source": source,
                    "directory_paths": [source],
                    "file_count": count,
                    "directory_count": count,
                },
            )
        self.assertEqual(result["fallback"], [17, 16])

    def test_real_registry_runtime_imports_only_selected_factories_in_isolated_processes(self):
        repository_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        eager_body = (
            "import json,sys\n"
            "sys.path.insert(0," + repr(repository_root) + ")\n"
            "from scripts.generated_data.schema import SchemaRegistry\n"
            "from scripts.generated_data.supports.schema import SupportsTableSchema\n"
            "from scripts.generated_data.units.schema import UnitsTableSchema\n"
            "from scripts.generated_data.shops.schema import ShopsTableSchema\n"
            "from scripts.generated_data.traps.schema import TrapsTableSchema\n"
            "from scripts.generated_data.items.schema import ItemsTableSchema\n"
            "from scripts.generated_data.eventscripts.schema import EventScriptsTableSchema\n"
            "from scripts.generated_data.eventlists.schema import EventListsTableSchema\n"
            "from scripts.generated_data.chapterbundle.schema import ChapterBundleTableSchema\n"
            "from scripts.generated_data.classes.schema import ClassesTableSchema\n"
            "from scripts.generated_data.characters.schema import CharactersTableSchema\n"
            "from scripts.generated_data.terrainstats.schema import TerrainStatsTableSchema\n"
            "from scripts.generated_data.movecost.schema import MovecostTableSchema\n"
            "from scripts.generated_data.weapontriangle.schema import WeaponTriangleTableSchema\n"
            "from scripts.generated_data.ui_presentation.schema import UiPresentationTableSchema\n"
            "from scripts.generated_data.chapterobjectives.schema import ChapterObjectivesTableSchema\n"
            "from scripts.generated_data.autoplaystrategies.schema import AutoplayStrategiesTableSchema\n"
            "registry=SchemaRegistry()\n"
            "for schema in (\n"
            " SupportsTableSchema(), UnitsTableSchema(), ShopsTableSchema(), TrapsTableSchema(),\n"
            " ItemsTableSchema(), EventScriptsTableSchema(), EventListsTableSchema(),\n"
            " ChapterBundleTableSchema(), ClassesTableSchema(), CharactersTableSchema(),\n"
            " TerrainStatsTableSchema(), MovecostTableSchema(), WeaponTriangleTableSchema(),\n"
            " UiPresentationTableSchema(), ChapterObjectivesTableSchema(),\n"
            " AutoplayStrategiesTableSchema(),\n"
            "):\n"
            " registry.register(schema)\n"
            "resolved=registry.resolve(sys.argv[1])\n"
            "print(json.dumps({'modules':sorted(name for name in sys.modules "
            "if name.startswith('scripts.generated_data')),"
            "'name':resolved.name,'version':resolved.version,'source':resolved.default_source},"
            "sort_keys=True,separators=(',',':')))\n"
        )
        lazy_body = (
            "import json,sys\n"
            "sys.path.insert(0," + repr(repository_root) + ")\n"
            "from scripts.generated_data.registry import REGISTRY\n"
            "resolved=REGISTRY.resolve(sys.argv[1])\n"
            "print(json.dumps({'modules':sorted(name for name in sys.modules "
            "if name.startswith('scripts.generated_data')),"
            "'name':resolved.name,'version':resolved.version,'source':resolved.default_source},"
            "sort_keys=True,separators=(',',':')))\n"
        )
        for name, source, required_modules, forbidden_modules in (
            (
                "shops",
                "src/data/ch2_shops.json",
                {"scripts.generated_data.shops.schema"},
                {"scripts.generated_data.autoplaystrategies.schema"},
            ),
            (
                "autoplaystrategies",
                "src/data/autoplay_strategies.json",
                {
                    "scripts.generated_data.autoplaystrategies.schema",
                    "scripts.generated_data.chapterobjectives.schema",
                    "scripts.generated_data.chapterbundle.schema",
                },
                {"scripts.generated_data.shops.schema"},
            ),
        ):
            with self.subTest(schema=name):
                lazy = subprocess.run(
                    [sys.executable, "-I", "-S", "-B", "-c", lazy_body, name],
                    cwd=repository_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                eager = subprocess.run(
                    [sys.executable, "-I", "-S", "-B", "-c", eager_body, name],
                    cwd=repository_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                lazy_result = json.loads(lazy.stdout)
                eager_result = json.loads(eager.stdout)
                self.assertEqual(
                    {key: lazy_result[key] for key in ("name", "version", "source")},
                    {"name": name, "version": 1, "source": source},
                )
                self.assertEqual(
                    {key: eager_result[key] for key in ("name", "version", "source")},
                    {"name": name, "version": 1, "source": source},
                )
                for module in required_modules:
                    self.assertIn(module, lazy_result["modules"])
                for module in forbidden_modules:
                    self.assertNotIn(module, lazy_result["modules"])
                    self.assertIn(module, eager_result["modules"])


if __name__ == "__main__":
    unittest.main()
