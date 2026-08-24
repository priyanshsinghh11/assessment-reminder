"""
Utility helpers: business-day math and reminder state tracking.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from config import STATE_DIR, STATE_FILE, MAX_REMINDERS_PER_CANDIDATE

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
# State tracking (local JSON file)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt state file. Starting fresh.")
    return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def state_key(email: str, assessment_group: str) -> str:
    """
    Dedupe key: lowercased email scoped to the PORTAL ASSIGNMENT id.

    Deliberately not the Workable shortcode. Many postings feed one assignment
    -- fifteen marketing shortcodes all point at portal 30 -- so a shortcode
    key lets one candidate who applied to five of them receive five identical
    emails carrying the same link. The assignment is what they actually have
    to complete, so that is what "already reminded" has to be counted against.
    """
    return f"{email.strip().lower()}::{assessment_group}"


def should_send_reminder(
    email: str,
    assessment_group: str,
    days_between_reminders: int = 2,
) -> bool:
    """
    Return True if we have not hit the max reminder count
    and enough business days have passed since the last one.
    """
    state = _load_state()
    key = state_key(email, assessment_group)
    record = state.get(key)

    if record is None:
        return True

    if record["reminders_sent"] >= MAX_REMINDERS_PER_CANDIDATE:
        return False

    last = datetime.fromisoformat(record["last_reminder_at"])
    gap = business_days_since(last)
    return gap >= days_between_reminders


def record_reminder(
    email: str,
    assessment_group: str,
    candidate_id: str,
    candidate_name: str,
    job_shortcode: str,
) -> None:
    """Mark that a reminder was sent."""
    state = _load_state()
    key = state_key(email, assessment_group)
    now_iso = datetime.now(timezone.utc).isoformat()

    if key in state:
        state[key]["reminders_sent"] += 1
        state[key]["last_reminder_at"] = now_iso
    else:
        state[key] = {
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "job_shortcode": job_shortcode,
            "reminders_sent": 1,
            "first_reminder_at": now_iso,
            "last_reminder_at": now_iso,
        }

    _save_state(state)
    log.info("Recorded reminder #%d for %s", state[key]["reminders_sent"], email)


def get_reminder_count(email: str, assessment_group: str) -> int:
    """Return how many reminders have already been sent."""
    state = _load_state()
    key = state_key(email, assessment_group)
    record = state.get(key)
    return record["reminders_sent"] if record else 0


def load_reminder_state() -> dict:
    """
    The whole reminder log, keyed by state_key().

    Callers building a list of many candidates should use this once rather
    than calling get_reminder_count() per candidate, which re-reads the file
    every time.
    """
    return _load_state()
