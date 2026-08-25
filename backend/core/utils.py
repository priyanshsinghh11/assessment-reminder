"""
Utility helpers: business-day math and reminder state tracking.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.config import MAX_REMINDERS_PER_CANDIDATE
from backend.database import reminder_log

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
# State tracking (MongoDB)
# ---------------------------------------------------------------------------
#
# THIS USED TO BE A JSON FILE, and it was the most dangerous thing in the
# system. Every writer read it whole, changed one key and wrote it back over a
# truncated handle, with no lock and no atomic swap. Sixty concurrent writes
# left one survivor, and every lost record is a candidate the next run believes
# has never been contacted. A reader that caught the file mid-truncate got a
# JSONDecodeError, which the loader answered with `{}` -- which is not "the
# file is briefly unreadable" but "nobody has ever been reminded", and that
# re-arms the entire window at once.
#
# It could not be fixed where it was. A threading.Lock cannot see the cron
# process; a file lock cannot see the other container; and on Cloud Run or App
# Service the file is per-instance and dies with the instance. The state moved
# to Mongo, where a single conditional update settles the race -- see
# backend/database/reminder_log.py for the mechanism.
#
# The functions below keep their old names and signatures so every caller is
# unchanged. What changed underneath is that the decision and the record are
# now the same operation instead of three steps with two gaps in them.

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


def _gap_satisfied(days_between_reminders: int):
    """
    A test for "enough business days have passed", as a callable.

    Passed down to the claim so the arithmetic stays here. Business days are
    not something a Mongo query can express, and approximating them with a
    calendar cutoff would quietly change who is eligible around every weekend.
    """
    def satisfied(previous) -> bool:
        return business_days_since(previous) >= days_between_reminders
    return satisfied


def should_send_reminder(
    email: str,
    assessment_group: str,
    days_between_reminders: int = 2,
) -> bool:
    """
    Would this candidate be eligible right now?

    READ-ONLY, AND THAT MAKES IT ADVISORY. It answers for the dashboard, the
    dry run and the log line; it is NOT what protects against a double send,
    because anything that checks and then acts has a gap in the middle. The
    real gate is claim_reminder(), which decides and records in one operation.

    Errs toward False. If the log cannot be read, the honest answer is "I don't
    know", and the only safe way to render that is as "not eligible" -- the
    alternative is a dashboard inviting somebody to send a duplicate.
    """
    key = state_key(email, assessment_group)
    try:
        record = reminder_log.get(key)
    except reminder_log.ReminderLogUnavailable as exc:
        log.error("Cannot read the reminder log (%s). Reporting %s as not "
                  "eligible rather than risking a duplicate.", exc, email)
        return False

    if record is None:
        return True
    if reminder_log.is_suppressed(record):
        return False
    if reminder_log.reminders_sent(record) >= MAX_REMINDERS_PER_CANDIDATE:
        return False

    previous = reminder_log.last_reminder_at(record)
    if previous is None:
        # A record with no usable timestamp. Once a KeyError that aborted the
        # whole run; now one candidate reported as not eligible, with the
        # reason in the log.
        return False
    return business_days_since(previous) >= days_between_reminders


def claim_reminder(
    email: str,
    assessment_group: str,
    candidate_id: str,
    candidate_name: str,
    job_shortcode: str,
    days_between_reminders: int = 2,
) -> Optional[int]:
    """
    Take the right to send one reminder, atomically. The number of the reminder
    this claim is for, or None if this candidate must not be mailed.

    THIS IS THE GATE. Call it immediately before sending and send only if it
    returns a number. Two processes calling it at the same moment produce
    exactly one number and one None.

    Claimed BEFORE the send rather than recorded after, which is the same
    ordering send_batch() already uses within a run: a transport failure must
    not leave the row available as a second attempt at the same person. Use
    release_reminder() when the transport refuses outright and the candidate
    has genuinely had nothing.

    Raises ReminderLogUnavailable if the log cannot be reached, so the caller
    stops rather than treating an outage as permission.
    """
    return reminder_log.claim(
        key=state_key(email, assessment_group),
        email=email,
        assessment_group=assessment_group,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        job_shortcode=job_shortcode,
        max_reminders=MAX_REMINDERS_PER_CANDIDATE,
        gap_satisfied=_gap_satisfied(days_between_reminders),
    )


def release_reminder(email: str, assessment_group: str,
                     claimed_number: int) -> bool:
    """Hand a claim back when the send never happened."""
    return reminder_log.release(state_key(email, assessment_group),
                                claimed_number)


def record_reminder(
    email: str,
    assessment_group: str,
    candidate_id: str,
    candidate_name: str,
    job_shortcode: str,
) -> None:
    """
    Mark that a reminder was sent.

    KEPT FOR CALLERS THAT STILL RECORD AFTER SENDING. It is claim_reminder()
    with the answer discarded, so it is atomic in the same way -- but it cannot
    stop a send that has already gone out, which is why the send path uses the
    claim instead and this exists for anything that legitimately records after
    the fact.
    """
    number = claim_reminder(email, assessment_group, candidate_id,
                            candidate_name, job_shortcode,
                            days_between_reminders=0)
    if number is None:
        log.info("Nothing recorded for %s: the log already accounts for it.",
                 email)
    else:
        log.info("Recorded reminder #%d for %s", number, email)


def get_reminder_count(email: str, assessment_group: str) -> int:
    """How many reminders have already been sent."""
    try:
        return reminder_log.reminders_sent(
            reminder_log.get(state_key(email, assessment_group)))
    except reminder_log.ReminderLogUnavailable as exc:
        log.error("Cannot read the reminder log: %s", exc)
        raise


def load_reminder_state() -> dict:
    """
    The whole reminder log, keyed by state_key().

    Callers annotating many candidates should use this once rather than calling
    get_reminder_count() per candidate.
    """
    return reminder_log.load_all()
