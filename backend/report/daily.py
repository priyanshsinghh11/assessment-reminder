"""
The daily recruiting report, posted to a Google Chat space.

    python manage.py report                 # build it and post it
    python manage.py report --dry-run       # print it instead (laptop only)
    python manage.py report --hours 24      # a fixed window; leaves the bookmark

One post a day, so nobody has to open the dashboard to know what is new:

  * New applicants in the window, total and by role
  * Newly scored in the window, total and by role
  * Top 5 from the window: the best-scored of the people who applied in it
  * Top 3 unreviewed per open role, from any date

Each candidate carries three links: their evaluation on the dashboard, their
submission on the assessment portal, and their Workable application.

THE WINDOW IS "SINCE THE LAST REPORT", which on a normal day is the last 24
hours. It is bookmarked in Mongo rather than computed from the clock because
the workflow runs on GitHub's best-effort scheduler: a run that is an hour late,
or a day that is skipped, would otherwise drop the applicants in between. A
manual `--hours` run uses its own window and does not move the bookmark, so a
test post does not shorten tomorrow's.

WHO RECEIVES IT is the Chat space's membership. Nothing here lists people;
adding or removing a reader is done in Chat.

NOTHING ABOUT A CANDIDATE IS LOGGED. This runs on a runner of a public
repository, so stdout is a world-readable page. The log says how many, never
who -- which is also why --dry-run refuses to run under GitHub Actions.
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from backend.config import (ASSESSMENT_JOBS, CV_ONLY_JOBS, CV_ONLY_JOB_IDS,
                            GOOGLE_CHAT_WEBHOOK_URL, REPORT_DASHBOARD_URL,
                            WORKABLE_API_TOKEN)
from backend.db import store
from backend.logging_setup import setup_logging

log = logging.getLogger(__name__)

TOP_TODAY = 5
TOP_PER_ROLE = 3

# Google Chat caps a message's text. Comfortably under the cap, and split at a
# block boundary so a candidate's line never lands apart from their links.
MESSAGE_LIMIT = 3800

# A bookmark older than this is not trusted as a window start: a report that
# has not run for a week should say "the last 24 hours", not dump a week.
MAX_WINDOW = timedelta(days=7)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def open_roles() -> set[int]:
    """Roles wired to a live posting -- the dashboard's "recruiting" flag."""
    return ({int(job["portal_job_id"]) for job in ASSESSMENT_JOBS.values()}
            | set(CV_ONLY_JOB_IDS))


def _shortcodes_by_role() -> dict[int, set[str]]:
    """Which Workable postings feed each role, to pick the right application
    when one person applied to more than one."""
    found: dict[int, set[str]] = {}
    for shortcode, job in ASSESSMENT_JOBS.items():
        found.setdefault(int(job["portal_job_id"]), set()).add(shortcode)
    for shortcode, (_label, _slug, job_id) in CV_ONLY_JOBS.items():
        found.setdefault(job_id, set()).add(shortcode)
    return found


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

_workable_refused = False


def _refuse_workable() -> None:
    global _workable_refused
    _workable_refused = True


def evaluation_url(row: dict) -> str:
    return (f"{REPORT_DASHBOARD_URL}/evaluations.html"
            f"#role={row['job_id']}&candidate={row['_id']}")


def workable_url(row: dict, postings: dict[int, set[str]]) -> Optional[str]:
    """
    The candidate's application on Workable.

    A CV-only record came from Workable and carries it. A portal record does
    not -- the portal never sees Workable -- so it is looked up by email once
    and remembered on the submission. A miss is not remembered: the candidate
    may simply not be on Workable yet, and tomorrow's report asks again.
    """
    if row.get("profile_url"):
        return row["profile_url"]
    cached = (row.get("workable_link") or {}).get("url")
    if cached:
        return cached
    email = (row.get("candidate_email") or "").strip()
    if not (email and WORKABLE_API_TOKEN) or _workable_refused:
        return None

    from backend.scraping import workable_client
    try:
        found = workable_client.get("/candidates", {"email": email, "limit": 10})
    except requests.RequestException as exc:
        # The status only: a Workable error message can quote the request.
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        if status in (401, 403):
            # An expired token fails every lookup the same way. Say it once and
            # stop asking; the report still goes out, without Workable links.
            _refuse_workable()
            log.warning("Workable refused the token (HTTP %s) -- renew "
                        "WORKABLE_API_TOKEN. Workable links left out.", status)
        else:
            log.warning("Workable lookup failed (HTTP %s); link left out", status)
        return None
    candidates = [c for c in found.get("candidates") or [] if c.get("profile_url")]
    if not candidates:
        return None
    wanted = postings.get(row.get("job_id"), set())
    best = next((c for c in candidates
                 if (c.get("job") or {}).get("shortcode") in wanted), candidates[0])
    store.set_workable_link(row["_id"], best["profile_url"])
    return best["profile_url"]


# ---------------------------------------------------------------------------
# Formatting -- pure, so tests/test_daily_report.py can pin it with no database
# ---------------------------------------------------------------------------

def _plain(text) -> str:
    """A name or title with Chat's markup characters taken out, so a candidate
    called *Alex* cannot bold half the message or break a link."""
    return re.sub(r"[*_~`<>|]", "", str(text or "")).strip() or "—"


def _score(row: dict) -> str:
    return f"{round(float(row['evaluation']['score']), 1):g}"


def _day(value) -> str:
    """'3 Nov' from a portal ISO string, or '' when there is none."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{when.day} {when:%b}"


def applied_at(row: dict) -> str:
    return row.get("started_at") or row.get("submitted_at") or ""


def candidate_block(rank: int, row: dict, title: str, links: dict,
                    show_role: bool) -> str:
    head = f"{rank}. *{_plain(row.get('candidate_name'))}*"
    if show_role:
        head += f" · {_plain(title)}"
    head += f" · Score *{_score(row)}*"
    applied = _day(applied_at(row))
    if applied and not show_role:
        head += f" · applied {applied}"

    parts = [f"<{links['evaluation']}|Evaluation>"]
    if links.get("submission"):
        parts.append(f"<{links['submission']}|Submission>")
    elif row.get("cv_only"):
        parts.append("No assessment (CV only)")
    parts.append(f"<{links['workable']}|Workable>" if links.get("workable")
                 else "Workable: not found")
    return head + "\n      " + " · ".join(parts)


def count_section(heading: str, rows: list[dict], titles: dict[int, str],
                  roles: set[int], none_text: str) -> str:
    by_role: dict[int, int] = {}
    for row in rows:
        by_role[row["job_id"]] = by_role.get(row["job_id"], 0) + 1
    lines = [f"*{heading}: {len(rows)}*"]
    for job_id, n in sorted(by_role.items(),
                            key=lambda kv: (-kv[1], titles.get(kv[0], ""))):
        lines.append(f"• {_plain(titles.get(job_id))}: {n}")
    quiet = sorted(_plain(titles.get(j)) for j in roles if j not in by_role)
    if quiet:
        lines.append(f"_{none_text}:_ {', '.join(quiet)}")
    return "\n".join(lines)


def window_label(since: datetime, until: datetime) -> str:
    hours = (until - since).total_seconds() / 3600
    to = f"{until:%H:%M} UTC"
    if 23 <= hours <= 25:
        return f"_Last 24 hours, to {to}_"
    return (f"_Since {since:%a} {since.day} {since:%b} {since:%H:%M} UTC"
            f" ({hours:.0f} hours), to {to}_")


def build_messages(*, since: datetime, until: datetime, titles: dict[int, str],
                   roles: set[int], applicants: list[dict], scored: list[dict],
                   top_today: list[dict], unreviewed: dict[int, list[dict]],
                   links: dict[int, dict], sync_note: str = "") -> list[str]:
    """
    The report as Chat messages, each under MESSAGE_LIMIT.

    Counts first, then today's best, then the per-role lists -- so the part a
    reader can take in at a glance is the top of the first message.
    """
    blocks = [
        f"*Daily recruiting report* · {until:%a} {until.day} {until:%b %Y}\n"
        + window_label(since, until)
        + (f"\n⚠️ {sync_note}" if sync_note else ""),
        count_section("New applicants", applicants, titles, roles,
                      "No new applicants for"),
        count_section("Newly scored", scored, titles, roles,
                      "None newly scored for"),
    ]

    if top_today:
        blocks.append(f"*Top {len(top_today)} from today*\n" + "\n".join(
            candidate_block(i, row, titles.get(row["job_id"], ""),
                            links[row["_id"]], show_role=True)
            for i, row in enumerate(top_today, 1)))
    else:
        blocks.append("*Top 5 from today*\n_No applicant from this window "
                      "has been scored yet._")

    blocks.append(f"*Top {TOP_PER_ROLE} unreviewed per role*")
    empty = []
    for job_id in sorted(roles, key=lambda j: titles.get(j, "").lower()):
        rows = unreviewed.get(job_id) or []
        if not rows:
            empty.append(_plain(titles.get(job_id)))
            continue
        blocks.append(f"*{_plain(titles.get(job_id))}*\n" + "\n".join(
            candidate_block(i, row, "", links[row["_id"]], show_role=False)
            for i, row in enumerate(rows, 1)))
    if empty:
        blocks.append("_No unreviewed scored candidates for:_ "
                      + ", ".join(empty))

    return pack(blocks)


def pack(blocks: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Join blocks into as few messages as fit. A block is never split."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        joined = f"{current}\n\n{block}" if current else block
        if current and len(joined) > limit:
            messages.append(current)
            current = block
        else:
            current = joined
    if current:
        messages.append(current)
    return messages


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def post(messages: list[str]) -> None:
    """
    Send each message to the space, in order.

    Errors are reported by status code only. The webhook URL carries the
    space's key and token, and requests puts the URL in its exception text --
    logging the exception would publish the credential on the run page.
    """
    for i, text in enumerate(messages):
        if i:
            time.sleep(1.2)   # Chat allows about one message a second per space
        for attempt in range(1, 4):
            try:
                resp = requests.post(GOOGLE_CHAT_WEBHOOK_URL, json={"text": text},
                                     timeout=30)
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Could not reach Google Chat ({type(exc).__name__})") from None
            if resp.status_code == 429 and attempt < 3:
                time.sleep(5 * attempt)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Google Chat refused message {i + 1} of {len(messages)} "
                    f"(HTTP {resp.status_code})")
            break


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _iso(when: datetime) -> str:
    """The portal's own timestamp format, so the window compares as text."""
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _finished(row: dict) -> bool:
    evaluation = row.get("evaluation") or {}
    return (isinstance(evaluation.get("score"), (int, float))
            and evaluation.get("grid_complete") is not False
            and evaluation.get("score_provisional") is not True)


def _still_in(row: dict) -> bool:
    return ((row.get("decision") or {}).get("status") != "rejected"
            and (row.get("pipeline") or {}).get("stage") != "rejected")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report instead of posting it")
    parser.add_argument("--hours", type=float, default=0,
                        help="a fixed window of this many hours; does not move "
                             "the bookmark the scheduled run reads")
    parser.add_argument("--sync-status", default="",
                        help="the portal sync's conclusion, from the workflow; "
                             "anything but 'success' adds a warning line")
    args = parser.parse_args()

    setup_logging()

    if args.dry_run and os.environ.get("GITHUB_ACTIONS"):
        log.error("--dry-run prints candidate names, and this repository's run "
                  "pages are public. Refusing.")
        return 1
    if not args.dry_run and not GOOGLE_CHAT_WEBHOOK_URL:
        log.error("GOOGLE_CHAT_WEBHOOK_URL is not set -- there is nowhere to "
                  "post. --dry-run prints the report instead.")
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        log.error("%s", exc)
        return 1

    until = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if args.hours:
        since = until - timedelta(hours=args.hours)
    else:
        last = store.last_report_until()
        recent = last and until - MAX_WINDOW <= last <= until - timedelta(hours=1)
        since = last if recent else until - timedelta(hours=24)

    roles = open_roles()
    titles = {r["_id"]: r.get("title") or f"Role {r['_id']}"
              for r in store.role_index()}

    applicants = store.applicants_between(_iso(since), _iso(until))
    scored = store.scored_between(since, until)
    unreviewed = store.top_unreviewed(roles, TOP_PER_ROLE)
    top_today = sorted((r for r in applicants if _finished(r) and _still_in(r)),
                       key=lambda r: (-r["evaluation"]["score"], applied_at(r))
                       )[:TOP_TODAY]

    # A role off the roles collection still needs a name on its line.
    for row in applicants + scored:
        titles.setdefault(row["job_id"], row.get("job_title") or f"Role {row['job_id']}")

    postings = _shortcodes_by_role()
    listed = {row["_id"]: row for row in top_today}
    for rows in unreviewed.values():
        listed.update((row["_id"], row) for row in rows)
    links = {sid: {"evaluation": evaluation_url(row),
                   "submission": row.get("admin_url") or "",
                   "workable": workable_url(row, postings)}
             for sid, row in listed.items()}

    sync_note = ""
    if args.sync_status and args.sync_status != "success":
        sync_note = ("This morning's portal sync did not finish "
                     f"({args.sync_status}), so recent applicants may be missing.")

    messages = build_messages(
        since=since, until=until, titles=titles, roles=roles,
        applicants=applicants, scored=scored, top_today=top_today,
        unreviewed=unreviewed, links=links, sync_note=sync_note)

    log.info("Report window %s -> %s: %d applicant(s), %d scored, %d in today's "
             "top, %d listed across %d open role(s), %d Workable link(s) found, "
             "%d message(s)", _iso(since), _iso(until), len(applicants),
             len(scored), len(top_today), len(listed), len(roles),
             sum(1 for l in links.values() if l["workable"]), len(messages))

    if args.dry_run:
        print("\n\n----- next message -----\n\n".join(messages))
        return 0

    try:
        post(messages)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    if not args.hours:
        store.record_report(until, len(messages))
    log.info("Posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
