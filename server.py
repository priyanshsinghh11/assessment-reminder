#!/usr/bin/env python3
"""
The dashboard: serves frontend/ and every endpoint behind it.

    python server.py

The code lives in `backend/web/server.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.web.server import main

if __name__ == "__main__":
    sys.exit(main())
