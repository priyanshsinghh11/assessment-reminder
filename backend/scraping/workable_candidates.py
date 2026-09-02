"""
Build gradeable records for a posting that has no assessment.

The portal path collects a `resume_link` the candidate typed into a form, and
resume_reader's module docstring is a long account of what those links turn out
to be: Google Drive share pages, LinkedIn profiles, folders, files nobody
granted access to. About 40% of them never yield text.

This path collects nothing. Workable already holds the file the candidate
uploaded when they applied, and the candidate detail endpoint hands back a
presigned S3 link straight to it. Measured over all 58 candidates on
EA7059EA8E on 2026-08-25: 58 extracted, 0 errors, 51 PDF and 7 DOCX. The
extraction code is the same resume_reader either way -- the difference is
entirely that Workable hosts the bytes and Drive hosts a sign-in page.

What comes out of here is shaped like a portal submission, because everything
downstream -- the grader, the decision flow, the dashboard, the shortlist --
already speaks that shape. The fields a submission has and a CV-only candidate
cannot are left empty rather than faked: there is no submission_markdown, no
video_link, no started_at, and `assessment_name` says so in words.
"""

import logging
from typing import Optional

from backend.scraping import resume_reader
from backend.scraping.workable_client import (
    get_candidate,
    get_job,
    get_job_candidates,
)

log = logging.getLogger(__name__)

# Stages whose candidates are worth reading. Everything at or past an interview
# has a human verdict on it already, and disqualified candidates have one too.
#
# Deliberately not `workable_scanner.ELIGIBLE_STAGES`, which exists to answer a
# different question -- "was this person invited to an assessment" -- on jobs
# that have one. A CV-only posting invites nobody.
GRADEABLE_STAGES = ("applied", "sourced", "screen", "assessment")


def _experience_summary(candidate: dict, limit: int = 6) -> str:
    """
    The candidate's Workable-parsed work history as plain lines.

    Kept beside the resume text rather than merged into it. It is the same
    career read twice -- once by Workable's parser off the same file, once by
    ours -- and the two disagree in useful ways: the parser has clean company
    names and dates where our extraction has a mangled two-column header, and
    it has nothing at all where the CV has the sentence that says what the
    person did. Storing both means a grader can use whichever is legible.
    """
    lines = []
    for entry in (candidate.get("experience_entries") or [])[:limit]:
        title = (entry.get("title") or "").strip()
        company = (entry.get("company") or "").strip()
        start = (entry.get("start_date") or "")[:7]
        end = "present" if entry.get("current") else (entry.get("end_date") or "")[:7]
        head = " at ".join(part for part in (title, company) if part)
        when = f" ({start} to {end})" if start or end else ""
        if not head:
            continue
        lines.append(f"- {head}{when}")
        summary = (entry.get("summary") or "").strip()
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def _education_summary(candidate: dict, limit: int = 4) -> str:
    lines = []
    for entry in (candidate.get("education_entries") or [])[:limit]:
        parts = [(entry.get("degree") or "").strip(),
                 (entry.get("field_of_study") or "").strip(),
                 (entry.get("school") or "").strip()]
        line = ", ".join(part for part in parts if part)
        if line:
            lines.append(f"- {line}")
    return "\n".join(lines)


def _skills(candidate: dict) -> str:
    names = [(s or {}).get("name", "").strip()
             for s in (candidate.get("skills") or [])]
    return ", ".join(name for name in names if name)


def _linkedin(candidate: dict) -> str:
    for profile in (candidate.get("social_profiles") or []):
        if (profile or {}).get("type") == "linkedin":
            return (profile.get("url") or "").strip()
    return ""


def build_record(candidate: dict, job_id: int, job_title: str,
                 submission_id: Optional[int] = None) -> dict:
    """
    One candidate, fetched and read, in submission shape.

    `submission_id` is left to the caller because it is allocated by Mongo on
    first sight -- see store.upsert_workable_candidates. Nothing else
    here needs it.
    """
    resume_url = candidate.get("resume_url") or ""
    text, error = resume_reader.read_resume(resume_url)

    metadata = candidate.get("resume_metadata") or {}
    return {
        "submission_id": submission_id,
        "workable_candidate_id": candidate.get("id") or "",
        "job_id": job_id,
        "job_title": job_title,
        "candidate_name": candidate.get("name") or "Candidate",
        "candidate_email": (candidate.get("email") or "").strip().lower(),
        "candidate_phone": (candidate.get("phone") or "").strip(),
        "candidate_headline": (candidate.get("headline") or "").strip(),
        "candidate_location": ((candidate.get("location") or {}).get("location_str")
                               or (candidate.get("address") or "")),
        "linkedin_url": _linkedin(candidate),
        "profile_url": candidate.get("profile_url") or "",
        "workable_stage": (candidate.get("stage") or "").strip(),
        "submitted_at": candidate.get("created_at") or "",
        # The portal's own columns, held empty rather than omitted, so a
        # CV-only record and a portal one have the same keys and every reader
        # of `submission.get(...)` behaves the same on both.
        "submission_markdown": "",
        "video_link": "",
        "started_at": "",
        "assignment_name": "No assessment -- decided on CV",
        "review_status": "new",
        # "submitted" is the portal's word for a candidate who handed in
        # everything the role asked of them, and that is what this is: the
        # application IS the submission here. Kept rather than invented afresh
        # because ungraded(), the role counts and the dashboard filters all key
        # on this exact value, and a new one would make a CV-only role a role
        # nothing could see. `cv_only` below is how a reader tells the two
        # kinds apart.
        "submission_status": "submitted",
        "cv_only": True,
        "auto_submitted": False,
        # Resume fields, written the same way ingest.py writes them so
        # store.set_resume and the resume stats read both paths alike.
        #
        # The two links are different on purpose, and it is the S3 one that
        # cannot be `resume_link`. The dashboard renders `resume_link` as "Open
        # resume" for a reviewer to click, and the presigned URL carries an
        # X-Amz-Expires that runs out in hours -- so by the time anybody reads
        # a score, the link beside it would 403. The Workable profile does not
        # expire, is where the resume is viewable anyway, and carries the rest
        # of the application with it.
        #
        # The S3 URL is kept as `resume_source_link` because that field is
        # provenance rather than navigation: it records where these bytes were
        # actually read from. It is expected to be dead on arrival, which is
        # fine for a thing nobody clicks.
        "resume_link": candidate.get("profile_url") or resume_url,
        "resume_source_link": resume_url,
        "resume_filename": metadata.get("filename") or "",
        "resume_filetype": metadata.get("filetype") or "",
        "resume_text": text,
        "resume_error": error,
        # Workable's own parse of the same file. See _experience_summary.
        "workable_experience": _experience_summary(candidate),
        "workable_education": _education_summary(candidate),
        "workable_skills": _skills(candidate),
        "cover_letter": (candidate.get("cover_letter") or "").strip(),
        "candidate_summary": (candidate.get("summary") or "").strip(),
    }


def fetch(shortcode: str, job_id: int, stages: tuple[str, ...] = GRADEABLE_STAGES,
          limit: int = 0) -> tuple[list[dict], dict]:
    """
    Every gradeable candidate on a posting, with their resume text.

    Returns (records, stats). One list call for the job plus one detail call
    per candidate, which at the client's 8-per-10s ceiling is about 75 seconds
    per 58 candidates. Fine at this size; a job with two thousand applicants
    would want a `created_after` bound.
    """
    job = get_job(shortcode)
    title = job.get("title") or ""

    raw = get_job_candidates(shortcode)
    wanted = [c for c in raw
              if (c.get("stage") or "").strip().lower() in stages]
    if limit:
        wanted = wanted[:limit]

    log.info("%s (%s): %d candidates, %d in a gradeable stage",
             shortcode, title, len(raw), len(wanted))

    records, failed = [], 0
    for index, listed in enumerate(wanted, 1):
        candidate_id = listed.get("id") or ""
        try:
            detail = get_candidate(candidate_id)
        except Exception as exc:                       # noqa: BLE001
            # One candidate's failed detail call must not end the run. They
            # simply are not in this batch, and the next one picks them up.
            log.warning("Could not fetch candidate %s: %s", candidate_id, exc)
            failed += 1
            continue

        record = build_record(detail, job_id, title)
        records.append(record)
        if record["resume_error"]:
            log.info("  %3d/%d  %-28s  no text: %s", index, len(wanted),
                     record["candidate_name"][:28], record["resume_error"])
        else:
            log.debug("  %3d/%d  %-28s  %d chars", index, len(wanted),
                      record["candidate_name"][:28], len(record["resume_text"]))

    read = sum(1 for r in records if r["resume_text"])
    stats = {
        "job_title": title,
        "candidates": len(raw),
        "gradeable": len(wanted),
        "fetched": len(records),
        "fetch_failed": failed,
        "resumes_read": read,
        "resumes_unread": len(records) - read,
    }
    log.info("Read %d of %d resumes (%d could not be fetched from Workable)",
             read, len(records), failed)
    return records, stats
