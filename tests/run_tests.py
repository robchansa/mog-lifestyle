#!/usr/bin/env python3
"""Run the whole suite:  python3 tests/run_tests.py [-v] [pattern]"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests  # noqa: E402,F401  (configures the test environment)


def main(argv: list[str]) -> int:
    verbosity = 2 if "-v" in argv else 1
    pattern = next((a for a in argv[1:] if not a.startswith("-")), "test_*.py")
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern=pattern, top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=verbosity, buffer=True).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
