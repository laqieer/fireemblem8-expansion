#!/usr/bin/env python3
"""Load only the selected trusted foundation, not ambient Python hooks."""

import sys
from pathlib import Path

if not sys.flags.isolated:
    raise SystemExit("ownership probe requires python3 -I")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation_ownership.consumer import main

raise SystemExit(main())
