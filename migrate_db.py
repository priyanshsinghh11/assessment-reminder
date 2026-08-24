#!/usr/bin/env python3
"""
Copy an older database into the one config.MONGO_DB now points at.

    python migrate_db.py --from ajaia_assessments            copy
    python migrate_db.py --from ajaia_assessments --dry-run  report only

Written for the rename to `assessment-evaluation`, but it is general: it copies
`roles` and `submissions` across, preserving `_id` so re-running is idempotent.

The source is only ever read. Nothing is dropped, so the old database stays as
a rollback: point MONGO_DB back at it in .env and the dashboard returns to
exactly where it was.
"""

import argparse
import sys

from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError

from config import MONGO_DB, MONGO_URI
import mongo_store as store

COLLECTIONS = ("roles", "submissions")
BATCH = 500


def copy_collection(src, dst, name: str, dry_run: bool) -> dict:
    total = src[name].estimated_document_count()
    if not total:
        return {"read": 0, "written": 0}

    if dry_run:
        return {"read": total, "written": 0}

    read = written = 0
    ops: list[UpdateOne] = []
    for doc in src[name].find():
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))
        read += 1
        if len(ops) >= BATCH:
            result = dst[name].bulk_write(ops, ordered=False)
            written += result.upserted_count + result.modified_count
            ops = []
            print(f"  {name}: {read}/{total}", end="\r", flush=True)

    if ops:
        result = dst[name].bulk_write(ops, ordered=False)
        written += result.upserted_count + result.modified_count

    print(f"  {name}: {read}/{total}   ")
    return {"read": read, "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", required=True,
                        help="database to copy out of (e.g. ajaia_assessments)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be copied, write nothing")
    args = parser.parse_args()

    if args.source == MONGO_DB:
        print(f"Source and destination are both '{MONGO_DB}'. Nothing to do.")
        return 1

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        print(f"Cannot reach MongoDB at {MONGO_URI}: {exc}")
        return 1

    if args.source not in client.list_database_names():
        print(f"No database named '{args.source}' on {MONGO_URI}.")
        return 1

    src, dst = client[args.source], client[MONGO_DB]
    verb = "Would copy" if args.dry_run else "Copying"
    print(f"{verb} {args.source} -> {MONGO_DB}\n")

    totals = {"read": 0, "written": 0}
    for name in COLLECTIONS:
        result = copy_collection(src, dst, name, args.dry_run)
        totals["read"] += result["read"]
        totals["written"] += result["written"]

    if args.dry_run:
        print(f"\n{totals['read']} document(s) would be copied.")
        return 0

    store.ensure_indexes()
    print(f"\nCopied {totals['read']} document(s); {totals['written']} written.")
    print(f"'{args.source}' is untouched -- set MONGO_DB back to it to roll back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
