"""Graph discovery; the required foundation gate owns its native modules."""

from pathlib import Path
import unittest


def load_tests(loader, standard_tests, pattern):
    native_modules = {"test_foundation", "test_producer", "test_dependency"}
    suite = unittest.TestSuite()
    for path in sorted(Path(__file__).parent.glob(pattern or "test_*.py")):
        if path.stem not in native_modules:
            suite.addTests(loader.loadTestsFromName(__name__ + "." + path.stem))
    return suite
