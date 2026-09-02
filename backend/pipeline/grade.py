#!/usr/bin/env python3
"""
Run AI evaluation over pending submissions.

    python manage.py grade --job 23                  grade every pending AI Trainer
    python manage.py grade --job 31 --limit 10       ten of them, to sanity-check first
    python manage.py grade --all --limit 50          across all roles
    python manage.py grade --job 4 --rubric-only     write the grid, grade nothing
    python manage.py grade --job 4 --force-rubric    regenerate the grid first

Candidates are marked against the Ajaia rubric pack: the family grid for their
assessment, 100 points across four blocks, banded Best 85 / Better 75 / Good 60
/ Okay below, with the advance bar at 75.
Fourteen portal assessments have a hand-authored grid in rubric_pack/ and
never need --rubric-only; the rest derive one from their assessment text on
first use, which is what the two rubric flags are for.

Only submissions in the `pending` bucket are graded -- anything auto-rejected
for a missing artefact, still in progress, or already scored is skipped.
Re-running picks up where the last run stopped, so a rate-limited run can just
be started again.
"""

import argparse
import logging
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Optional

from backend.config import LLM_CONCURRENCY, LLM_MODEL
from backend.grading import evaluator, grader
from backend.db import store
from backend.grading import tier_resolver
from backend.logging_setup import setup_logging

log = logging.getLogger("grade")


def _resolve_tiers(role: dict) -> None:
    """Fill in which posting each candidate applied to, where that decides the grid."""
    result = tier_resolver.ensure_resolved(role, store)
    if result and (result["written"] or result["unresolved"]):
        log.info(
            "[%s] tiers: %d resolved, %d unresolved, %d applied to both "
            "postings.", role.get("title", role["_id"]),
            result["written"], result["unresolved"], result["both"],
        )


def _grade_role(role: dict, limit: int, force_rubric: bool,
                rubric_only: bool) -> dict:
    title = role.get("title", role["_id"])
    pending = store.ungraded(job_id=role["_id"], limit=limit)

    if rubric_only:
        grid = evaluator.derive_grid(role, force=force_rubric)
        log.info("[%s] %s grid ready (%s).", title, grid.get("unit"),
                 grid.get("source"))
        return {"graded": 0, "failed": 0, "pending": len(pending)}

    if not pending:
        log.info("[%s] nothing pending.", title)
        return {"graded": 0, "failed": 0, "pending": 0}

    # Resolved once per role, before the fan-out, so concurrent workers cannot
    # each trigger their own derivation and score against different anchors.
    grid = evaluator.derive_grid(role, force=force_rubric)

    # A role whose postings are graded at different tiers needs to know which
    # posting each candidate came from before it can pick their anchors, and
    # only Workable can answer that. Best effort on purpose: an unresolved
    # candidate falls back to the default grid, which is the senior one, so a
    # Workable outage delays a correction rather than stopping the run.
    _resolve_tiers(role)

    log.info("[%s] grading %d submission(s) against the %s grid with %s...",
             title, len(pending), grid.get("unit"), LLM_MODEL)

    graded = failed = ungrounded = 0
    marks: list[int] = []
    exhausted: Optional[evaluator.QuotaExhausted] = None

    def one(sub):
        return sub, grader.grade_and_store(sub, role, grid)

    with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
        futures = [pool.submit(one, sub) for sub in pending]
        for future in as_completed(futures):
            try:
                sub, verdict = future.result()
            except evaluator.QuotaExhausted as exc:
                # The day's tokens are gone. Every remaining candidate would
                # fail identically, so cancel what has not started rather than
                # filling the log with thirty copies of the same message.
                exhausted = exc
                for pending_future in futures:
                    pending_future.cancel()
                break
            except CancelledError:
                continue
            except evaluator.EvaluationFailed as exc:
                failed += 1
                log.warning("[%s] evaluation failed: %s", title, exc)
                continue
            graded += 1
            triage = verdict.get("triage") or {}
            grounding = verdict.get("grounding") or {}
            ungrounded += grounding.get("ungrounded", 0)
            marks += [r["score"] for r in verdict.get("grid") or []
                      if r.get("score") is not None]
            log.info("  %-32s %5.1f  %-10s  triage %s/%s  quotes %s/%s  %s",
                     (sub.get("candidate_name") or sub.get("candidate_email") or "?")[:32],
                     verdict["score"], verdict["recommendation"],
                     triage.get("passed", "?"), triage.get("of", "?"),
                     grounding.get("verified", "?"), grounding.get("checked", "?"),
                     verdict["brief"][:60])
            for row in verdict.get("grid") or []:
                if row.get("grounded") is False:
                    log.warning("      unevidenced %s (%s): quote is not in the "
                                "submission -- %.60s",
                                row["key"], row["score"], row.get("quote"))
            for finding in verdict.get("auto_fails") or []:
                log.info("      auto-fail: %s -- %s",
                         finding.get("rule"), finding.get("evidence"))
            for finding in verdict.get("fraud_tells") or []:
                log.warning("      FRAUD LOG: %s -- %s",
                            finding.get("tell"), finding.get("evidence"))

    # Two ways a run can be individually well-formed and collectively useless,
    # neither visible from inside a single verdict. Say so at the end of the
    # role rather than leaving it for someone to notice on the dashboard.
    if marks:
        middle = sum(1 for m in marks if m in (2, 3, 4))
        if not middle:
            log.warning("[%s] every one of the %d marks was a 5 or a 1. The "
                        "scale is being used as a binary -- the score is a "
                        "count of sections present, not a grade. Run "
                        "`python manage.py calibrate --job %s`.",
                        title, len(marks), role["_id"])
        elif middle < len(marks) // 4:
            log.warning("[%s] only %d of %d marks used 2, 3 or 4. Thin spread.",
                        title, middle, len(marks))
    if ungrounded:
        log.warning("[%s] %d criterion mark(s) quoted text that is not in the "
                    "submission. Those marks are unevidenced.", title, ungrounded)

    if exhausted:
        left = len(pending) - graded - failed
        minutes = (exhausted.retry_after or 0) / 60
        log.warning(
            "[%s] daily token budget reached after %d graded. %d candidate(s) "
            "still pending. %s Re-run the same command in about %.0f min and "
            "it will pick up where this stopped.",
            title, graded, left, exhausted, minutes,
        )

    return {"graded": graded, "failed": failed, "pending": len(pending),
            "exhausted": exhausted is not None,
            "remaining": len(pending) - graded - failed if exhausted else 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--job", type=int, help="portal job id (e.g. 23)")
    target.add_argument("--all", action="store_true", help="every role")
    parser.add_argument("--limit", type=int, default=0,
                        help="max submissions per role (0 = no cap)")
    parser.add_argument("--rubric-only", action="store_true",
                        help="write rubrics without grading anything")
    parser.add_argument("--force-rubric", action="store_true",
                        help="regenerate the rubric, discarding hand edits")
    args = parser.parse_args()

    setup_logging()

    if not evaluator.is_configured():
        log.error(
            "No LLM credentials. Set LLM_API_KEY in .env (and LLM_BASE_URL / "
            "LLM_MODEL if you are not using the Groq default). Ingest and the "
            "dashboard work without it; only grading is blocked."
        )
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        log.error("%s", exc)
        return 1

    if args.all:
        roles = [r for r in store.get_roles() if r.get("published")]
    else:
        role = store.get_role(args.job)
        if role is None:
            log.error("No role with job id %s. Run `python manage.py ingest --roles-only`.",
                      args.job)
            return 1
        roles = [role]

    totals = {"graded": 0, "failed": 0, "remaining": 0}
    exhausted = False
    for role in roles:
        try:
            result = _grade_role(role, args.limit, args.force_rubric, args.rubric_only)
        except evaluator.QuotaExhausted as exc:
            # Raised before this role graded anything -- the budget went on an
            # earlier one, or on a grid derivation.
            log.warning("[%s] %s", role.get("title"), exc)
            exhausted = True
            break
        except (evaluator.EvaluationFailed, evaluator.EvaluatorNotConfigured) as exc:
            log.error("[%s] %s", role.get("title"), exc)
            continue
        totals["graded"] += result["graded"]
        totals["failed"] += result["failed"]
        totals["remaining"] += result.get("remaining", 0)
        if result.get("exhausted"):
            exhausted = True
            break

    print(f"\nGraded {totals['graded']} submission(s), {totals['failed']} failed.")
    if exhausted:
        # A daily cap is not a failure to fix, it is a queue to come back to,
        # so it gets its own exit code: a cron wrapper can retry on 3 and
        # escalate on 2 without parsing the log.
        still_pending = totals["remaining"]
        tail = f", {still_pending} still pending" if still_pending else ""
        print(f"Stopped on the provider's daily token budget{tail}. Re-run the "
              f"same command once it resets; already-scored candidates are "
              f"skipped.")
        return 3
    return 0 if totals["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
