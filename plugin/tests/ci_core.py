"""Run only the core test list used by CI: python tests/ci_core.py [-v]."""
import os
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent


def names():
    lines = (TESTS / "ci_core.txt").read_text(encoding="utf-8").splitlines()
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def main():
    # Same import layout as `python -m unittest discover -s tests` run from plugin/.
    os.chdir(PLUGIN)
    sys.path[0:1] = [str(TESTS), str(PLUGIN)]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names())
    verbosity = 2 if "-v" in sys.argv[1:] else 1
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
