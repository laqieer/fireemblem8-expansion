#!/usr/bin/env python3
"""Isolated startup boundary for the live sibling-review gate."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if not sys.flags.isolated:
        print(
            "isolated review gate requires /usr/bin/python3 -I",
            file=sys.stderr,
        )
        return 2
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    from scripts.workflow_pilot import github_review

    return github_review.main()


if __name__ == "__main__":
    raise SystemExit(main())
