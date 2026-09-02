#!/usr/bin/env python3
"""
Move state/reminder_log.json into MongoDB.

    python manage.py migrate-reminder-log --dry-run    report, change nothing
    python manage.py migrate-reminder-log              migrate
    python manage.py migrate-reminder-log --verify     compare the two afterwards

WHAT IS AT STAKE. This file is the only thing standing between a candidate and
a second copy of the same email. Every record has to arrive, and two fields in
particular have to arrive UNCHANGED.

    reminders_sent   COPIED VERBATIM. NEVER RECOMPUTED.

        192 records in the live file sit at 2 with their two timestamps still
        equal to each other. That is not two sends -- a real second send stamps
        a fresh last_reminder_at. Somebody hand-edited the counter up to
        MAX_REMINDERS_PER_CANDIDATE after the 2026-08-10 incident, because
        pushing the count to the limit was the only lever the file format
        offered for "never mail this person again".

        Any migration clever enough to notice those look wrong -- to normalise
        them against the timestamps, or rebuild counts from send history --
        resets all 192 to 1 and re-arms the exact population the incident
        response was protecting. So this copies the number and does not think
        about it.

    blocked_reason   CARRIED ACROSS, AND PROMOTED TO A REAL FLAG.

        Nothing in the codebase reads blocked_reason; the suppression rides
        entirely on the inflated counter above. That works and it is invisible.
        Every record carrying one also gets `suppressed: true`, which
        reminder_log.claim() checks explicitly, so the block stops depending on
        a number that looks like corruption to the next person who reads it.

Idempotent. Existing records are left exactly as they are rather than
overwritten, so running it twice cannot roll a live counter backwards to
whatever the file said. The file itself is never modified -- it stays on disk
as the rollback.
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from backend.config import STATE_FILE
from backend.db import store

COLLECTION = "reminders"


def _parse(value):
    """ISO string -> aware datetime, or None. Anything unparseable stays None."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def read_file() -> dict:
    if not STATE_FILE.exists():
        print(f"No file at {STATE_FILE} -- nothing to migrate.")
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # LOUD, and fatal. The old loader answered a corrupt file with {},
        # which is what made a truncated write look like "nobody has ever been
        # reminded". A migration must never inherit that behaviour: an empty
        # migration of a corrupt file would look like a clean success and leave
        # the whole window re-armed.
        print(f"ERROR: {STATE_FILE} is not valid JSON ({exc}).")
        print("Refusing to migrate. Restore it from a backup first -- an empty "
              "migration here would re-arm every candidate in the window.")
        raise SystemExit(2)


def to_document(key: str, record: dict) -> dict:
    """One file record as a Mongo document. Nothing is derived or corrected."""
    email, _, group = key.partition("::")
    document = {
        "_id": key,
        "email": email,
        "assessment_group": group,
        "candidate_id": record.get("candidate_id"),
        "candidate_name": record.get("candidate_name"),
        "job_shortcode": record.get("job_shortcode"),
        # Verbatim. See the module docstring.
        "reminders_sent": record.get("reminders_sent", 0),
        "first_reminder_at": _parse(record.get("first_reminder_at")),
        "last_reminder_at": _parse(record.get("last_reminder_at")),
        "migrated_from_file_at": store.now(),
    }

    reason = record.get("blocked_reason")
    if reason:
        document["blocked_reason"] = reason
        document["suppressed"] = True
    else:
        document["suppressed"] = False

    # Anything this migration does not know about is carried across rather than
    # dropped. A field somebody added by hand is exactly the kind of thing that
    # matters and exactly the kind of thing a field-by-field copy loses.
    known = {"candidate_id", "candidate_name", "job_shortcode", "reminders_sent",
             "first_reminder_at", "last_reminder_at", "blocked_reason"}
    for field, value in record.items():
        if field not in known and field not in document:
            document[field] = value

    return document


def migrate(dry_run: bool) -> int:
    state = read_file()
    if not state:
        return 0

    collection = store.get_db()[COLLECTION]
    existing = {row["_id"] for row in collection.find({}, {"_id": 1})}

    to_insert = [to_document(key, record) for key, record in state.items()
                 if key not in existing]
    skipped = len(state) - len(to_insert)

    counts = {}
    for record in state.values():
        counts[record.get("reminders_sent")] = \
            counts.get(record.get("reminders_sent"), 0) + 1
    suppressed = sum(1 for r in state.values() if r.get("blocked_reason"))

    print(f"File:      {len(state)} record(s) in {STATE_FILE.name}")
    print(f"           reminders_sent spread: "
          f"{dict(sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0])))}")
    print(f"           {suppressed} carrying a blocked_reason "
          f"-> will be marked suppressed")
    print(f"Mongo:     {len(existing)} record(s) already in '{COLLECTION}'")
    print(f"To insert: {len(to_insert)}   (skipping {skipped} already present)")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    if not to_insert:
        print("\nNothing to do.")
        return 0

    # insert_many with ordered=False rather than upsert: a record that appeared
    # between the read above and here must not be overwritten by the file's
    # older idea of it. A duplicate _id is skipped, which is the right answer.
    try:
        collection.insert_many(to_insert, ordered=False)
    except Exception as exc:
        written = getattr(getattr(exc, "details", None), "get", lambda _k: None)("nInserted")
        if written is None:
            written = "some"
        print(f"\nPartially written ({written} inserted): {exc}")
        print("Re-run to finish -- already-present records are skipped.")
        return 1

    print(f"\nInserted {len(to_insert)} record(s).")
    print(f"{STATE_FILE} was NOT modified. Keep it until --verify passes and "
          "a real run has been through the new store.")
    return 0


def verify() -> int:
    """Compare the file against Mongo, field by field, and report differences."""
    state = read_file()
    if not state:
        return 0

    collection = store.get_db()[COLLECTION]
    documents = {row["_id"]: row for row in collection.find()}

    missing = [key for key in state if key not in documents]
    mismatched = []
    for key, record in state.items():
        document = documents.get(key)
        if document is None:
            continue
        if document.get("reminders_sent") != record.get("reminders_sent", 0):
            mismatched.append(
                f"{key}: file says {record.get('reminders_sent')}, "
                f"Mongo says {document.get('reminders_sent')}")
        if record.get("blocked_reason") and not document.get("suppressed"):
            mismatched.append(f"{key}: blocked in the file, NOT suppressed in Mongo")

    print(f"File:  {len(state)} record(s)")
    print(f"Mongo: {len(documents)} record(s)")
    print(f"Missing from Mongo: {len(missing)}")
    print(f"Count or suppression mismatches: {len(mismatched)}")

    for line in (missing[:10] + mismatched[:10]):
        print(f"  {line}")

    if missing or mismatched:
        print("\nFAILED. Do not delete the file.")
        return 1

    blocked_file = sum(1 for r in state.values() if r.get("blocked_reason"))
    blocked_mongo = sum(1 for d in documents.values() if d.get("suppressed"))
    print(f"\nSuppressed: {blocked_file} in the file, {blocked_mongo} in Mongo.")
    print("OK -- every record present, every count identical.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen, write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="compare the file against Mongo and report")
    args = parser.parse_args()

    try:
        store.ping()
    except Exception as exc:
        print(f"Cannot reach MongoDB: {exc}")
        return 1

    store.ensure_indexes()

    if args.verify:
        return verify()
    return migrate(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
