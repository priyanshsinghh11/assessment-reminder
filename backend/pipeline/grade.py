#!/usr/bin/env python3
"""
Run AI evaluation over pending submissions.

    python manage.py grade --job 23                  grade every pending AI Trainer
    python manage.py grade --job 31 --limit 10       ten of them, to sanity-check first
    python manage.py grade --all --limit 50          across all roles
    python manage.py grade --next --limit 50         one role, whoever's turn it is
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

--next grades ONE role, the one with work waiting that was graded least
recently, and is what the hourly workflow runs. It takes the roles in turn
without storing a cursor: grading a role is what moves it to the back of the
queue. A role nobody has graded yet goes first, and a role with an empty queue
is skipped rather than spending the hour. See _next_role.
"""

import argparse
import logging
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Optional

from backend.config import (LLM_CONCURRENCY, LLM_MODEL,
                            LOG_CANDIDATE_DETAIL)
from backend.grading import evaluator, grader
from backend.db import store
from backend.grading import tier_resolver
from backend.logging_setup import setup_logging

log = logging.getLogger("grade")

# How many roles `--next` may step through in one run before giving up. Only
# reached when a role raises before grading anything -- see _rotation.
ROTATION_ATTEMPTS = 3


def _resolve_tiers(role: dict) -> None:
    """Fill in which posting each candidate applied to, where that decides the grid."""
    result = tier_resolver.ensure_resolved(role, store)
    if result and (result["written"] or result["unresolved"]):
        log.info(
            "[%s] tiers: %d resolved, %d unresolved, %d applied to both "
            "postings.", role.get("title", role["_id"]),
            result["written"], result["unresolved"], result["both"],
        )


def _next_role(roles: list[dict], pending: dict[int, int],
               last_graded: dict) -> Optional[dict]:
    """
    Of the roles with work waiting, the one graded least recently.

    This is the whole rotation. An hourly run grades one role, which makes that
    role the most recently graded, which sends it to the back of the queue --
    so the roles come round in turn without anybody storing a cursor. Two
    properties that matter more than the ordering:

      * A role with nothing waiting is never chosen, so an hour is never spent
        on an empty queue while another role has a backlog.
      * A role nobody has ever graded sorts FIRST. A newly published role does
        not wait for a full cycle before it is looked at.

    The sort key is (0, None) for never-graded and (1, when) otherwise, which
    is deliberate: it never compares None to a datetime, and never compares
    datetimes that came from different places. The job id breaks ties so that
    two roles in the same state cannot swap places between runs.

    Pure on purpose -- the queries live in store, so the rule that decides
    where an hour of LLM budget goes can be tested without a database.
    """
    order = _rotation(roles, pending, last_graded)
    return order[0] if order else None


def _rotation(roles: list[dict], pending: dict[int, int],
              last_graded: dict) -> list[dict]:
    """
    Every role with work waiting, in the order the rotation should take them.

    The caller needs more than the head of this list because of one failure
    mode: a role whose grid will not derive raises before it grades anything,
    which means its `graded_at` does not move, which means it is still the
    least recently graded role an hour later. Handed only the head, an hourly
    run would pick that same broken role every hour forever and never reach
    the other thirty -- while reporting success, because nothing failed that
    it counted. main() walks a few of these instead, so one bad role costs a
    log line rather than the whole queue.
    """
    waiting = [r for r in roles if pending.get(r["_id"], 0) > 0]

    def key(role: dict):
        when = last_graded.get(role["_id"])
        return ((0, None) if when is None else (1, when), role["_id"])

    return sorted(waiting, key=key)


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
    auto_fails = fraud_tells = 0
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
            auto_fails += len(verdict.get("auto_fails") or [])
            fraud_tells += len(verdict.get("fraud_tells") or [])

            # Everything below names the candidate or quotes their work, so
            # none of it is written when the log is going somewhere public.
            # The counts it would have produced are kept above and reported in
            # aggregate at the end of the role -- a scheduled run still says a
            # fraud tell fired, it just does not say who or quote what. See
            # LOG_CANDIDATE_DETAIL in backend/config.py.
            if not LOG_CANDIDATE_DETAIL:
                continue

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
    # Only worth saying when the per-candidate lines were suppressed; with
    # detail on, every one of these was already printed beside the name it
    # belongs to, and repeating the tally reads like a second set of findings.
    if not LOG_CANDIDATE_DETAIL and (auto_fails or fraud_tells):
        log.warning("[%s] %d auto-fail(s) and %d fraud tell(s) fired. Names "
                    "and evidence are suppressed here -- open the role in the "
                    "dashboard, or re-run locally, to see which candidates.",
                    title, auto_fails, fraud_tells)

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
    target.add_argument("--next", action="store_true",
                        help="one role: whichever has work waiting and was "
                             "graded least recently")
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
    elif args.next:
        # One role per run, taken in turn. See _rotation: the order is the
        # grading timestamps themselves, so nothing has to be remembered
        # between runs, and a run that finds every queue empty is a success
        # that says so rather than a failure.
        #
        # The list is trimmed rather than taken whole: the loop below stops at
        # the first role that grades, so the extras are only reached when a
        # role raises before grading anything. Three is enough to step over a
        # broken role without spending the hour discovering that every role is
        # broken -- if it is, the exit code says so and a human should look.
        # role_index() rather than get_roles(): choosing a role needs an id, a
        # title and whether it is published, while get_roles() carries every
        # role's live assessment markdown with it. Measured against Atlas on a
        # warm connection, 37 roles: 0.07s for the index against 0.33s and
        # ~0.4MB for the full documents. Small either way -- the point is that
        # it is text this run will not read, fetched once an hour forever, and
        # it grows with the assessments rather than with the work.
        published = [r for r in store.role_index() if r.get("published")]
        waiting = store.ungraded_counts_by_role()
        order = _rotation(published, waiting, store.last_graded_by_role())
        if not order:
            print("Nothing pending in any published role.")
            return 0
        log.info("Next in rotation: [%s] (job %s), %d waiting.",
                 order[0].get("title"), order[0]["_id"],
                 waiting.get(order[0]["_id"], 0))
        # The full document IS needed to grade -- derive_grid reads the
        # assessment text -- so the chosen few are fetched whole. Normally that
        # is one role; the rest are only reached if it turns out to be broken.
        # A role that vanished between the index and here is dropped rather
        # than crashing the hour.
        roles = [full for full in
                 (store.get_role(r["_id"]) for r in order[:ROTATION_ATTEMPTS])
                 if full is not None]
        if not roles:
            log.error("The roles picked for this run no longer exist.")
            return 1
    else:
        role = store.get_role(args.job)
        if role is None:
            log.error("No role with job id %s. Run `python manage.py ingest --roles-only`.",
                      args.job)
            return 1
        roles = [role]

    totals = {"graded": 0, "failed": 0, "remaining": 0}
    exhausted = False
    broken_roles = 0
    for role in roles:
        try:
            result = _grade_role(role, args.limit, args.force_rubric, args.rubric_only)
        except evaluator.QuotaExhausted as exc:
            # Raised before this role graded anything -- the budget went on an
            # earlier one, or on a grid derivation. Not this role's fault, so
            # it is not counted against it, and the next run retries it.
            log.warning("[%s] %s", role.get("title"), exc)
            exhausted = True
            break
        except (evaluator.EvaluationFailed, evaluator.EvaluatorNotConfigured) as exc:
            # This role cannot be graded at all -- usually a grid that will not
            # derive from its assessment text. Counted, because a run that
            # reports success having graded nobody is how a broken role stays
            # broken. Under --next the loop moves on to the next role in the
            # rotation rather than ending the run here.
            log.error("[%s] %s", role.get("title"), exc)
            broken_roles += 1
            continue
        totals["graded"] += result["graded"]
        totals["failed"] += result["failed"]
        totals["remaining"] += result.get("remaining", 0)
        if result.get("exhausted"):
            exhausted = True
            break
        if args.next:
            # One role per run. Anything that raised above never reached this
            # line, which is what lets a broken role be stepped over instead
            # of wedging every later hour on the same role.
            break

    unusable = f", {broken_roles} role(s) could not be graded at all" if broken_roles else ""
    print(f"\nGraded {totals['graded']} submission(s), "
          f"{totals['failed']} failed{unusable}.")
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
    # `broken_roles` counts here too. Exit 2 means "the run stands but
    # something needs a human", and a role whose grid will not derive is
    # exactly that -- left out of this it would be invisible on a run page,
    # and under --next it would be invisible every hour.
    return 0 if not totals["failed"] and not broken_roles else 2


if __name__ == "__main__":
    sys.exit(main())
