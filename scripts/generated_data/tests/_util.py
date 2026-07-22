"""Shared test helpers.

Scratch directories are created *inside the repository* (never under
``/tmp``) and always removed via ``TemporaryDirectory``'s context manager.
"""

from __future__ import annotations

import os
import tempfile

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SCRATCH_ROOT = os.path.join(os.path.dirname(__file__), ".scratch")


def fixture_path(*parts):
    return os.path.join(FIXTURES_DIR, *parts)


def scratch_dir():
    """Return a context manager yielding a fresh repo-local scratch directory."""
    os.makedirs(SCRATCH_ROOT, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
