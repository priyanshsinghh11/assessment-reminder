#!/usr/bin/env python3
"""
Re-score submissions that already carry a verdict.

    python regrade.py --job 33

The code lives in `backend/pipeline/regrade.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.pipeline.regrade import main

if __name__ == "__main__":
    sys.exit(main())
