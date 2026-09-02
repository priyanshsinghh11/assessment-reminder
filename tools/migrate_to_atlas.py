#!/usr/bin/env python3
"""
Copy the local MongoDB into Atlas, so a deployment has something to serve.

    python tools/migrate_to_atlas.py "mongodb+srv://user:pass@cluster.mongodb.net/"
    python tools/migrate_to_atlas.py "<uri>" --dry-run     # count both sides, write nothing

WHY THIS IS NOT OPTIONAL. A fresh Atlas cluster is empty, and pointing the
deployment at an empty database does not fail loudly -- it comes up healthy and
serves nothing. Two collections are the reason:

    review_links   the tokens already emailed to hiring managers. Not copied,
                   every one of those links is a dead end.
    settings       holds app_secret, which SIGNS UNSUBSCRIBE LINKS. A fresh
                   database mints a new one, and every "click here to stop" in
                   every message already sent becomes a 404. That is the one
                   thing in here that cannot be repaired afterwards -- the old
                   links are in inboxes we do not control.

IDEMPOTENT, BY _id. Every collection here has a MEANINGFUL primary key, not a
generated one: a submission's _id is the portal's submission id, a review
link's _id is the token in the URL, a rejection's _id is the candidate's email
address, and settings' _id is the name of the setting. So this replaces by _id
rather than inserting, and running it twice is the same as running it once --
which matters, because the honest way to use this is to run it, look at the
counts, fix something, and run it again.

WHAT IT WILL NOT DO
  * It never deletes. A document in Atlas that is not in the local database is
    left alone -- this is a copy, not a mirror, and a --sync flag that could
    empty a production collection is not worth the convenience.
  * It refuses to run source-to-source, which is the paste-the-wrong-URI
    accident and would otherwise look like a successful no-op.
  * SESSIONS AND LOGIN ATTEMPTS ARE SKIPPED. Both are ephemeral: a session is a
    live credential whose only effect is keeping one browser signed in, and
    login_attempts is throttle state that means nothing an hour later. Copying
    live session tokens into a second database doubles the places one can be
    stolen from, for the benefit of not signing in again.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pymongo import MongoClient, ReplaceOne          # noqa: E402
from pymongo.errors import BulkWriteError, PyMongoError  # noqa: E402

from backend.config import MONGO_DB, MONGO_URI  # noqa: E402

# Ephemeral. See the module docstring.
SKIP = ("sessions", "login_attempts")

# How many documents go in one round trip. Submissions carry answer text and
# CV text, so a few thousand at once is a large payload on a free-tier cluster;
# 500 keeps each write comfortably under Atlas's 16MB command limit.
BATCH = 500


def copy(source, target, name: str, dry_run: bool) -> tuple[int, int]:
    """Copy one collection. Returns (documents seen, documents written)."""
    total = source[name].count_documents({})
    if dry_run or total == 0:
        return total, 0

    written = 0
    batch: list[ReplaceOne] = []
    for doc in source[name].find({}):
        batch.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if len(batch) >= BATCH:
            written += _flush(target, name, batch)
            batch = []
            print(f"    {name}: {written:,}/{total:,}", end="\r", flush=True)
    if batch:
        written += _flush(target, name, batch)
    return total, written


def _flush(target, name: str, batch: list) -> int:
    """
    One bulk write. Unordered, so a single bad document does not abandon the
    other 499 behind it -- and the ones that failed are reported rather than
    counted as written.
    """
    try:
        result = target[name].bulk_write(batch, ordered=False)
    except BulkWriteError as exc:
        errors = exc.details.get("writeErrors", [])
        print(f"\n    {name}: {len(errors)} document(s) refused; first: "
              f"{errors[0].get('errmsg', '')[:120]}" if errors else "")
        result = exc.details
        return (result.get("nUpserted", 0) + result.get("nModified", 0)
                + result.get("nMatched", 0))
    return result.upserted_count + result.matched_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy the local MongoDB into Atlas.")
    parser.add_argument("target", help="Atlas connection string (mongodb+srv://...)")
    parser.add_argument("--db", default=MONGO_DB,
                        help=f"database name on BOTH sides (default: {MONGO_DB})")
    parser.add_argument("--dry-run", action="store_true",
                        help="count both sides and write nothing")
    args = parser.parse_args()

    print(f"  from  {MONGO_URI}  ({args.db})")
    # The password is in the target URI, so only the host half is ever printed.
    shown = args.target.split("@")[-1] if "@" in args.target else args.target
    print(f"  to    ...@{shown}  ({args.db})")
    print()

    if args.target.strip() == MONGO_URI.strip():
        print("  Source and target are the same database. Nothing to do.")
        return 1

    try:
        source = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[args.db]
        source.command("ping")
    except PyMongoError as exc:
        print(f"  Cannot reach the LOCAL database: {exc}")
        print("  Is mongod running?")
        return 1

    try:
        client = MongoClient(args.target, serverSelectionTimeoutMS=15000)
        client.admin.command("ping")
        target = client[args.db]
    except PyMongoError as exc:
        print(f"  Cannot reach ATLAS: {exc}\n")
        print("  Usually one of three things:")
        print("    * Network Access does not allow 0.0.0.0/0")
        print("    * the password is wrong, or has an unencoded @ : / ? # in it")
        print("      (percent-encode those: @ is %40)")
        print("    * the string still has <db_password> in it")
        return 1

    names = [n for n in sorted(source.list_collection_names()) if n not in SKIP]
    skipped = [n for n in sorted(source.list_collection_names()) if n in SKIP]

    print(f"  {'collection':<22}{'local':>9}{'atlas before':>14}{'copied':>9}{'atlas after':>13}")
    print(f"  {'-' * 66}")

    seen_total = written_total = 0
    for name in names:
        before = target[name].count_documents({})
        total, written = copy(source, target, name, args.dry_run)
        after = target[name].count_documents({})
        seen_total += total
        written_total += written
        flag = "" if args.dry_run or after >= total else "   <-- SHORT"
        print(f"  {name:<22}{total:>9,}{before:>14,}{written:>9,}{after:>13,}{flag}")

    print(f"  {'-' * 66}")
    print(f"  {'':<22}{seen_total:>9,}{'':>14}{written_total:>9,}")
    if skipped:
        print(f"\n  Skipped (ephemeral): {', '.join(skipped)}")

    if args.dry_run:
        print("\n  Dry run — nothing was written.")
        return 0

    # The indexes the dashboard's queries rely on. Created against the TARGET by
    # pointing the store's cached client at it -- ensure_indexes() reads
    # get_db(), and get_db() caches one client per process.
    print("\n  Creating indexes on Atlas...")
    try:
        from backend import auth
        from backend.db import store
        store._client = client
        store.MONGO_DB = args.db
        store.ensure_indexes()
        auth.ensure_indexes()
        print("  Indexes created.")
    except Exception as exc:
        print(f"  Could not create indexes ({exc}).")
        print("  Not fatal — the app creates them at startup. Queries are "
              "slower until then.")

    print("\n  Done. Check the two count columns match before switching over.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
