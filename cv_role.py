#!/usr/bin/env python3
"""
Ingest and grade a Workable posting that has no assessment.

    python cv_role.py --job EA7059EA8E

The code lives in `backend/pipeline/cv_role.py`. This launcher stays at the
repository root alongside ingest.py and grade.py, so every documented command
in this project is still one word from here.
"""

import sys

from backend.pipeline.cv_role import main

if __name__ == "__main__":
    sys.exit(main())
