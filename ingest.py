#!/usr/bin/env python3
"""
Portal -> MongoDB, plus the missing-artefact screening rule.

    python ingest.py
    python ingest.py --job 33

The code lives in `backend/pipeline/ingest.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.pipeline.ingest import main

if __name__ == "__main__":
    sys.exit(main())
