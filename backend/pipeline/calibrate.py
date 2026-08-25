#!/usr/bin/env python3
"""
Is the grader using the scale, or just detecting missing sections?

    python calibrate.py                  every graded role
    python calibrate.py --job 33         one role
    python calibrate.py --job 33 --rows  every criterion mark, worst first

A grid is only worth its anchors if the marks spread across them. On
2026-08-12 this run reported 94.5 percent 5s, 5.5 percent 1s and not one 2, 3
or 4 across 91 marks -- a five-point rubric collapsed to present-or-absent,
which is how eight candidates in one role scored exactly 100. Nothing in the
grading path can notice that from inside a single verdict: every one of those
evaluations was individually well-formed, complete and internally consistent.
It is only visible across a batch, which is what this reads.

Three numbers matter.

  MARK SPREAD    2s, 3s and 4s should be the bulk of it. A run that is all 5s
                 and 1s is not grading, and the score it produces is really
                 just a count of the sections the candidate remembered to
                 include.
  UNGROUNDED     the share of marks whose quote is not in the submission. The
                 model is supposed to copy 25 words out of the answer; when it
                 hands back the 5 anchor reworded instead, it never read the
                 thing it is marking. Anything above a few percent invalidates
                 the run.
  PERFECT        candidates on exactly 100. Real assessment cohorts do not
                 produce these in bulk.

Read-only. Touches nothing.
"""

import argparse
import collections
import sys

from backend.database import mongo_store as store

BAR_WIDTH = 44


def _bar(count: int, total: int) -> str:
    return "#" * int(round(BAR_WIDTH * count / total)) if total else ""


def _pct(count: int, total: int) -> str:
    return f"{100 * count / total:5.1f}%" if total else "    --"


def _load(job_id):
    query = {"evaluation": {"$exists": True}}
    if job_id is not None:
        query["job_id"] = job_id
    return list(store.get_db().submissions.find(
        query,
        {"evaluation": 1, "job_id": 1, "candidate_name": 1, "candidate_email": 1},
    ))


def _report(docs) -> int:
    marks = collections.Counter()
    grounding = collections.Counter()
    scores, rows, legacy = [], [], []

    for doc in docs:
        ev = doc.get("evaluation") or {}
        grid = ev.get("grid")
        if not grid:
            # Pre-pack evaluations, scored on a different rubric entirely.
            legacy.append(doc)
            continue
        scores.append((ev.get("score"), doc))
        for row in grid:
            marks[row.get("score")] += 1
            state = row.get("grounded")
            grounding["missing" if state is None else
                       "verified" if state else "ungrounded"] += 1
            rows.append((row, doc))

    total_marks = sum(marks.values())
    if not total_marks:
        print("No evaluations carrying a scored grid. Nothing to calibrate.")
        if legacy:
            print(f"({len(legacy)} legacy evaluation(s) found, from before the "
                  f"rubric pack. They store a `matrix`, not a `grid`, and are "
                  f"not comparable to anything current.)")
        return 1

    print(f"\n{len(scores)} evaluation(s), {total_marks} criterion marks\n")

    print("MARK SPREAD")
    for level in (5, 4, 3, 2, 1, None):
        count = marks.get(level, 0)
        label = "unrated" if level is None else str(level)
        print(f"  {label:>7} {count:>5}  {_pct(count, total_marks)}  "
              f"{_bar(count, total_marks)}")
    middle = sum(marks.get(level, 0) for level in (2, 3, 4))
    print(f"\n  2/3/4 together: {_pct(middle, total_marks).strip()} of all marks", end="")
    if middle == 0:
        print("  <-- BROKEN. The scale is being used as a binary.")
    elif middle < 0.25 * total_marks:
        print("  <-- thin. Most work is ordinary and should mark as such.")
    else:
        print()

    checked = grounding["verified"] + grounding["ungrounded"]
    print("\nQUOTE GROUNDING")
    if not checked:
        print("  No checkable quotes. These verdicts predate the quote rule; "
              "re-grade to measure.")
    else:
        print(f"  verified   {grounding['verified']:>5}  "
              f"{_pct(grounding['verified'], checked)}  quote found in the submission")
        print(f"  ungrounded {grounding['ungrounded']:>5}  "
              f"{_pct(grounding['ungrounded'], checked)}  quote NOT in the submission", end="")
        print("  <-- the model is not reading the answer."
              if grounding["ungrounded"] > 0.05 * checked else "")
        if grounding["missing"]:
            print(f"  unquoted   {grounding['missing']:>5}  "
                  f"(empty or too short to verify)")

    perfect = [d for s, d in scores if s == 100]
    print("\nSCORES")
    buckets = collections.Counter()
    for value, _ in scores:
        buckets["unscored" if value is None else
                "exactly 100" if value == 100 else
                "90-99" if value >= 90 else
                "75-89 advance" if value >= 75 else
                "60-74 hold" if value >= 60 else "<60 reject"] += 1
    for key in ("exactly 100", "90-99", "75-89 advance", "60-74 hold",
                "<60 reject", "unscored"):
        if buckets.get(key):
            print(f"  {key:>14} {buckets[key]:>5}")
    if perfect:
        print(f"\n  {len(perfect)} candidate(s) on exactly 100 "
              f"({_pct(len(perfect), len(scores)).strip()}):")
        for doc in perfect[:12]:
            print(f"      {doc.get('candidate_name') or doc.get('candidate_email')}")
        if len(perfect) > 12:
            print(f"      ... and {len(perfect) - 12} more")

    if legacy:
        print(f"\nLEGACY: {len(legacy)} evaluation(s) with no scored grid, from "
              f"before the rubric pack.")
        by_job = collections.Counter(d.get("job_id") for d in legacy)
        print("  jobs: " + ", ".join(f"{job} ({n})" for job, n in sorted(by_job.items())))
        print("  These were marked on a different rubric and their scores do "
              "not mean the same thing.\n  Re-grade them or filter them out of "
              "the dashboard.")

    healthy = middle >= 0.25 * total_marks and grounding["ungrounded"] <= 0.05 * checked
    return 0 if healthy else 2


def _rows(docs) -> None:
    print("\nCRITERION MARKS -- ungrounded first, then by score\n")
    entries = []
    for doc in docs:
        for row in (doc.get("evaluation") or {}).get("grid") or []:
            entries.append((row.get("grounded") is False, -(row.get("score") or 0),
                            row, doc))
    entries.sort(key=lambda e: (not e[0], e[1]))
    for _, _, row, doc in entries:
        flag = {True: "ok ", False: "BAD", None: "-- "}[row.get("grounded")]
        name = (doc.get("candidate_name") or doc.get("candidate_email") or "?")[:22]
        print(f"  {flag} {row.get('score')}  {name:<22} {row.get('key','')[:20]:<20} "
              f"{(row.get('quote') or row.get('evidence') or '')[:64]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=int, help="portal job id")
    parser.add_argument("--rows", action="store_true",
                        help="print every criterion mark")
    args = parser.parse_args()

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        print(exc)
        return 1

    docs = _load(args.job)
    if not docs:
        print("No evaluations found." if args.job is None
              else f"No evaluations for job {args.job}.")
        return 1

    code = _report(docs)
    if args.rows:
        _rows(docs)
    return code


if __name__ == "__main__":
    sys.exit(main())
