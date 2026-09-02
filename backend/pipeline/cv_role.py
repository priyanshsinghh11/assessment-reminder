#!/usr/bin/env python3
"""
Ingest and grade a posting that has no assessment.

    python manage.py cv-role                          every CV-only role: fetch, then grade
    python manage.py cv-role --job EA7059EA8E         just that posting
    python manage.py cv-role --fetch-only             pull candidates and resumes, no grading
    python manage.py cv-role --grade-only             grade what is already stored
    python manage.py cv-role --limit 5                five candidates, to sanity-check first
    python manage.py cv-role --regrade                clear existing scores and mark again

One command rather than the ingest/grade pair the portal roles use, because
there is no portal here and nothing to crawl. The whole path is: list the job's
candidates on Workable, fetch each one's uploaded resume, extract the text with
resume_reader, store the record, and mark it against the role's CV-only grid.

Candidates are scored out of 100 against the same bands as every other family
-- Best 85 / Better 75 / Good 60 / Okay below, advance bar at 75 -- so a card
from here ranks against a card from anywhere else. What differs is where the
marks come from: all 100 points read the record, because there is no work
sample to read instead. See backend/grading/cv_evaluator.py.
"""

import argparse
import logging
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Optional

from backend.config import CV_ONLY_JOBS, LLM_CONCURRENCY, LLM_MODEL
from backend.db import store
from backend.grading import cv_evaluator, evaluator
from backend.grading import rubric_pack as pack
from backend.logging_setup import setup_logging
from backend.scraping import workable_candidates

log = logging.getLogger("cv_role")


def _ensure_role(shortcode: str, label: str, slug: str, job_id: int,
                 title: str) -> dict:
    """
    The role document, created on first run.

    Portal roles arrive from the crawler; this one has no portal page to be
    crawled off, so the record is written from what config and Workable already
    know. Written every run rather than only when missing, so a retitled
    posting on Workable shows up here without anyone remembering to say so.
    """
    store.upsert_roles([{
        "job_id": job_id,
        "title": title or label,
        "slug": slug,
        "published": True,
        "status": "published",
        "live_assignment_label": "No assessment -- decided on CV",
        "cv_only": True,
        "workable_shortcode": shortcode,
        "apply_url": f"https://apply.workable.com/ajaia/j/{shortcode}/",
    }])
    role = store.get_role(job_id)
    if role is None:
        raise RuntimeError(f"Role {job_id} was written but cannot be read back.")
    return role


def _fetch(shortcode: str, job_id: int, limit: int) -> dict:
    records, stats = workable_candidates.fetch(shortcode, job_id, limit=limit)
    written = store.upsert_workable_candidates(records)
    log.info("Stored %d new candidate(s), updated %d.",
             written["inserted"], written["matched"])
    if stats["resumes_unread"]:
        # Not an error and not silent either. On this posting the figure has
        # been zero, and a run where it is not is a run where somebody should
        # look at why before reading the scores as a complete picture.
        log.warning("%d candidate(s) have no readable CV and will not be "
                    "graded -- on a CV-only seat there is nothing else to "
                    "grade.", stats["resumes_unread"])
    return {**stats, **written}


def _grade(role: dict, grid: dict, limit: int) -> dict:
    title = role.get("title", role["_id"])
    pending = store.ungraded(job_id=role["_id"], limit=limit)
    if not pending:
        log.info("[%s] nothing pending.", title)
        return {"graded": 0, "failed": 0, "pending": 0, "remaining": 0}

    log.info("[%s] marking %d candidate(s) against the %s grid with %s...",
             title, len(pending), grid.get("unit"), LLM_MODEL)

    graded = failed = ungrounded = 0
    marks: list[int] = []
    exhausted: Optional[evaluator.QuotaExhausted] = None

    def one(sub):
        return sub, cv_evaluator.evaluate_and_store_cv(sub, role, grid)

    # Keyed by future rather than a plain list, because a failure needs to name
    # the candidate it happened to and `future.result()` raising means nothing
    # came back to name them with. On this seat the commonest failure is a CV
    # that would not extract, and "evaluation failed" without a name is not a
    # thing anyone can act on.
    with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
        futures = {pool.submit(one, sub): sub for sub in pending}
        for future in as_completed(futures):
            try:
                sub, verdict = future.result()
            except evaluator.QuotaExhausted as exc:
                exhausted = exc
                for waiting in futures:
                    waiting.cancel()
                break
            except CancelledError:
                continue
            except evaluator.EvaluationFailed as exc:
                failed += 1
                log.warning("[%s] %s: %s", title, sub_name(futures[future]), exc)
                continue
            graded += 1
            triage = verdict.get("triage") or {}
            grounding = verdict.get("grounding") or {}
            ungrounded += grounding.get("ungrounded", 0)
            marks += [r["score"] for r in verdict.get("grid") or []
                      if r.get("score") is not None]
            log.info("  %-30s %5.1f  %-10s  triage %s/%s  quotes %s/%s",
                     (sub.get("candidate_name") or sub.get("candidate_email")
                      or "?")[:30],
                     verdict["score"], verdict["recommendation"],
                     triage.get("passed", "?"), triage.get("of", "?"),
                     grounding.get("verified", "?"), grounding.get("checked", "?"))
            for row in verdict.get("grid") or []:
                if row.get("grounded") is False:
                    log.warning("      unevidenced %s (%s): quote is not in "
                                "the record -- %.60s",
                                row["key"], row["score"], row.get("quote"))
            for finding in verdict.get("auto_fails") or []:
                log.info("      auto-fail: %s -- %s",
                         finding.get("rule"), finding.get("evidence"))
            for finding in verdict.get("fraud_tells") or []:
                log.warning("      FRAUD LOG: %s -- %s",
                            finding.get("tell"), finding.get("evidence"))

    if marks:
        middle = sum(1 for m in marks if m in (2, 3, 4))
        if not middle:
            log.warning("[%s] every one of the %d marks was a 5 or a 1. The "
                        "scale is being used as a binary.", title, len(marks))
        elif middle < len(marks) // 4:
            log.warning("[%s] only %d of %d marks used 2, 3 or 4. Thin spread.",
                        title, middle, len(marks))
    if ungrounded:
        log.warning("[%s] %d criterion mark(s) quoted text that is not in the "
                    "record. Those marks are unevidenced.", title, ungrounded)
    if exhausted:
        log.warning("[%s] daily token budget reached after %d graded. Re-run "
                    "to pick up the remaining %d.",
                    title, graded, len(pending) - graded - failed)

    return {"graded": graded, "failed": failed, "pending": len(pending),
            "exhausted": exhausted is not None,
            "remaining": len(pending) - graded - failed if exhausted else 0}


def sub_name(sub) -> str:
    if not isinstance(sub, dict):
        return "a candidate"
    return sub.get("candidate_name") or sub.get("candidate_email") or "?"


def run_role(shortcode: str, entry: tuple, args) -> dict:
    label, slug, job_id = entry

    grid = pack.for_slug(slug)
    if grid is None:
        log.error("No grid in the pack for slug %r. A CV-only role cannot "
                  "derive one from an assessment, because it has none -- add "
                  "a grid to rubric_pack/_grids.py claiming that slug.", slug)
        return {"graded": 0, "failed": 0}

    stats = {}
    if not args.grade_only:
        # The title comes back from Workable inside fetch(); the role document
        # is written first with the config label so a fetch that fails still
        # leaves a role on the dashboard saying what it is.
        _ensure_role(shortcode, label, slug, job_id, label)
        stats = _fetch(shortcode, job_id, args.limit)

    role = _ensure_role(shortcode, label, slug, job_id,
                        stats.get("job_title") or label)

    if args.fetch_only:
        return {"graded": 0, "failed": 0, **stats}

    if args.regrade:
        cleared = store.clear_evaluations(job_id=job_id)
        log.info("Cleared %d existing evaluation(s) for re-marking.", cleared)

    return {**stats, **_grade(role, grid, args.limit)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", help="Workable shortcode (e.g. EA7059EA8E)")
    parser.add_argument("--limit", type=int, default=0,
                        help="max candidates per role (0 = no cap)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch-only", action="store_true",
                      help="pull candidates and resumes, grade nothing")
    mode.add_argument("--grade-only", action="store_true",
                      help="grade what is stored, fetch nothing")
    parser.add_argument("--regrade", action="store_true",
                        help="clear existing AI scores first, keeping manual "
                             "decisions")
    args = parser.parse_args()

    setup_logging()

    if args.job:
        entry = CV_ONLY_JOBS.get(args.job)
        if not entry:
            log.error("%s is not in CV_ONLY_JOBS. Known: %s",
                      args.job, ", ".join(sorted(CV_ONLY_JOBS)) or "(none)")
            return 1
        jobs = {args.job: entry}
    else:
        jobs = dict(CV_ONLY_JOBS)

    if not jobs:
        log.error("No CV-only roles configured. Add one to "
                  "config.CV_ONLY_JOBS.")
        return 1

    if not args.fetch_only and not evaluator.is_configured():
        log.error("No LLM credentials. Set LLM_API_KEY in .env. --fetch-only "
                  "works without them.")
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        log.error("%s", exc)
        return 1

    totals = {"graded": 0, "failed": 0}
    for shortcode, entry in jobs.items():
        log.info("=== %s (%s) ===", entry[0], shortcode)
        result = run_role(shortcode, entry, args)
        totals["graded"] += result.get("graded", 0)
        totals["failed"] += result.get("failed", 0)

    print(f"\nGraded {totals['graded']} candidate(s), {totals['failed']} failed.")
    return 0 if totals["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
