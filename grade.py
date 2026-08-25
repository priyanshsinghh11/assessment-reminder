#!/usr/bin/env python3
"""
Grade a role, or the whole backlog.

    python grade.py --job 33

The code lives in `backend/pipeline/grade.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.pipeline.grade import main

if __name__ == "__main__":
    sys.exit(main())
