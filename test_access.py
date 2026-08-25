#!/usr/bin/env python3
"""
Regression test for the access rules: who signs in, which roles they see.

    python test_access.py

The code lives in `tests/test_access.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from tests.test_access import main

if __name__ == "__main__":
    sys.exit(main())
