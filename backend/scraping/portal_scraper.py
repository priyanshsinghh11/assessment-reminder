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

from backend.config import (
    PORTAL_LOGIN_URL,
    PORTAL_EMAIL,
    PORTAL_PASSWORD,
    PORTAL_SUBMISSIONS_CSV,
    PORTAL_SUBMISSIONS_CSV_ALL,
    PORTAL_REQUIRED_COLUMNS,
    PORTAL_MIN_TOTAL_ROWS,
    PORTAL_BUCKET_DROP_TOLERANCE,
)
from backend.db import store

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


class PortalExportSuspect(RuntimeError):
    """The body arrived but does not look like the export it claims to be."""


def _parse_rows(body: str, label: str) -> list[dict]:
    """
    Parse one CSV body, refusing anything that is not recognisably the export.

    A 200 IS NOT EVIDENCE THE EXPORT WORKED. An expired portal session answers
    200 with an HTML login page, which csv.DictReader will happily parse into
    rows of nonsense keyed by "<!DOCTYPE html>". Those rows carry no
    candidate_email, so every one of them is dropped later -- leaving a record
    set that is short, or empty, with nothing anywhere reporting a failure. A
    short record set reads downstream as "these candidates never started", and
    that is acted on by sending mail.

    So the header is checked before any row is trusted. It is a cheap check and
    it catches the login page, an error document, and a schema that has moved.
    """
    reader = csv.DictReader(io.StringIO(body))
    columns = set(reader.fieldnames or ())
    missing = [column for column in PORTAL_REQUIRED_COLUMNS
               if column not in columns]
    if missing:
        sample = " ".join((body[:120] or "").split())
        raise PortalExportSuspect(
            f"{label}: not the submissions export -- missing column(s) "
            f"{', '.join(missing)}. First bytes: {sample!r}")
    return list(reader)


def _remembered_bucket_counts() -> dict:
    """What each bucket held on the last fetch that was believed in full."""
    try:
        document = store.get_db().settings.find_one({"_id": "portal_bucket_counts"})
    except Exception as exc:
        log.warning("Could not read the last known bucket counts (%s); "
                    "skipping the shrink check this run.", exc)
        return {}
    return (document or {}).get("counts") or {}


def _remember_bucket_counts(counts: dict) -> None:
    """Record this fetch's shape, so the next one has something to check against."""
    try:
        store.get_db().settings.update_one(
            {"_id": "portal_bucket_counts"},
            {"$set": {"counts": counts, "updated_at": store.now()}},
            upsert=True,
        )
    except Exception as exc:
        log.warning("Could not record the bucket counts (%s).", exc)


def _bucket_shrank(bucket: str, seen: int, before: int) -> bool:
    """
    Has this bucket lost enough rows to mean the fetch is broken, not the news?

    A bucket that HAD rows and now has NONE is always broken. That is the exact
    shape of the failure this exists for: the portal stops recognising a
    review_status value, answers 200 with a well-formed empty CSV, and every
    candidate in that bucket silently becomes someone who never started.

    An absolute row floor does not catch it. "interview" holds around 83 rows,
    so it can empty completely without moving any total-row threshold -- and
    those 83 people get emailed.
    """
    if before <= 0:
        return False
    if seen == 0:
        return True
    return seen < before * (1.0 - PORTAL_BUCKET_DROP_TOLERANCE)


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
    remembered = _remembered_bucket_counts()

    rows: dict[str, dict] = {}
    counts: dict[str, int] = {}
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

        try:
            parsed = _parse_rows(body, f"review_status={bucket}")
        except PortalExportSuspect as exc:
            log.error("%s", exc)
            break

        counts[bucket] = len(parsed)
        if _bucket_shrank(bucket, len(parsed), remembered.get(bucket, 0)):
            # NOT a break to the fallback: the fallback is one request for the
            # same data from the same session, and if the portal has stopped
            # recognising this bucket it will answer that one just as
            # confidently. Refuse the whole fetch instead. gather_state() turns
            # None into PortalUnavailable, which stops the run -- which is the
            # correct outcome, because the alternative is emailing everybody
            # who was in this bucket.
            log.error(
                "Bucket '%s' returned %d row(s), down from %d on the last "
                "good fetch. That is the shape of a review_status the portal "
                "has stopped recognising, and those candidates would look "
                "like they never started. Refusing this fetch.",
                bucket, len(parsed), remembered.get(bucket, 0))
            return None

        before = len(rows)
        for row in parsed:
            rows[row.get("submission_id") or f"?{len(rows)}"] = row
        log.info("  %-10s %d row(s)", bucket, len(rows) - before)
    else:
        if len(rows) < PORTAL_MIN_TOTAL_ROWS:
            # The catastrophic case the per-bucket check cannot see: every
            # bucket shrank together, or this is the first ever run against a
            # portal that answered with almost nothing.
            log.error(
                "The export came to %d row(s), below the %d-row floor. "
                "Refusing it rather than treating the missing candidates as "
                "never having started.", len(rows), PORTAL_MIN_TOTAL_ROWS)
            return None
        _remember_bucket_counts(counts)
        return list(rows.values())

    # A bucket failed, so what we have is incomplete. Discard it -- mixing it
    # with a second attempt would only hide which rows are missing.
    body = _download(session, PORTAL_SUBMISSIONS_CSV_ALL, "full export")
    if body is None:
        return None
    try:
        parsed = _parse_rows(body, "full export")
    except PortalExportSuspect as exc:
        log.error("%s", exc)
        return None

    if len(parsed) < PORTAL_MIN_TOTAL_ROWS:
        log.error(
            "The unfiltered export came to %d row(s), below the %d-row floor. "
            "Refusing it.", len(parsed), PORTAL_MIN_TOTAL_ROWS)
        return None
    return parsed


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

    # str() on BOTH sides. rec.job_id is a CSV column so it is always a
    # string; portal_job_id comes from the config table, where one unquoted
    # number would make this test False for every row -- emptying the result
    # and reporting that nobody has started this assignment. That reads as
    # "everyone is overdue", and the caller acts on it by sending mail.
    # config._validate_job_assessments() refuses the wrong type at import; this
    # makes the comparison correct even if it is ever reached another way.
    wanted = str(portal_job_id) if portal_job_id is not None else None

    emails: dict[str, str] = {}
    for rec in records:
        if wanted and str(rec.job_id) != wanted:
            continue
        status = rec.effective_status
        best = emails.get(rec.email)
        # A submitted record always wins over a merely started one.
        if best is None or _STATUS_RANK.get(status, 1) > _STATUS_RANK.get(best, 1):
            emails[rec.email] = status
    return emails
