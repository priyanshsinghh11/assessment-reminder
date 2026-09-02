"""
Pure helpers: business-day arithmetic, the dedupe key, and one datetime fix-up.

NOTHING IN THIS MODULE IMPORTS FROM THE REST OF THE PROJECT, and that is the
point. backend/__init__.py states the rule -- "this package imports from
nothing else in the project, which is what keeps the dependency graph acyclic"
-- and this file used to break it: the reminder-state functions that lived here
imported backend.db.reminder_log, so `core`, the bottom of the stack,
reached down into the storage layer above it. Anything in `database` that
wanted a business-day count could not have it without closing a cycle.

Those functions were never core helpers anyway. They are policy over the
reminder log -- who may be mailed, how often, and the claim that settles a race
between two runs -- and they now live next to the collection they read, in
backend/db/reminder_state.py. state_key() stayed behind because both
halves need it and it is a pure string.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Business day helpers
# ---------------------------------------------------------------------------

def business_days_between(start: datetime, end: datetime) -> int:
    """Count weekday-only days between two datetimes."""
    d1 = start.date() if isinstance(start, datetime) else start
    d2 = end.date() if isinstance(end, datetime) else end
    count = 0
    current = d1 + timedelta(days=1)
    while current <= d2:
        if current.weekday() < 5:  # Mon=0 .. Fri=4
            count += 1
        current += timedelta(days=1)
    return count


def business_days_since(dt: datetime) -> int:
    """Business days between dt and now (UTC)."""
    return business_days_between(dt, datetime.now(timezone.utc))


def business_days_since_iso(iso_str: str) -> int:
    """Business days from an ISO timestamp string to now."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return business_days_since(dt)


def business_days_ago(n: int) -> datetime:
    """
    The UTC datetime n business days before now.

    Used only to bound the Workable query cheaply -- the exact window test is
    done locally with business_days_since_iso(), so a day of slack here is
    harmless and avoids an off-by-one at the API boundary.
    """
    dt = datetime.now(timezone.utc)
    remaining = n
    while remaining > 0:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:
            remaining -= 1
    return dt



# ---------------------------------------------------------------------------
# Datetimes off a document
# ---------------------------------------------------------------------------

def aware(value) -> Optional[datetime]:
    """
    A timezone-aware datetime from whatever a Mongo document holds, or None.

    THREE COPIES OF THIS EXISTED -- in auth.py, db/store.py
    and database/reminder_log.py -- and they did not agree. auth's and
    reminder_log's returned None for a value they could not use; store's
    was typed as taking a datetime and read `.tzinfo` off whatever it was
    handed, so a string in that field was an AttributeError on the manager
    review link rather than an expired one. Only reminder_log's parsed ISO
    strings, which is what documents migrated from the old JSON file actually
    contain, because that is what json.dumps left behind.

    This is the union of the three, which is the strictest of them: naive
    datetimes get UTC, ISO strings are parsed (including a trailing Z),
    anything else is None. Records that were migrated and records written since
    both read correctly, because a migration that rewrote every timestamp would
    have been a second chance to lose one.

    The reason it is here rather than in either package: `accounts` and
    `database` both need it and neither may import the other.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The dedupe key
# ---------------------------------------------------------------------------

def state_key(email: str, assessment_group: str) -> str:
    """
    Dedupe key: lowercased email scoped to the PORTAL ASSIGNMENT id.

    Deliberately not the Workable shortcode. Many postings feed one assignment
    -- fifteen marketing shortcodes all point at portal 30 -- so a shortcode
    key lets one candidate who applied to five of them receive five identical
    emails carrying the same link. The assignment is what they actually have
    to complete, so that is what "already reminded" has to be counted against.

    Unchanged from the file-backed version, and it has to be: it is the _id of
    every migrated record.
    """
    return f"{email.strip().lower()}::{assessment_group}"
