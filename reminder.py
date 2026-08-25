#!/usr/bin/env python3
"""
The assessment nudge: portal, Workable window, cross-reference, send.

    python reminder.py --scan-only
    python reminder.py --dry-run
    python reminder.py --force

The code lives in `backend/notifications/reminder.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.notifications.reminder import main

if __name__ == "__main__":
    sys.exit(main())
