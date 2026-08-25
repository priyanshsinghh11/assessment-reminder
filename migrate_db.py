#!/usr/bin/env python3
"""
Copy an older database into the one MONGO_DB now points at.

    python migrate_db.py --from ajaia_assessments --dry-run

The code lives in `backend/database/migrate_db.py`. This launcher stays at the
repository root so the documented commands -- and the crontab entry -- keep
working from here, unchanged, after the move into packages.
"""

import sys

from backend.database.migrate_db import main

if __name__ == "__main__":
    sys.exit(main())
