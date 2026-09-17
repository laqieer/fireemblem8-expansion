"""Graph discovery; the required foundation gate owns its native modules."""

from pathlib import Path
import unittest


def load_tests(loader, standard_tests, pattern):
    native_modules = {
        "test_foundation", "test_producer", "test_dependency", "test_metadata_transport",
        "test_content_publication",
        "test_private_install",
        "test_text_producer",
        "test_header_effects",
        "test_header_pipeline",
        "test_read_epochs",
        "test_source_phases",
        "test_source_effects",
        "test_source_journal",
        "test_source_directories",
        "test_file_ownership",
    }
    suite = unittest.TestSuite()
    for path in sorted(Path(__file__).parent.glob(pattern or "test_*.py")):
        if path.stem not in native_modules:
            suite.addTests(loader.loadTestsFromName(__name__ + "." + path.stem))
    return suite
