#!/usr/bin/env python3
"""Load only the selected trusted foundation, not ambient Python hooks."""

import sys
from pathlib import Path

if not sys.flags.isolated or not sys.flags.no_site:
    raise SystemExit("ownership probe requires python3 -I -S")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation_ownership.consumer import main

raise SystemExit(main())
