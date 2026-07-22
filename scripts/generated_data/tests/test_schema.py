import unittest

from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.schema import DependencyGraph, SchemaRegistry, TableSchema


class DummySchema(TableSchema):
    name = "dummy"
    version = 1


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

    def test_duplicate_registration_rejected(self):
        registry = SchemaRegistry()
        registry.register(DummySchema())
        with self.assertRaises(GeneratedDataError):
            registry.register(DummySchema())

    def test_unknown_table_raises_with_known_list(self):
        registry = SchemaRegistry()
        registry.register(DummySchema())
        with self.assertRaises(GeneratedDataError) as ctx:
            registry.resolve("nonexistent")
        self.assertIn("unknown schema 'nonexistent'", str(ctx.exception))
        self.assertIn("dummy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
