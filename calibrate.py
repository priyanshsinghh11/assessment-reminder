#!/usr/bin/env python3
"""
Is the grader using the scale, or just detecting missing sections?

    python calibrate.py
    python calibrate.py --job 33 --rows

The code lives in `backend/pipeline/calibrate.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.pipeline.calibrate import main

if __name__ == "__main__":
    sys.exit(main())
