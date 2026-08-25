#!/usr/bin/env python3
"""
Move state/reminder_log.json into MongoDB.

    python migrate_reminder_log.py --dry-run
    python migrate_reminder_log.py
    python migrate_reminder_log.py --verify

The code lives in `backend/database/migrate_reminder_log.py`. This launcher
stays at the repository root so the documented command works from here,
unchanged, like every other CLI in this project.
"""

import sys

from backend.database.migrate_reminder_log import main

if __name__ == "__main__":
    sys.exit(main())
