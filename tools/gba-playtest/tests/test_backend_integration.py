import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


class BackendIntegrationTests(unittest.TestCase):
    def test_backend_compiles_when_libmgba_is_available(self):
        with tempfile.TemporaryDirectory(prefix="gba-playtest-test-") as temporary:
            output = Path(temporary) / "backend"
            try:
                gba_playtest.build_backend(output)
            except gba_playtest.PlaytestError as exc:
                raise unittest.SkipTest(
                    f"libmGBA integration skipped explicitly: {exc}"
                ) from exc
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
