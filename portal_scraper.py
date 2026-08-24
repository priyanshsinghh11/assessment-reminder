"""
Assessment portal client for candidateassessments.ajaia.ai.

Logs in and downloads the portal's own CSV export -- the same file the
"Export CSV" button produces. One request returns every submission with real
columns, so there is no HTML parsing and nothing to break when the dashboard
layout changes.

(The dashboard HTML was the original approach. It renders only ~200 of the
rows, and status had to be guessed from badge text, so it both under-reported
and mis-classified. The CSV has an explicit submission_status column.)

The bare export URL is never used on its own. It hides every row a reviewer has
touched -- the Pending Review queue included -- which made candidates who had
already submitted look like they had never started, and got them reminded. The
comment on PORTAL_SUBMISSIONS_CSV_ALL in config.py has the measurements.

Because the complete export is too big to download reliably, it is fetched one
review state at a time and reassembled. See REVIEW_BUCKETS below.
"""

import io
import csv
import time
import logging
import requests
from bs4 import BeautifulSoup
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from config import (
    PORTAL_LOGIN_URL,
    PORTAL_EMAIL,
    PORTAL_PASSWORD,
    PORTAL_SUBMISSIONS_CSV,
    PORTAL_SUBMISSIONS_CSV_ALL,
)

log = logging.getLogger(__name__)

# The `submission` column holds full free-text answers, so single records span
# many lines and can be very large. Without this the reader raises on them.
csv.field_size_limit(10_000_000)


# `review_status` values that mean nobody has touched the submission yet. "new"
# is the portal's starting state; the observed values past it are pending,
# rejected, reviewed and interview.
#
# Deliberately kept short: an unfamiliar value counts as REVIEWED. The two ways
# of being wrong are not symmetric -- mistaking a reviewed candidate for an idle
# one emails someone about work they already did, while the reverse costs only
# a reminder nobody was owed.
UNREVIEWED = {"", "new", "none", "not_reviewed", "unreviewed"}


@dataclass
class PortalRecord:
    email: str
    name: str
    job_id: str
    job_title: str
    status: str                      # "submitted" or "in_progress"
    review_status: str = ""          # portal-side review queue: "new", ...
    screener_rating: str = ""
    started_at: Optional[str] = None
    submitted_at: Optional[str] = None
    reviewed_at: Optional[str] = None

    @property
    def under_review(self) -> bool:
        """
        Has this submission entered the portal's review queue?

        Three independent signals, because the portal can express the same
        thing in more than one place and only one of them has to be set: an
        explicit review_status, a reviewed_at timestamp, or a screener rating.
        """
        if (self.review_status or "").strip().lower() not in UNREVIEWED:
            return True
        return bool((self.reviewed_at or "").strip()) or \
            bool((self.screener_rating or "").strip())

    @property
    def effective_status(self) -> str:
        """
        The status the reminder logic should act on: the portal's own.

        An earlier version promoted any reviewed record to "submitted", on the
        reasoning that nobody reviews work that was never handed in. The full
        export disproves it -- 1,586 rows are in_progress with the review
        column moved on regardless. Reporting those as submitted would put a
        wrong badge on the dashboard for no gain, since presence in the export
        is what suppresses the reminder either way. `under_review` carries the
        review signal separately.
        """
        return self.status


def _login() -> Optional[requests.Session]:
    """
    Log in and return a session carrying the auth cookie, or None on failure.

    The login form takes exactly `email` and `password` -- no CSRF token. Any
    unauthenticated request to /admin/* redirects to the login page with HTTP
    200, so success is judged by the final URL, never by status code.
    """
    if not PORTAL_EMAIL or not PORTAL_PASSWORD:
        log.error(
            "PORTAL_EMAIL and PORTAL_PASSWORD must be set in .env. "
            "Cannot reach the portal."
        )
        return None

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    try:
        login_page = session.get(PORTAL_LOGIN_URL, timeout=20)
        login_page.raise_for_status()
    except requests.RequestException as exc:
        log.error("Could not load portal login page: %s", exc)
        return None

    # Carry over any hidden fields the form declares, in case one is added
    # later, then overwrite the credentials.
    data = {}
    form = BeautifulSoup(login_page.text, "html.parser").find("form")
    if form:
        for field in form.find_all(["input", "select", "textarea"]):
            name = field.get("name")
            if name:
                data[name] = field.get("value", "")
    data["email"] = PORTAL_EMAIL
    data["password"] = PORTAL_PASSWORD

    try:
        resp = session.post(PORTAL_LOGIN_URL, data=data, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Portal login request failed: %s", exc)
        return None

    if "/login" in resp.url:
        log.error(
            "Portal login failed (still on the login page). "
            "Check PORTAL_EMAIL and PORTAL_PASSWORD."
        )
        return None

    log.info("Portal login successful.")
    return session


CSV_ATTEMPTS = 3

# The export is fetched one review state at a time, not in one go.
#
# The unfiltered export is ~40 MB, almost all of it submission_markdown, and
# the portal drops the connection part-way through the body most of the time:
# measured 14 Aug 2026, 6 failures in 8 attempts, buffered or streamed alike,
# at roughly 100 seconds per failed attempt. Leading with it cost five minutes
# a run and usually ended in the fallback anyway.
#
# Per bucket it comes down reliably. Measured the same day: new 4,464 rows /
# 25 MB, pending 2,647 / 11 MB, rejected 1,117 / 1.7 MB, reviewed 300 /
# 0.9 MB, interview 83 / 0.3 MB.
#
# Each value returns only its own rows -- verified, none silently falls back to
# the default set. The cost of splitting is that this list has to stay current:
# an unrecognised value falls back rather than erroring, so a review state the
# portal adds later would not be fetched and its candidates would look like
# they never started. The per-bucket counts are logged every run, and
# fetch_portal_records() logs the review_status spread, so a state that stops
# appearing is visible. Add it here when it does.
REVIEW_BUCKETS = ("new", "pending", "rejected", "reviewed", "interview")


def _download(session: requests.Session, url: str, label: str) -> Optional[str]:
    """
    Fetch one CSV, streamed, retrying a dropped or truncated read.

    Streamed because the body is far too big to want buffered whole, and
    because reading it in chunks fails faster and more cleanly when the
    connection does drop.
    """
    for attempt in range(1, CSV_ATTEMPTS + 1):
        try:
            with session.get(url, timeout=300, stream=True) as resp:
                resp.raise_for_status()
                chunks = [chunk for chunk in resp.iter_content(chunk_size=1 << 20)]
                encoding = resp.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")
        except requests.RequestException as exc:
            log.warning(
                "Portal CSV (%s) attempt %d of %d failed: %s",
                label, attempt, CSV_ATTEMPTS, exc,
            )
            if attempt < CSV_ATTEMPTS:
                time.sleep(5 * attempt)
    return None


def _download_rows(session: requests.Session) -> Optional[list[dict]]:
    """
    Every row of the export, or None if it could not be fetched in full.

    Assembled from one request per review bucket -- see REVIEW_BUCKETS for why
    that is the normal path rather than the single unfiltered request, which is
    kept as a last resort for when a bucket fails.

    Never returns partial data. If neither route completes, the caller gets
    None, because a short record set is indistinguishable from a portal where
    those candidates never started -- and gather_state() would read that as
    "nobody has started" and email the lot.
    """
    # Keyed by submission_id so a record that moves between buckets mid-fetch
    # is counted once rather than twice.
    rows: dict[str, dict] = {}
    for bucket in REVIEW_BUCKETS:
        body = _download(
            session,
            f"{PORTAL_SUBMISSIONS_CSV}?review_status={bucket}",
            f"review_status={bucket}",
        )
        if body is None:
            log.warning(
                "Bucket '%s' would not download. Trying the single unfiltered "
                "export instead.", bucket,
            )
            break
        before = len(rows)
        for row in csv.DictReader(io.StringIO(body)):
            rows[row.get("submission_id") or f"?{len(rows)}"] = row
        log.info("  %-10s %d row(s)", bucket, len(rows) - before)
    else:
        return list(rows.values())

    # A bucket failed, so what we have is incomplete. Discard it -- mixing it
    # with a second attempt would only hide which rows are missing.
    body = _download(session, PORTAL_SUBMISSIONS_CSV_ALL, "full export")
    if body is None:
        return None
    return list(csv.DictReader(io.StringIO(body)))


def fetch_portal_records() -> list[PortalRecord]:
    """Download and parse every submission the portal knows about."""
    session = _login()
    if session is None:
        return []

    rows = _download_rows(session)
    if rows is None:
        log.error("Could not download the portal CSV export.")
        return []

    records = []
    for row in rows:
        email = (row.get("candidate_email") or "").strip().lower()
        if not email:
            continue
        records.append(PortalRecord(
            email=email,
            name=row.get("candidate_name") or "",
            job_id=row.get("job_id") or "",
            job_title=row.get("job_title") or "",
            status=row.get("submission_status") or "unknown",
            review_status=row.get("review_status") or "",
            screener_rating=row.get("screener_rating") or "",
            started_at=row.get("started_at") or None,
            submitted_at=row.get("submitted_at") or None,
            reviewed_at=row.get("reviewed_at") or None,
        ))

    log.info("Portal: %d submission records downloaded.", len(records))

    # The review columns are the portal's, not ours, and a new value can appear
    # there without warning. Log the spread every run so an unfamiliar state
    # shows up in the log rather than passing unnoticed -- it also has to be
    # added to REVIEW_BUCKETS, or the fallback path will stop seeing it.
    spread = Counter((r.review_status or "").strip().lower() or "(blank)"
                     for r in records)
    log.info(
        "Portal review_status: %s  |  %d record(s) in the review queue.",
        ", ".join(f"{value}={count}" for value, count in spread.most_common()),
        sum(1 for r in records if r.under_review),
    )
    return records


# How much a status counts for when one candidate has several records for the
# same assignment. Anything unrecognised still outranks having no record at
# all: an unknown state is evidence the candidate is on the portal, and a
# reminder is the one thing that must not happen on a guess.
_STATUS_RANK = {"submitted": 3, "in_progress": 2}


def get_portal_emails(
    records: Optional[list[PortalRecord]] = None,
    portal_job_id: Optional[str] = None,
) -> dict[str, str]:
    """
    Return {lowercased_email: status} for candidates in the portal.

    Presence in this dict is what suppresses a reminder, so it must cover
    everyone the portal knows about -- candidates in the review queue included.
    That depends entirely on the caller having fetched the unfiltered export.

    Pass portal_job_id to restrict to a single assignment -- someone who
    completed a *different* role's assessment has not started this one.
    Pass an existing `records` list to avoid re-downloading per job.
    """
    if records is None:
        records = fetch_portal_records()

    emails: dict[str, str] = {}
    for rec in records:
        if portal_job_id and rec.job_id != portal_job_id:
            continue
        status = rec.effective_status
        best = emails.get(rec.email)
        # A submitted record always wins over a merely started one.
        if best is None or _STATUS_RANK.get(status, 1) > _STATUS_RANK.get(best, 1):
            emails[rec.email] = status
    return emails
