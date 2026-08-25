#!/usr/bin/env python3
"""
Dashboard accounts from the terminal: the first admin, and the way back in.

    python manage_users.py list
    python manage_users.py add priya@ajaia.ai --admin

The code lives in `backend/accounts/manage_users.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.accounts.manage_users import main

if __name__ == "__main__":
    sys.exit(main())
