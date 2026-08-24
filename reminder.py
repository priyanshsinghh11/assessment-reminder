#!/usr/bin/env python3
"""
Assessment Reminder System
==========================

Runs daily via cron. For each assessment-enabled job:

  1. Downloads the assessment portal's CSV export -- everyone who has
     started, submitted, or is sitting in the portal's review queue. These
     candidates do NOT need reminders.
  2. Fetches candidates from Workable who applied between
     REMINDER_AFTER_BUSINESS_DAYS and REMINDER_UNTIL_BUSINESS_DAYS ago and
     are in an eligible stage (they applied, so automation invited them).
  3. Cross-references: anyone in the Workable window but not in the portal
     gets a reminder via Brevo.

The two halves are separate functions -- gather_state() decides who qualifies
and sends nothing, send_batch() does the sending. server.py drives the same
two functions from the dashboard.

AUTOMATION IS PAUSED. A plain `python reminder.py` will not scan or send while
AUTOMATION_ENABLED is off in config.py -- the cron entry can fire and nothing
happens. Read-only modes still work, and --force sends anyway for a run you are
sitting in front of. The dashboard's "Sync portal" button is unaffected: it is
a person clicking, which is the whole point.

Usage:
    python reminder.py                # normal run (blocked while paused)
    python reminder.py --force        # send anyway, paused or not
    python reminder.py --preview      # print the emails here, send nothing
    python reminder.py --dry-run      # log what WOULD happen, send nothing
    python reminder.py --scan-only    # just list who qualifies
    python reminder.py --job CODE     # restrict to one job shortcode
    python reminder.py --limit N      # send at most N emails this run
    python reminder.py --help         # every flag, spelled exactly

Any other flag is an error and the run stops. A mistyped --dry-run (--dryrun,
--dry_run) can never fall through to a live send.

Exit codes, so cron can tell a quiet run from a broken one:
    0  ran fine -- including a paused run that did nothing
    1  could not run: portal down, unknown --job, no jobs configured
    2  ran, but at least one email failed to send
"""

import argparse
import sys
import logging
from datetime import datetime, timezone
from typing import Optional

from config import (
    ASSESSMENT_JOBS,
    AUTOMATION_ENABLED,
    REMINDER_AFTER_BUSINESS_DAYS,
    REMINDER_UNTIL_BUSINESS_DAYS,
    MAX_REMINDERS_PER_CANDIDATE,
    DAYS_BETWEEN_REMINDERS,
    LOG_DIR,
    LOG_FILE,
)
from workable_scanner import find_candidates_to_remind
from portal_scraper import fetch_portal_records, get_portal_emails
from brevo_client import send_reminder_email, build_reminder_email
from utils import (
    should_send_reminder,
    record_reminder,
    state_key,
    load_reminder_state,
)

log = logging.getLogger("reminder")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level=logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# Step 1 + 2: work out who qualifies. Sends nothing.
# ---------------------------------------------------------------------------

class PortalUnavailable(RuntimeError):
    """
    The portal returned nothing.

    Treated as fatal on purpose: without the portal every candidate looks like
    they never started, so continuing would email the entire window at once.
    """


def gather_state(only_job: Optional[str] = None) -> dict:
    """
    Build the full picture: every candidate in the reminder window, annotated
    with whether they have started the assessment and how many reminders they
    have already had.

    Returns a dict shaped for both the CLI and the dashboard API.
    """
    jobs = dict(ASSESSMENT_JOBS)
    if only_job:
        jobs = {k: v for k, v in jobs.items() if k == only_job}
        if not jobs:
            raise ValueError(f"No job with shortcode {only_job} in ASSESSMENT_JOBS.")

    log.info("--- Downloading assessment portal records ---")
    portal_records = fetch_portal_records()
    if not portal_records:
        raise PortalUnavailable(
            "The portal returned no records. Refusing to continue rather than "
            "treating every candidate as 'never started' and emailing them all."
        )

    reminder_log = load_reminder_state()
    candidates: list[dict] = []
    portal_totals = {"total": 0, "submitted": 0, "in_progress": 0, "under_review": 0}

    # Every email the portal knows about, on any assignment. Used only to
    # explain a miss: a candidate we are about to chase who is on the portal
    # under a different job_id means the assignment mapping is wrong, not that
    # they never started. Logged, never acted on -- see get_portal_emails().
    portal_emails_anywhere = {rec.email for rec in portal_records}

    for shortcode, job in jobs.items():
        label = job["label"]
        portal_job_id = job.get("portal_job_id")
        started = get_portal_emails(portal_records, portal_job_id)
        reviewing = {
            rec.email for rec in portal_records
            if rec.under_review and (not portal_job_id or rec.job_id == portal_job_id)
        }

        log.info("--- Job: %s (%s) ---", shortcode, label)
        log.info(
            "Portal: %d candidates on this assignment "
            "(%d submitted, %d in progress, %d in the review queue)",
            len(started),
            sum(1 for s in started.values() if s == "submitted"),
            sum(1 for s in started.values() if s == "in_progress"),
            len(reviewing),
        )
        portal_totals["total"] += len(started)
        portal_totals["submitted"] += sum(1 for s in started.values() if s == "submitted")
        portal_totals["in_progress"] += sum(1 for s in started.values() if s == "in_progress")
        portal_totals["under_review"] += len(reviewing)

        for cand in find_candidates_to_remind(shortcode, label):
            record = reminder_log.get(state_key(cand.email, portal_job_id)) or {}
            if not started.get(cand.email) and cand.email in portal_emails_anywhere:
                log.warning(
                    "%s (%s) has no record on assignment %s but does appear on "
                    "the portal under another assignment. They will be treated "
                    "as not started -- check the %s mapping in JOB_ASSESSMENTS.",
                    cand.name, cand.email, portal_job_id, shortcode,
                )
            candidates.append({
                "candidate_id": cand.candidate_id,
                "name": cand.name,
                "email": cand.email,
                "job_shortcode": shortcode,
                "portal_job_id": portal_job_id,
                "job_title": cand.job_title,
                "stage": cand.stage,
                "applied_at": cand.applied_at,
                "business_days_elapsed": cand.business_days_elapsed,
                "portal_status": started.get(cand.email),
                "portal_under_review": cand.email in reviewing,
                "reminders_sent": record.get("reminders_sent", 0),
                "last_reminder_at": record.get("last_reminder_at"),
                "assessment_url": job["assessment_url"],
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "reminder_after_business_days": REMINDER_AFTER_BUSINESS_DAYS,
            "reminder_until_business_days": REMINDER_UNTIL_BUSINESS_DAYS,
            "max_reminders_per_candidate": MAX_REMINDERS_PER_CANDIDATE,
            "days_between_reminders": DAYS_BETWEEN_REMINDERS,
        },
        "portal": portal_totals,
        "jobs": [
            {"shortcode": code, "label": job["label"]}
            for code, job in jobs.items()
        ],
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Step 3: send
# ---------------------------------------------------------------------------

def print_email_preview(cand: dict, reminder_number: int) -> None:
    """
    Print the exact email a candidate would receive to stdout.

    Goes to whatever terminal is running the process -- for the dashboard,
    that is the terminal running server.py.
    """
    email = build_reminder_email(
        cand["name"], cand["job_title"], cand["assessment_url"], reminder_number
    )
    print("\n" + "=" * 78)
    print(f"TO:      {cand['name']} <{cand['email']}>")
    print(f"SUBJECT: {email['subject']}")
    print(f"REMINDER #{reminder_number}  |  applied {cand['business_days_elapsed']} "
          f"business days ago  |  stage={cand['stage']}")
    print("-" * 78)
    print(email["text"].strip())
    print("=" * 78, flush=True)


def send_batch(
    candidates: list[dict],
    dry_run: bool = False,
    preview: bool = False,
    limit: Optional[int] = None,
    only_emails: Optional[list[str]] = None,
) -> tuple[dict, list[str]]:
    """
    Send reminders to candidates who have not started the assessment.

    only_emails restricts the send to a hand-picked subset -- this is what the
    dashboard's "send to selected" uses. The dedupe and window rules still
    apply to every candidate, so a selection can never bypass them.

    preview=True prints each email to the terminal instead of sending it.
    Nothing reaches Brevo and nothing is recorded, so the same candidates stay
    selectable afterwards and can be previewed as many times as you like.

    Returns (totals, recorded) where `recorded` identifies every candidate this
    run actually wrote a reminder for, as {"email", "portal_job_id"} -- the two
    fields state_key() is built from, left separate so the dashboard can match
    rows without reimplementing the key format. It is empty for preview and
    dry-run, which record nothing. The dashboard uses it to update the rows it
    just sent to instead of re-running the whole scan.
    """
    picked = {e.strip().lower() for e in only_emails} if only_emails else None

    totals = {
        "considered": len(candidates),
        "already_started": 0,
        "not_selected": 0,
        "already_reminded": 0,
        "duplicate_in_run": 0,
        "skipped_by_limit": 0,
        "previewed": 0,
        "reminders_sent": 0,
        "errors": 0,
    }
    sent = 0
    recorded: list[dict] = []

    # One candidate can appear once per Workable posting they applied to, and
    # several postings share an assignment -- so the same person can show up
    # more than once in this list with the same assessment to complete. A live
    # send would catch the repeat on the state file it just wrote, but preview
    # and dry-run write nothing, so their counts would overstate the real send.
    # Track it here instead and every mode agrees.
    seen_this_run: set[str] = set()

    for cand in candidates:
        email = cand["email"]

        if cand.get("portal_status"):
            totals["already_started"] += 1
            continue

        if picked is not None and email not in picked:
            totals["not_selected"] += 1
            continue

        group = cand["portal_job_id"]
        if not should_send_reminder(email, group, DAYS_BETWEEN_REMINDERS):
            log.info("%s (%s): already reminded, or too soon.", cand["name"], email)
            totals["already_reminded"] += 1
            continue

        key = state_key(email, group)
        if key in seen_this_run:
            log.info(
                "%s (%s): already queued this run for assignment %s "
                "(also applied via %s). Skipping the duplicate.",
                cand["name"], email, group, cand["job_shortcode"],
            )
            totals["duplicate_in_run"] += 1
            continue

        if limit is not None and sent >= limit:
            totals["skipped_by_limit"] += 1
            continue

        # Claimed before the send, not after: a Brevo failure must not let the
        # duplicate row through as a second attempt at the same person.
        seen_this_run.add(key)
        reminder_number = cand.get("reminders_sent", 0) + 1

        if preview:
            print_email_preview(cand, reminder_number)
            totals["previewed"] += 1
            sent += 1
            continue

        if dry_run:
            log.info(
                "[DRY RUN] Would send reminder #%d to %s (%s) | applied %d "
                "business days ago | %s",
                reminder_number, cand["name"], email,
                cand["business_days_elapsed"], cand["assessment_url"],
            )
            totals["reminders_sent"] += 1
            sent += 1
            continue

        ok = send_reminder_email(
            to_email=email,
            to_name=cand["name"],
            role_title=cand["job_title"],
            assessment_url=cand["assessment_url"],
            reminder_number=reminder_number,
        )

        if ok:
            record_reminder(
                email=email,
                assessment_group=group,
                candidate_id=cand["candidate_id"],
                candidate_name=cand["name"],
                job_shortcode=cand["job_shortcode"],
            )
            recorded.append({"email": email, "portal_job_id": group})
            totals["reminders_sent"] += 1
            sent += 1
        else:
            totals["errors"] += 1

    return totals, recorded


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        # The usage block and exit-code table in the docstring are laid
        # out by hand; the default formatter reflows them into a blob.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true",
                        help="send even though automation is paused")
    parser.add_argument("--preview", action="store_true",
                        help="print each email to this terminal, send nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would be sent, send nothing")
    parser.add_argument("--scan-only", action="store_true",
                        help="list who qualifies and stop")
    parser.add_argument("--job", metavar="CODE",
                        help="restrict to one Workable job shortcode")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="send at most N emails this run")
    args = parser.parse_args()

    setup_logging()

    dry_run = args.dry_run
    scan_only = args.scan_only
    preview = args.preview
    force = args.force
    only_job = args.job
    limit = args.limit

    # The paused switch. Checked before anything else so a cron entry left in
    # place costs one log line, not a portal download -- and never an email.
    # Modes that cannot send are exempt: they are how you check the system
    # while it is paused.
    sends = not (dry_run or scan_only or preview)
    if sends and not AUTOMATION_ENABLED and not force:
        log.warning(
            "Automated runs are paused (AUTOMATION_ENABLED is off in config.py). "
            "Nothing was scanned or sent. Use the dashboard's Sync portal button, "
            "or re-run with --force to send from here."
        )
        # Not a failure: a cron entry firing while paused is the expected
        # state, and should not page anyone.
        return 0

    if preview:
        log.info("=== PREVIEW MODE -- emails printed here, nothing sent ===")
    if dry_run:
        log.info("=== DRY RUN MODE -- no emails will be sent ===")
    if scan_only:
        log.info("=== SCAN ONLY MODE ===")
    if sends and force and not AUTOMATION_ENABLED:
        log.info("=== --force: sending even though automation is paused ===")
    if limit is not None:
        log.info("Send limit for this run: %d", limit)

    log.info("Run started at %s", datetime.now(timezone.utc).isoformat())

    if not ASSESSMENT_JOBS:
        log.warning(
            "ASSESSMENT_JOBS is empty in config.py. Add your Workable shortcodes."
        )
        return 1

    try:
        state = gather_state(only_job)
    except (PortalUnavailable, ValueError) as exc:
        log.error("%s", exc)
        return 1

    candidates = state["candidates"]
    not_started = [c for c in candidates if not c["portal_status"]]

    log.info(
        "In window: %d  |  already started: %d  |  eligible: %d",
        len(candidates), len(candidates) - len(not_started), len(not_started),
    )

    failed = 0
    if scan_only:
        for cand in not_started:
            log.info(
                "[ELIGIBLE] %s (%s) | stage=%s | applied %d business days ago",
                cand["name"], cand["email"], cand["stage"],
                cand["business_days_elapsed"],
            )
    else:
        totals, _ = send_batch(
            candidates, dry_run=dry_run, preview=preview, limit=limit
        )
        for key, value in totals.items():
            log.info("  %s: %d", key, value)
        failed = totals["errors"]

    log.info(
        "=== Run complete (window: %d-%d business days) ===",
        REMINDER_AFTER_BUSINESS_DAYS, REMINDER_UNTIL_BUSINESS_DAYS,
    )

    # A send that Brevo rejected is a real failure even though the rest of the
    # run worked -- give cron something to notice.
    if failed:
        log.error("%d reminder(s) failed to send.", failed)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
