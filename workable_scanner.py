"""
Candidate selection.

Returns the candidates for a job who applied inside the reminder window and
are in a stage that means they were actually invited.

This used to open every candidate's activity log looking for the invite email
-- one API call each, ~45 minutes for a large job. That is no longer needed:
Workable automation sends the invite when a candidate applies, so `created_at`
already tells us when the invite went out. Measured over 40 candidates the
median apply-to-invite lag was 0.00 days and the maximum 1.81, and 67 of 67
sampled candidates in the Applied and Assessment stages had a real invite.

The whole scan is now one paginated candidate list per job.
"""

import logging
from dataclasses import dataclass

from config import (
    ELIGIBLE_STAGES,
    INVITES_START_AT,
    REMINDER_AFTER_BUSINESS_DAYS,
    REMINDER_UNTIL_BUSINESS_DAYS,
)
from workable_client import get_job_candidates
from utils import business_days_since_iso, business_days_ago

log = logging.getLogger(__name__)


@dataclass
class Applicant:
    candidate_id: str
    name: str
    email: str
    job_shortcode: str
    job_title: str
    stage: str
    applied_at: str          # ISO timestamp; also when the invite was sent
    business_days_elapsed: int


def find_candidates_to_remind(
    job_shortcode: str,
    job_label: str,
) -> list[Applicant]:
    """
    Every candidate for the job who applied between REMINDER_AFTER_BUSINESS_DAYS
    and REMINDER_UNTIL_BUSINESS_DAYS ago, in an eligible stage.

    Whether they have actually started the assessment is decided later, against
    the portal.
    """
    # Bound the query generously, then filter exactly in Python. A couple of
    # days of slack here avoids an off-by-one at the API boundary and costs
    # nothing -- pagination is cheap.
    created_after = business_days_ago(REMINDER_UNTIL_BUSINESS_DAYS + 2).isoformat()

    log.info(
        "Fetching %s (%s) candidates created after %s",
        job_shortcode, job_label, created_after[:10],
    )

    try:
        raw = get_job_candidates(job_shortcode, created_after=created_after)
    except Exception as exc:
        log.error("Could not fetch candidates for %s: %s", job_shortcode, exc)
        return []

    log.info("Fetched %d candidates in the query window", len(raw))

    # Jobs whose invite automation was switched on late have applicants who were
    # never invited. They look identical to invited ones -- same stage, same
    # created_at -- so the only thing separating them is the date.
    invites_start = INVITES_START_AT.get(job_shortcode)

    applicants: list[Applicant] = []
    skipped_stage = skipped_window = skipped_no_email = 0
    skipped_pre_automation = 0

    for cand in raw:
        stage = (cand.get("stage") or "").strip()
        if stage.lower() not in ELIGIBLE_STAGES:
            skipped_stage += 1
            continue

        email = (cand.get("email") or "").strip().lower()
        if not email:
            skipped_no_email += 1
            continue

        applied_at = cand.get("created_at") or ""
        if not applied_at:
            skipped_window += 1
            continue

        # Applied before the automation existed, so no invite was ever sent.
        # Reminding them would deliver an assessment link out of nowhere.
        if invites_start and applied_at[:10] < invites_start:
            skipped_pre_automation += 1
            continue

        elapsed = business_days_since_iso(applied_at)
        if not (REMINDER_AFTER_BUSINESS_DAYS <= elapsed <= REMINDER_UNTIL_BUSINESS_DAYS):
            skipped_window += 1
            continue

        applicants.append(Applicant(
            candidate_id=cand.get("id", ""),
            name=cand.get("name") or "Candidate",
            email=email,
            job_shortcode=job_shortcode,
            job_title=(cand.get("job") or {}).get("title") or job_label,
            stage=stage,
            applied_at=applied_at,
            business_days_elapsed=elapsed,
        ))

    log.info(
        "In window: %d  (skipped: %d wrong stage, %d outside %d-%d business "
        "days, %d without an email)",
        len(applicants), skipped_stage, skipped_window,
        REMINDER_AFTER_BUSINESS_DAYS, REMINDER_UNTIL_BUSINESS_DAYS,
        skipped_no_email,
    )
    if skipped_pre_automation:
        log.info(
            "Skipped %d candidate(s) who applied before %s, when this job's "
            "invite automation was switched on -- they were never invited.",
            skipped_pre_automation, invites_start,
        )
    return applicants
