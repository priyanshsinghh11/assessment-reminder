"""
The daily recruiting summary, posted to a Google Chat space.

    python manage.py report                 # build it and post it
    python manage.py report --dry-run       # print it instead (laptop only)
    python manage.py report --hours 24      # a fixed window; leaves the bookmark

One short post a day, so nobody has to open the dashboard to know where the
pipeline stands:

  * The last 24 hours: new applicants against the week's daily average, what
    was scored, which roles drew the most applicants, which drew none
  * The pipeline: applicants on file, scored, interviews, hires
  * The roles: most and least crowded, strongest pools, which roles' candidates
    finish the assessment and which drop off, which have gone quiet, and where
    strong candidates are waiting on a review
  * Top 5 to look at: the best-scored of today's applicants -- or, while none
    of them is scored yet, the best nobody has reviewed -- each with links to
    the evaluation, the portal submission and the Workable application

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
from backend.grading.rubric_pack import BANDS
from backend.logging_setup import setup_logging

log = logging.getLogger(__name__)

TOP = 5           # candidates listed at the bottom
PODIUM = 3        # roles named on each "most / least" line

# The score floors, from the rubric pack, so the summary's "75+" is the same
# bar the dashboard draws. "better" is the lowest band that advances.
BAR = next(b["min"] for b in BANDS if b["key"] == "better")
BEST = next(b["min"] for b in BANDS if b["key"] == "best")

# A completion rate on a handful of starts is noise. A role needs this many
# starts in the last 30 days to be ranked on how many finish.
MIN_STARTS = 10

# Google Chat caps a message's text. Comfortably under the cap, and split at a
# block boundary so a section never lands across two messages.
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


def applied_at(row: dict) -> str:
    return row.get("started_at") or row.get("submitted_at") or ""


def window_label(since: datetime, until: datetime) -> str:
    hours = (until - since).total_seconds() / 3600
    to = f"{until:%H:%M} UTC"
    if 23 <= hours <= 25:
        return f"_Last 24 hours, to {to}_"
    return (f"_Since {since:%a} {since.day} {since:%b} {since:%H:%M} UTC"
            f" ({hours:.0f} hours), to {to}_")


def podium(stats: dict[int, dict], titles: dict[int, str], value,
           fmt="{:,}", lowest=False, keep=lambda s: True) -> str:
    """'Role A (12) · Role B (9) · Role C (4)': the top -- or bottom -- three
    roles by `value`, leaving out any `keep` rejects."""
    ranked = sorted(((value(s), job_id) for job_id, s in stats.items() if keep(s)),
                    key=lambda vj: (vj[0] if lowest else -vj[0],
                                    titles.get(vj[1], "")))
    return " · ".join(f"{_plain(titles.get(job_id))} ({fmt.format(v)})"
                      for v, job_id in ranked[:PODIUM]) or "—"


def _completion(s: dict) -> float:
    return s.get("submitted_month", 0) / max(s.get("started_month", 0), 1)


def candidate_line(rank: int, row: dict, title: str, links: dict) -> str:
    head = (f"{rank}. *{_plain(row.get('candidate_name'))}* · {_plain(title)}"
            f" · *{_score(row)}*")
    parts = [f"<{links['evaluation']}|Evaluation>"]
    if links.get("submission"):
        parts.append(f"<{links['submission']}|Submission>")
    elif row.get("cv_only"):
        parts.append("No assessment (CV only)")
    parts.append(f"<{links['workable']}|Workable>" if links.get("workable")
                 else "Workable: not found")
    return head + "\n      " + " · ".join(parts)


def build_messages(*, since: datetime, until: datetime, titles: dict[int, str],
                   stats: dict[int, dict], scored: list[dict],
                   pipeline: dict[str, int], top: list[dict], top_is_today: bool,
                   links: dict[int, dict], sync_note: str = "") -> list[str]:
    """
    The summary as Chat messages -- one on any normal day.

    `stats` is store.report_role_stats() for the open roles. Headline numbers
    first, then the roles, then the five people to open first.
    """
    roles = len(stats)
    new_day = sum(s.get("new_day", 0) for s in stats.values())
    per_day = sum(s.get("new_week", 0) for s in stats.values()) / 7
    if new_day > per_day * 1.15:
        trend = f"▲ above the week's {per_day:.0f} a day"
    elif new_day < per_day * 0.85:
        trend = f"▼ below the week's {per_day:.0f} a day"
    else:
        trend = f"about the week's {per_day:.0f} a day"
    scored_open = [r for r in scored if r["job_id"] in stats]
    quiet_today = sorted(_plain(titles.get(j)) for j, s in stats.items()
                         if not s.get("new_day"))

    day = ["*Last 24 hours*",
           f"• *{new_day:,}* new applicant{'' if new_day == 1 else 's'} · {trend}",
           f"• *{len(scored_open):,}* scored · "
           f"*{sum(1 for r in scored_open if r['evaluation']['score'] >= BAR)}*"
           f" of them {BAR}+"]
    if new_day:
        day.append("• Most applicants: " + podium(
            stats, titles, lambda s: s.get("new_day", 0),
            keep=lambda s: s.get("new_day", 0) > 0))
    if quiet_today:
        day.append(f"• No new applicants ({len(quiet_today)} of {roles} roles): "
                   + ", ".join(quiet_today))

    waiting = sum(s.get("waiting_at_bar", 0) for s in stats.values())
    pipe = ["*Pipeline now*",
            f"• *{sum(s.get('on_file', 0) for s in stats.values()):,}* applicants "
            f"across {roles} open roles · "
            f"*{sum(s.get('scored', 0) for s in stats.values()):,}* scored · "
            f"*{sum(s.get('at_best', 0) for s in stats.values()):,}* at {BEST}+",
            f"• *{pipeline.get('interview', 0)}* in interview · "
            f"*{pipeline.get('interview_2', 0)}* in round 2 · "
            f"*{pipeline.get('hired', 0)}* hired",
            f"• *{waiting:,}* candidates at {BAR}+ that nobody has reviewed"]

    engaged = lambda s: s.get("started_month", 0) >= MIN_STARTS
    quiet_week = sorted(_plain(titles.get(j)) for j, s in stats.items()
                        if not s.get("new_week"))
    role_lines = [
        "*Roles*",
        "• Most crowded: " + podium(stats, titles, lambda s: s.get("on_file", 0)),
        "• Least crowded: " + podium(stats, titles, lambda s: s.get("on_file", 0),
                                     lowest=True),
        "• Busiest this week: " + podium(stats, titles,
                                         lambda s: s.get("new_week", 0)),
        f"• Strongest pools ({BEST}+): " + podium(
            stats, titles, lambda s: s.get("at_best", 0),
            keep=lambda s: s.get("at_best", 0) > 0),
        "• Most engaging (finish the assessment, 30 days): " + podium(
            stats, titles, _completion, fmt="{:.0%}", keep=engaged),
        "• Least engaging: " + podium(stats, titles, _completion, fmt="{:.0%}",
                                      lowest=True, keep=engaged),
        f"• Waiting on review ({BAR}+): " + podium(
            stats, titles, lambda s: s.get("waiting_at_bar", 0),
            keep=lambda s: s.get("waiting_at_bar", 0) > 0),
    ]
    if quiet_week:
        role_lines.append("• Gone quiet (nobody in 7 days): " + ", ".join(quiet_week))

    if top:
        heading = (f"*Top {len(top)} from the last 24 hours*" if top_is_today
                   else f"*Top {len(top)} to review* _(none of today's "
                        "applicants is scored yet, so the best unreviewed "
                        "candidate in each of the strongest roles)_")
        people = [heading] + [candidate_line(i, row, titles.get(row["job_id"], ""),
                                             links[row["_id"]])
                              for i, row in enumerate(top, 1)]
    else:
        people = ["*Top 5*", "_Nobody scored is waiting on a review._"]

    blocks = [
        f"*Recruiting summary* · {until:%a} {until.day} {until:%b %Y}\n"
        + window_label(since, until)
        + (f"\n⚠️ {sync_note}" if sync_note else ""),
        "\n".join(day), "\n".join(pipe), "\n".join(role_lines), "\n".join(people),
    ]
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

    stats = store.report_role_stats(
        roles, day=_iso(since), week=_iso(until - timedelta(days=7)),
        month=_iso(until - timedelta(days=30)), bar=BAR, best=BEST)
    for job_id in roles:                    # a role nobody has applied to yet
        stats.setdefault(job_id, {})
        titles.setdefault(job_id, f"Role {job_id}")

    scored = store.scored_between(since, until)
    pipeline: dict[str, int] = {}
    for job_id, stages in store.pipeline_counts()["by_role"].items():
        if job_id in roles:
            for stage, n in stages.items():
                pipeline[stage] = pipeline.get(stage, 0) + n

    applicants = [r for r in store.applicants_between(_iso(since), _iso(until))
                  if r["job_id"] in roles]
    top = sorted((r for r in applicants if _finished(r) and _still_in(r)),
                 key=lambda r: (-r["evaluation"]["score"], applied_at(r)))[:TOP]
    top_is_today = bool(top)
    if not top:
        top = store.top_unreviewed(roles, TOP)

    postings = _shortcodes_by_role()
    links = {row["_id"]: {"evaluation": evaluation_url(row),
                          "submission": row.get("admin_url") or "",
                          "workable": workable_url(row, postings)}
             for row in top}

    sync_note = ""
    if args.sync_status and args.sync_status != "success":
        sync_note = ("This morning's portal sync did not finish "
                     f"({args.sync_status}), so recent applicants may be missing.")

    messages = build_messages(
        since=since, until=until, titles=titles, stats=stats, scored=scored,
        pipeline=pipeline, top=top, top_is_today=top_is_today, links=links,
        sync_note=sync_note)

    log.info("Report window %s -> %s: %d applicant(s) on %d open role(s), "
             "%d scored, top %d (%s), %d Workable link(s) found, %d message(s), "
             "%d characters", _iso(since), _iso(until), len(applicants),
             len(roles), len(scored), len(top),
             "today's" if top_is_today else "unreviewed",
             sum(1 for l in links.values() if l["workable"]), len(messages),
             sum(len(m) for m in messages))

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
