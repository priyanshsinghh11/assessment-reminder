"""
The reminder dedupe log, in MongoDB, claimed atomically.

WHAT THIS REPLACES AND WHY. This state used to be a JSON file that every writer
read whole, mutated, and wrote back over a truncated handle -- no lock, no
atomic swap. Sixty concurrent writes left one survivor. Every lost record is a
candidate the next run believes has never been contacted, so it mails them
again. Worse, a reader that caught the file mid-truncate got a JSONDecodeError,
which the loader answered by returning `{}` -- indistinguishable from "nobody
has ever been reminded", which re-arms the entire window at once.

None of that is fixable with a lock. A `threading.Lock` cannot see another
process, and the cron job and the dashboard are two processes; a file lock
cannot see another container. The state has to live somewhere that can settle a
race, so it lives here.

CLAIM, THEN SEND -- NOT SEND, THEN RECORD.

The old flow asked `should_send_reminder()` and, if it said yes, sent and then
wrote the record. Those are three steps with two gaps in them, and a second
process can pass the same check in either gap. Instead `claim()` decides and
records in ONE conditional update: it increments only if the document still
looks exactly the way it did when the decision was made. Two processes racing
means one update matches and the other does not, so exactly one email is sent.

That ordering also matches what send_batch() already does within a run -- it
adds to `seen_this_run` BEFORE the send, with the comment "a Brevo failure must
not let the duplicate row through as a second attempt at the same person". The
same reasoning applies across runs: a claim that is consumed by a failed send
costs one reminder to one candidate, and the alternative costs a duplicate.

THE COMPARE-AND-SWAP IS ON `last_reminder_at`, NOT A VERSION FIELD. It changes
on every successful claim, and it is the value the gap decision was made from,
so it is exactly the right thing to test. A filter that matched only on
`reminders_sent` would let two processes that read the same count both win when
one of them had already moved the clock.

WHY THE GAP CHECK STAYS IN PYTHON. It is counted in BUSINESS days, which Mongo
cannot express in a query. Rather than approximate it with a calendar-day
cutoff -- which would quietly change who is eligible around every weekend --
the decision is made in Python from the document that was read, and the update
refuses to apply if that document has since moved.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.db import store
from backend.utils import aware as _aware

log = logging.getLogger(__name__)

COLLECTION = "reminders"


class ReminderLogUnavailable(RuntimeError):
    """
    The dedupe log could not be reached.

    Raised rather than swallowed, and the callers let it stop the run. The
    whole point of this store is that "I don't know whether we already emailed
    this person" must never be answered by emailing them.
    """


def _collection():
    try:
        return store.get_db()[COLLECTION]
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc


def get(key: str) -> Optional[dict]:
    """One record, or None."""
    try:
        return _collection().find_one({"_id": key})
    except ReminderLogUnavailable:
        raise
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc


def load_all() -> dict:
    """
    Every record, keyed by state_key(), in the shape the dashboard expects.

    One query rather than one per candidate -- the reminders page annotates a
    few hundred rows at a time.
    """
    try:
        rows = list(_collection().find())
    except ReminderLogUnavailable:
        raise
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc

    state = {}
    for row in rows:
        record = dict(row)
        key = record.pop("_id")
        for field in ("first_reminder_at", "last_reminder_at"):
            stamp = _aware(record.get(field))
            record[field] = stamp.isoformat() if stamp else None
        state[key] = record
    return state


def reminders_sent(record: Optional[dict]) -> int:
    """
    How many reminders a record says have gone out.

    `.get`, not `[...]`. A legacy record migrated from the JSON file may be
    missing fields that were added later, and a KeyError here aborts the whole
    run for every other candidate in it.
    """
    if not record:
        return 0
    try:
        return int(record.get("reminders_sent") or 0)
    except (TypeError, ValueError):
        log.warning("Record %r has a non-numeric reminders_sent (%r). Treating "
                    "it as already at the limit.",
                    record.get("_id"), record.get("reminders_sent"))
        return 10 ** 6


def last_reminder_at(record: Optional[dict]) -> Optional[datetime]:
    """
    When the most recent reminder went out, as far as the record can say.

    THREE FALLBACKS, AND THE LAST ONE IS None. Records written before
    `last_reminder_at` existed do not have it, and reading it with `[...]`
    raised KeyError and killed the run -- one malformed row stopping every
    other candidate's reminder. `first_reminder_at` is the right stand-in when
    it is there, because a record with one reminder has the two set equal
    anyway. When neither is present the answer is genuinely unknown, and the
    caller treats unknown as "do not send" rather than guessing.
    """
    if not record:
        return None
    stamp = _aware(record.get("last_reminder_at"))
    if stamp is not None:
        return stamp
    stamp = _aware(record.get("first_reminder_at"))
    if stamp is not None:
        log.warning("Record %r has no last_reminder_at; falling back to "
                    "first_reminder_at.", record.get("_id"))
    return stamp


def is_suppressed(record: Optional[dict]) -> bool:
    """
    Has this record been deliberately marked never-again?

    An EXPLICIT flag, which the old file did not have. After the 2026-08-10
    incident, 192 records were suppressed by hand-editing `reminders_sent` up
    to the maximum -- the only lever available. That worked, and it was
    invisible: the counter looked like ordinary data, so anything that
    recomputed or normalised counts would have silently un-suppressed all 192.
    The migration sets this flag on every record carrying a `blocked_reason`,
    so the decision no longer rides on a number that looks like a bug.
    """
    return bool(record and record.get("suppressed"))


def claim(
    key: str,
    email: str,
    assessment_group: str,
    candidate_id: str,
    candidate_name: str,
    job_shortcode: str,
    max_reminders: int,
    gap_satisfied,
) -> Optional[int]:
    """
    Atomically claim the right to send one reminder. Returns the new count,
    or None if this candidate must not be mailed.

    `gap_satisfied` is a callable taking the record's last-reminder datetime
    and returning whether enough business days have passed -- the caller owns
    that arithmetic, because it is the only part Mongo cannot express.

    Raises ReminderLogUnavailable rather than returning None on a database
    error, so a caller cannot mistake "the log is down" for "already reminded".
    """
    collection = _collection()
    stamp = datetime.now(timezone.utc)

    try:
        record = collection.find_one({"_id": key})
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc

    # --- first reminder for this candidate on this assignment ---------------
    if record is None:
        try:
            result = collection.update_one(
                {"_id": key},
                {"$setOnInsert": {
                    "email": (email or "").strip().lower(),
                    "assessment_group": str(assessment_group),
                    "candidate_id": candidate_id,
                    "candidate_name": candidate_name,
                    "job_shortcode": job_shortcode,
                    "reminders_sent": 1,
                    "first_reminder_at": stamp,
                    "last_reminder_at": stamp,
                    "suppressed": False,
                }},
                upsert=True,
            )
        except Exception as exc:
            raise ReminderLogUnavailable(str(exc)) from exc

        if result.upserted_id is None:
            # Somebody else inserted it between the read and the write. They
            # own the send; we do not retry, because their claim is exactly the
            # reminder we were about to duplicate.
            log.info("Lost the race to claim %s -- another run has it.", key)
            return None
        return 1

    # --- an existing record -------------------------------------------------
    if is_suppressed(record):
        return None

    already = reminders_sent(record)
    if already >= max_reminders:
        return None

    previous = last_reminder_at(record)
    if previous is None:
        # A record that says it has been reminded but cannot say when. Refusing
        # is the safe direction and it is LOUD, because the alternative -- a
        # KeyError -- used to abort the entire run.
        log.warning(
            "Record %r has %d reminder(s) but no usable timestamp. Skipping "
            "this candidate rather than risking a duplicate; fix or clear the "
            "record to let them be reminded again.", key, already)
        return None

    if not gap_satisfied(previous):
        return None

    # THE COMPARE-AND-SWAP. Both fields are part of the filter, so the update
    # applies only if the document is still exactly what the decision above was
    # made from. A concurrent claim moves last_reminder_at, this matches
    # nothing, and modified_count is 0.
    try:
        result = collection.update_one(
            {
                "_id": key,
                "reminders_sent": record.get("reminders_sent"),
                "last_reminder_at": record.get("last_reminder_at"),
                "suppressed": {"$ne": True},
            },
            {
                "$inc": {"reminders_sent": 1},
                "$set": {"last_reminder_at": stamp,
                         "candidate_name": candidate_name or
                         record.get("candidate_name"),
                         "job_shortcode": job_shortcode or
                         record.get("job_shortcode")},
            },
        )
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc

    if result.modified_count != 1:
        log.info("Lost the race to claim %s -- another run moved it first.", key)
        return None
    return already + 1


def release(key: str, expected_count: int) -> bool:
    """
    Give a claim back after a send that never happened.

    Only if the count is still what the claim left it at, so a release cannot
    undo somebody else's later claim. Used when the mail transport refuses
    outright -- the candidate has had nothing, so charging them a reminder
    would silently shorten the number they actually receive.

    A record at 1 is left in place rather than deleted: the row is also the
    evidence that this candidate was considered.
    """
    try:
        result = _collection().update_one(
            {"_id": key, "reminders_sent": expected_count},
            {"$inc": {"reminders_sent": -1}},
        )
    except Exception as exc:
        log.warning("Could not release the claim on %s: %s", key, exc)
        return False
    return result.modified_count == 1


def suppress(key: str, reason: str) -> bool:
    """Mark a record never-again, explicitly. Returns True if it changed."""
    try:
        result = _collection().update_one(
            {"_id": key},
            {"$set": {"suppressed": True, "blocked_reason": reason}},
        )
    except Exception as exc:
        raise ReminderLogUnavailable(str(exc)) from exc
    return result.modified_count == 1
