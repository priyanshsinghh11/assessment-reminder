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
  * The roles: most and least crowded, busiest this week
  * Areas to focus: strong candidates nobody has reviewed, strong pools with
    nobody in interview, roles where candidates drop off the assessment, roles
    gone quiet, the grading queue, thin pools
  * What's going well: the application trend, strong new scores, roles whose
    candidates finish the assessment, interview momentum, the deepest pools
  * Top 5 not yet interviewed, for every role, in the messages after the
    summary: the best-scored candidates who have not had an interview, with
    links to the evaluation on the dashboard and the submission on the portal

Roles in config.REPORT_EXCLUDED_ROLES are left out of every line.

THE WINDOW IS "SINCE THE LAST REPORT", which on a normal day is the last 24
hours. It is bookmarked in Mongo rather than computed from the clock because
the workflow runs on GitHub's best-effort scheduler: a run that is an hour late,
or a day that is skipped, would otherwise drop the applicants in between. A
manual `--hours` run uses its own window and does not move the bookmark, so a
test post does not shorten tomorrow's.

WHO RECEIVES IT is the Chat space's membership. Nothing here lists people;
adding or removing a reader is done in Chat.

NOTHING ABOUT A CANDIDATE IS LOGGED. The top 5 names people, and this runs on
a runner of a public repository, where stdout is a world-readable page. The log
says how many, never who -- which is also why --dry-run refuses to run under
GitHub Actions.
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

from backend.config import (ASSESSMENT_JOBS, CV_ONLY_JOB_IDS,
                            GOOGLE_CHAT_WEBHOOK_URL, REPORT_DASHBOARD_URL,
                            REPORT_EXCLUDED_ROLES)
from backend.db import store
from backend.grading.rubric_pack import BANDS
from backend.logging_setup import setup_logging

log = logging.getLogger(__name__)

PODIUM = 3        # roles named on each "most / least" line
TOP = 5           # candidates in "Top 5 not yet interviewed"

# The score floors, from the rubric pack, so the summary's "75+" is the same
# bar the dashboard draws. "better" is the lowest band that advances.
BAR = next(b["min"] for b in BANDS if b["key"] == "better")
BEST = next(b["min"] for b in BANDS if b["key"] == "best")

# A completion rate on a handful of starts is noise. A role needs this many
# starts in the last 30 days to be judged on how many finish.
MIN_STARTS = 10
# Under this share finishing, the assessment is losing people; over the other,
# it is holding them.
LOW_COMPLETION = 0.35
HIGH_COMPLETION = 0.70

# A pool this deep at BEST+ with nobody in interview is a pool being left alone.
IDLE_POOL = 10
# Fewer applicants than this on file, and nobody at the bar, is a thin pool.
THIN_POOL = 50

# The day against the week's daily average: inside this band is "steady".
TREND_BAND = 0.15

# Google Chat caps a message's text. Comfortably under the cap, and split at a
# block boundary so a section never lands across two messages.
MESSAGE_LIMIT = 3800

# Older portal data than this gets a warning line on the report. The sync is
# started 90 minutes before the report, so on a normal day the data is about an
# hour old.
STALE_SYNC = timedelta(hours=6)

# A bookmark older than this is not trusted as a window start: a report that
# has not run for a week should say "the last 24 hours", not dump a week.
MAX_WINDOW = timedelta(days=7)


# A daily report posted less than this long ago counts as today's. Long enough
# to cover the morning's triggers -- none acts more than four hours after the
# 8 AM post, because report.yml stops at noon -- and short enough that a report
# posted by hand the evening before does not stop the next morning's.
REPOSTED = timedelta(hours=6)


def posted_recently() -> bool:
    """Whether today's daily report has already gone out. Several triggers
    race to post it (see report.yml), and only the first may."""
    last = store.last_report_until()
    return bool(last and datetime.now(timezone.utc) - last < REPOSTED)


def open_roles() -> set[int]:
    """Roles wired to a live posting -- the dashboard's "recruiting" flag --
    less the ones the report has been asked to leave out."""
    return ({int(job["portal_job_id"]) for job in ASSESSMENT_JOBS.values()}
            | set(CV_ONLY_JOB_IDS)) - set(REPORT_EXCLUDED_ROLES)


# ---------------------------------------------------------------------------
# Formatting -- pure, so tests/test_daily_report.py can pin it with no database
# ---------------------------------------------------------------------------

def _plain(text) -> str:
    """A name or title with Chat's markup characters taken out, so a candidate
    called *Alex* cannot bold half the message or break a link."""
    return re.sub(r"[*_~`<>|]", "", str(text or "")).strip() or "—"


def window_label(since: datetime, until: datetime) -> str:
    hours = (until - since).total_seconds() / 3600
    to = f"{until:%H:%M} UTC"
    if 23 <= hours <= 25:
        return f"_Last 24 hours, to {to}_"
    return (f"_Since {since:%a} {since.day} {since:%b} {since:%H:%M} UTC"
            f" ({hours:.0f} hours), to {to}_")


def ranked(stats: dict[int, dict], titles: dict[int, str], value,
           fmt="{:,}", lowest=False, keep=lambda s: True,
           limit=PODIUM) -> str:
    """'Role A (12), Role B (9), Role C (4)': the top -- or bottom -- roles by
    `value`, leaving out any `keep` rejects. Empty when none qualifies."""
    rows = sorted(((value(s), job_id) for job_id, s in stats.items() if keep(s)),
                  key=lambda vj: (vj[0] if lowest else -vj[0],
                                  titles.get(vj[1], "")))
    return ", ".join(f"{_plain(titles.get(job_id))} ({fmt.format(v)})"
                     for v, job_id in rows[:limit])


def _completion(s: dict) -> float:
    return s.get("submitted_month", 0) / max(s.get("started_month", 0), 1)


def _judged(s: dict) -> bool:
    return s.get("started_month", 0) >= MIN_STARTS


def _total(stats: dict[int, dict], key: str) -> int:
    return sum(s.get(key, 0) for s in stats.values())


def focus_points(stats: dict[int, dict], titles: dict[int, str],
                 new_day: int, per_day: float) -> list[str]:
    """What needs a decision or a push, most actionable first."""
    points = []

    waiting = _total(stats, "waiting_at_bar")
    if waiting:
        points.append(
            f"*Unreviewed strong candidates:* {waiting:,} at {BAR}+ that nobody "
            f"has looked at. Most in " + ranked(
                stats, titles, lambda s: s.get("waiting_at_bar", 0),
                keep=lambda s: s.get("waiting_at_bar", 0) > 0))

    idle = ranked(stats, titles, lambda s: s.get("at_best", 0),
                  fmt="{:,} at %d+" % BEST,
                  keep=lambda s: (s.get("at_best", 0) >= IDLE_POOL
                                  and not s.get("in_interview")))
    if idle:
        points.append(f"*Strong pools with nobody in interview:* {idle}")

    dropping = ranked(stats, titles, _completion, fmt="{:.0%} finish",
                      lowest=True,
                      keep=lambda s: _judged(s) and _completion(s) < LOW_COMPLETION)
    if dropping:
        points.append("*Candidates dropping off the assessment:* " + dropping)

    if per_day and new_day < per_day * (1 - TREND_BAND):
        points.append(f"*Applications down:* {new_day:,} in the last 24 hours "
                      f"against {per_day:.0f} a day this week")

    quiet = sorted(_plain(titles.get(j)) for j, s in stats.items()
                   if not s.get("new_week"))
    if quiet:
        points.append("*No applicants in 7 days:* " + ", ".join(quiet)
                      + ". Worth reposting or promoting")

    queue = _total(stats, "awaiting_score")
    if queue:
        points.append(f"*Waiting to be scored:* {queue:,} submissions. Most in "
                      + ranked(stats, titles, lambda s: s.get("awaiting_score", 0),
                               keep=lambda s: s.get("awaiting_score", 0) > 0))

    thin = ranked(stats, titles, lambda s: s.get("on_file", 0),
                  fmt="{:,} applicants", lowest=True,
                  keep=lambda s: (s.get("on_file", 0) < THIN_POOL
                                  and not s.get("at_bar")))
    if thin:
        points.append(f"*Thin pools, nobody at {BAR}+ yet:* {thin}")

    return points


def going_well_points(stats: dict[int, dict], titles: dict[int, str],
                      scored: list[dict], new_day: int,
                      per_day: float) -> list[str]:
    """What is working, with the numbers that show it."""
    points = []

    if per_day and new_day > per_day * (1 + TREND_BAND):
        points.append(f"*Applications up:* {new_day:,} in the last 24 hours "
                      f"against {per_day:.0f} a day this week")
    elif per_day and new_day >= per_day * (1 - TREND_BAND):
        points.append(f"*Applications steady:* {new_day:,} in the last 24 hours, "
                      f"in line with {per_day:.0f} a day this week")

    strong = [r for r in scored
              if r["job_id"] in stats and r["evaluation"]["score"] >= BEST]
    if strong:
        by_role: dict[int, dict] = {}
        for r in strong:
            by_role.setdefault(r["job_id"], {"n": 0})["n"] += 1
        points.append(
            f"*Strong new scores:* {len(strong)} candidate"
            f"{'' if len(strong) == 1 else 's'} scored {BEST}+ in the last 24 "
            "hours, in " + ranked(by_role, titles, lambda s: s["n"]))

    finishing = ranked(stats, titles, _completion, fmt="{:.0%} finish",
                       keep=lambda s: _judged(s) and _completion(s) >= HIGH_COMPLETION)
    if finishing:
        points.append("*Candidates finishing the assessment:* " + finishing)

    moving = _total(stats, "in_interview")
    if moving:
        hired = _total(stats, "hired")
        points.append(
            f"*Moving forward:* {moving} in interview"
            + (f" and {hired} hired" if hired else "") + ". Most in "
            + ranked(stats, titles, lambda s: s.get("in_interview", 0),
                     keep=lambda s: s.get("in_interview", 0) > 0))

    deep = ranked(stats, titles, lambda s: s.get("at_best", 0),
                  fmt="{:,} at %d+" % BEST, keep=lambda s: s.get("at_best", 0) > 0)
    if deep:
        points.append("*Deepest talent pools:* " + deep)

    return points


def evaluation_url(row: dict) -> str:
    return (f"{REPORT_DASHBOARD_URL}/evaluations.html"
            f"#role={row['job_id']}&candidate={row['_id']}")


def candidate_line(rank: int, row: dict) -> str:
    """One line a candidate: rank, name, score, then the two links."""
    score = f"{round(float(row['evaluation']['score']), 1):g}"
    links = [f"<{evaluation_url(row)}|Evaluation>"]
    if row.get("admin_url"):
        links.append(f"<{row['admin_url']}|Submission>")
    elif row.get("cv_only"):
        links.append("CV only")
    return (f"{rank}. *{_plain(row.get('candidate_name'))}* · *{score}* · "
            + " · ".join(links))


def top_blocks(top: dict[int, list[dict]], titles: dict[int, str],
               roles) -> list[str]:
    """The per-role lists, one block a role so a role never splits across
    two messages, alphabetical so a reader can find theirs."""
    blocks = [f"🏆 *Top {TOP} not yet interviewed, by role*"]
    empty = []
    for job_id in sorted(roles, key=lambda j: titles.get(j, "").lower()):
        rows = top.get(job_id) or []
        if not rows:
            empty.append(_plain(titles.get(job_id)))
            continue
        blocks.append(f"*{_plain(titles.get(job_id))}*\n" + "\n".join(
            candidate_line(i, row) for i, row in enumerate(rows, 1)))
    if empty:
        blocks.append("_Nobody scored is waiting for an interview in:_ "
                      + ", ".join(empty))
    return blocks


def build_messages(*, since: datetime, until: datetime, titles: dict[int, str],
                   stats: dict[int, dict], scored: list[dict],
                   top: Optional[dict[int, list[dict]]] = None,
                   sync_note: str = "") -> list[str]:
    """
    The summary as Chat messages -- one on any normal day.

    `stats` is store.report_role_stats() for the open roles, and `scored` the
    verdicts that landed in the window. Headline numbers first, then the roles,
    then what to act on and what is working. Then, from the second message on,
    each role's top five not yet interviewed. `top` is
    store.top_not_interviewed().
    """
    roles = len(stats)
    new_day = _total(stats, "new_day")
    per_day = _total(stats, "new_week") / 7
    scored_open = [r for r in scored if r["job_id"] in stats]
    quiet_today = sorted(_plain(titles.get(j)) for j, s in stats.items()
                         if not s.get("new_day"))

    day = ["*Last 24 hours*",
           f"• *{new_day:,}* new applicant{'' if new_day == 1 else 's'}",
           f"• *{len(scored_open):,}* scored · "
           f"*{sum(1 for r in scored_open if r['evaluation']['score'] >= BAR)}*"
           f" of them {BAR}+"]
    if new_day:
        day.append("• Most applicants: " + ranked(
            stats, titles, lambda s: s.get("new_day", 0),
            keep=lambda s: s.get("new_day", 0) > 0))
    if quiet_today:
        day.append(f"• No new applicants ({len(quiet_today)} of {roles} roles): "
                   + ", ".join(quiet_today))

    pipe = ["*Pipeline now*",
            f"• *{_total(stats, 'on_file'):,}* applicants across {roles} open "
            f"roles · *{_total(stats, 'scored'):,}* scored · "
            f"*{_total(stats, 'at_best'):,}* at {BEST}+",
            f"• *{_total(stats, 'in_interview')}* in interview · "
            f"*{_total(stats, 'hired')}* hired"]

    role_lines = [
        "*Roles*",
        "• Most crowded: " + ranked(stats, titles, lambda s: s.get("on_file", 0)),
        "• Least crowded: " + ranked(stats, titles, lambda s: s.get("on_file", 0),
                                     lowest=True),
        "• Busiest this week: " + (ranked(
            stats, titles, lambda s: s.get("new_week", 0),
            keep=lambda s: s.get("new_week", 0) > 0) or "—"),
    ]

    focus = focus_points(stats, titles, new_day, per_day)
    well = going_well_points(stats, titles, scored_open, new_day, per_day)

    blocks = [
        f"*Recruiting summary* · {until:%a} {until.day} {until:%b %Y}\n"
        + window_label(since, until)
        + (f"\n⚠️ {sync_note}" if sync_note else ""),
        "\n".join(day), "\n".join(pipe), "\n".join(role_lines),
        "🎯 *Areas to focus*\n" + ("\n".join(f"• {p}" for p in focus)
                                  or "• Nothing stands out today."),
        "✅ *What's going well*\n" + ("\n".join(f"• {p}" for p in well)
                                     or "• Nothing stands out today."),
    ]
    # The summary is the first message on its own; the lists follow it, so the
    # part a reader takes in at a glance is never pushed down by a hundred names.
    return pack(blocks) + pack(top_blocks(top or {}, titles, stats))


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report instead of posting it")
    parser.add_argument("--hours", type=float, default=0,
                        help="a fixed window of this many hours; does not move "
                             "the bookmark the scheduled run reads")
    parser.add_argument("--if-not-posted", action="store_true",
                        help="do nothing if today's daily report already went "
                             "out (the scheduled workflow's guard)")
    parser.add_argument("--posted", action="store_true",
                        help="print 'yes' if today's daily report already went "
                             "out, 'no' otherwise, and stop")
    args = parser.parse_args()

    setup_logging()

    if args.dry_run and os.environ.get("GITHUB_ACTIONS"):
        log.error("--dry-run prints candidate names, and this repository's run "
                  "pages are public. Refusing.")
        return 1
    if not (args.dry_run or args.posted) and not GOOGLE_CHAT_WEBHOOK_URL:
        log.error("GOOGLE_CHAT_WEBHOOK_URL is not set -- there is nowhere to "
                  "post. --dry-run prints the report instead.")
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        log.error("%s", exc)
        return 1

    if args.posted:
        print("yes" if posted_recently() else "no")
        return 0
    if args.if_not_posted and posted_recently():
        log.info("Today's report has already been posted; nothing to do.")
        return 0

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
    top = store.top_not_interviewed(roles, TOP)

    sync_note = ""
    synced = store.last_synced_at()
    if not synced or until - synced > STALE_SYNC:
        # The morning sync can be late, fail, or report success having
        # downloaded nothing -- a review queue that would not download is a
        # warning, not a failure -- and then every role reads "no new
        # applicants". Say so instead.
        when = (f"{synced:%a} {synced.day} {synced:%b} {synced:%H:%M} UTC"
                if synced else "never")
        sync_note = (f"Portal data was last synced {when}, so the newest "
                     "applicants are missing from these numbers.")

    messages = build_messages(since=since, until=until, titles=titles,
                              stats=stats, scored=scored, top=top,
                              sync_note=sync_note)

    log.info("Report window %s -> %s: %d new applicant(s) on %d open role(s), "
             "%d scored, %d in the top, %d message(s), %d characters",
             _iso(since), _iso(until), _total(stats, "new_day"), len(roles),
             len(scored), sum(len(rows) for rows in top.values()), len(messages),
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
