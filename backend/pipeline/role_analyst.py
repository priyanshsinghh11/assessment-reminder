"""
The role analyst: what a hiring manager should know about each of their roles,
without opening any of them.

Two halves, and the split is the design.

    read()     PURE. Takes the numbers the dashboard already holds for a role
               -- applicants, the pipeline quality read, who clears the bar,
               whose CV names a notable employer or school, where people are
               on the board -- and returns a headline and a list of findings.
               Every sentence is a count or a name off the record, so it is
               instant, free, and the same every time it is asked.

    narrate()  One model call per role. Hands the model the facts read()
               produced and asks for a few sentences a manager can act on. It
               is given nothing else and told to use nothing else: the model
               words the summary, it does not find things out.

The written summary is cached against a hash of the facts it was written from
(store.role_summaries), so a role is re-read by the model when something about
it changes and not on every page load.

WHAT THIS IS NOT. It is not a second grader and it advances nobody. "Worked at
Google" is where somebody worked, said so that a human opens the card; see the
note over the spotlights in views_evaluations.py for why that line is held.
"""

import hashlib
import json
import logging
from datetime import date, datetime

from backend.config import LLM_MODEL
from backend.grading import evaluator

log = logging.getLogger(__name__)

# Bumped when the prompt or the facts change shape, so every cached summary is
# rewritten rather than left describing a role in last month's terms.
VERSION = "1"

# How long without a new applicant before the summary says so.
STALE_DAYS = 30

# How many names a single finding spells out before it says "and N more".
NAMED = 4

GOOD, WARN, INFO = "good", "warn", "info"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _names(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _name(value) -> str:
    """A candidate's name as typed, less the stray double spaces."""
    return " ".join(str(value or "").split())


def _day(value) -> date | None:
    """The calendar day of an ISO string or a datetime, or None."""
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def notable(rows: list[dict]) -> list[dict]:
    """
    Pedigree-pool rows as the summary names them: who, and which employers and
    schools their record carries. Somebody turned down after an interview is
    left out -- the pool already drops the assessment's own rejections.
    """
    out = []
    for row in rows:
        stage = (row.get("pipeline") or {}).get("stage") or None
        if stage == "rejected":
            continue
        read = row.get("pedigree") or {}
        out.append({
            "id": row["_id"],
            "name": _name(row.get("candidate_name")
                          or row.get("candidate_email")),
            "employers": [hit["name"] for hit in read.get("employers") or []],
            "schools": [hit["name"] for hit in read.get("schools") or []],
            "stage": stage,
        })
    return out


def read(role: dict, counts: dict, pipeline: dict, recruitment: dict,
         clearing: dict | None = None, people: list[dict] | None = None,
         owners: dict | None = None, today: date | None = None) -> dict:
    """
    One role's summary: a headline, its tone, and the findings behind it.

    `recruitment` is views_evaluations._recruitment() for the role; its
    `quality` is None for an account that may not read scores, and then
    nothing here that is derived from a score is said. `clearing` is
    store.top_clearing() for the role, `people` is notable(), `owners` is
    store.role_owners() for the role.

    Findings are ordered as a manager would want them read: whether there is
    anybody good, who they are, who stands out on paper, where people are on
    the board, and what is in the way.
    """
    today = today or date.today()
    quality = recruitment.get("quality")
    clearing = clearing or {}
    people = people or []
    owners = owners or {}
    applicants = recruitment.get("applicants", 0)
    pending = counts.get("pending", 0)
    findings: list[dict] = []

    def say(tone: str, text: str) -> None:
        findings.append({"tone": tone, "text": text})

    # --- is there anybody good ------------------------------------------
    if not applicants:
        headline, tone = "No applicants yet", INFO
    elif quality is None:
        headline, tone = _plural(applicants, "applicant"), INFO
    elif quality["key"] == "none":
        headline, tone = "Not assessed yet", INFO
        say(INFO, f"Nothing has been graded yet ({quality['detail'].lower()}).")
    elif quality["key"] == "weak":
        best = (f" The best score is {quality['top']} against a bar of "
                f"{quality['bar']}." if quality.get("top") is not None else "")
        if quality["repost"]:
            headline, tone = "No good candidates — repost needed", WARN
            say(WARN, f"No good candidates: all {quality['graded']} graded "
                      f"submissions are below the bar.{best} Nothing is left "
                      "to grade and nobody is in interviews, so this role "
                      "needs reposting.")
        else:
            headline, tone = "No good candidates so far", WARN
            say(WARN, f"No good candidates so far: none of the "
                      f"{quality['graded']} graded clear the bar.{best}")
    elif quality["key"] == "thin":
        headline, tone = "Thin pipeline", WARN
        say(WARN, f"Only {_plural(quality['clear'], 'candidate')} of "
                  f"{quality['graded']} graded clear{'s' if quality['clear'] == 1 else ''} "
                  "the bar.")
    else:
        headline, tone = "Strong pipeline", GOOD
        say(GOOD, f"{quality['clear']} of {quality['graded']} graded "
                  "candidates clear the bar.")

    if quality is not None and pending and quality["key"] != "none":
        say(INFO, f"{_plural(pending, 'submission')} still waiting on grading, "
                  "so this read can change.")

    # --- who they are ----------------------------------------------------
    top = clearing.get("top") or []
    if quality is not None and top:
        say(GOOD, "Top of the queue: " + _names(
            [f"{_name(c.get('name')) or 'Unnamed'} ({round(c['score'], 1)})"
             for c in top]) + ".")

    # --- who stands out on paper ------------------------------------------
    worked = [p for p in people if p["employers"]]
    studied = [p for p in people if p["schools"] and not p["employers"]]
    for group, verb, key in ((worked, "worked at", "employers"),
                             (studied, "studied at", "schools")):
        if not group:
            continue
        lines = [f"{p['name']} {verb} {_names(p[key])}" for p in group[:NAMED]]
        more = len(group) - NAMED
        say(INFO, "; ".join(lines)
            + (f"; and {more} more with a notable "
               f"{'employer' if key == 'employers' else 'school'}"
               if more > 0 else "") + ".")

    # --- where people are on the board -----------------------------------
    hired = pipeline.get("hired", 0)
    booked = pipeline.get("interview", 0)
    second = pipeline.get("interview_2", 0)
    if hired:
        say(GOOD, f"{_plural(hired, 'candidate')} hired.")
    if booked or second:
        parts = ([f"{booked} at first interview"] if booked else []) \
            + ([f"{second} in round 2"] if second else [])
        say(INFO, f"In interviews: {_names(parts)}.")
    if (quality is not None and clearing.get("count") and not hired
            and not booked and not second):
        say(WARN, f"{_plural(clearing['count'], 'candidate')} above the bar "
                  "and nobody has been invited to interview yet.")

    # --- what is in the way ------------------------------------------------
    last = _day(recruitment.get("last_application"))
    if applicants and last and (today - last).days >= STALE_DAYS:
        say(WARN, f"No new applicant in {(today - last).days} days "
                  f"(the last was on {last:%d %b %Y}).")
    unsubmitted = counts.get("in_progress", 0)
    if unsubmitted and unsubmitted * 2 >= applicants:
        say(INFO, f"{unsubmitted} of {applicants} applicants started the "
                  "assessment and have not handed it in.")
    if applicants and "managers" in owners and not owners["managers"]:
        say(WARN, "No hiring manager is assigned to this role.")

    return {
        "id": role["_id"],
        "title": role.get("title") or f"Role {role['_id']}",
        "published": bool(role.get("published")),
        "headline": headline,
        "tone": tone,
        "applicants": applicants,
        "last_application": recruitment.get("last_application"),
        "quality": quality,
        "pipeline": {stage: n for stage, n in pipeline.items() if n},
        "findings": findings,
        "top": [{"id": c["id"], "name": _name(c.get("name")) or "Unnamed",
                 "score": round(c["score"], 1), "stage": c.get("stage") or None}
                for c in top] if quality is not None else [],
        "notable": people,
    }


# ---------------------------------------------------------------------------
# The written summary
# ---------------------------------------------------------------------------

def facts(summary: dict) -> dict:
    """
    What the model is shown, and what the cache is keyed on.

    Dates rather than "12 days ago": a relative age would change the hash every
    midnight and rewrite every summary for no new fact, and would be wrong in
    the cached text by the next morning.
    """
    quality = summary.get("quality") or {}
    return {
        "role": summary["title"],
        "headline": summary["headline"],
        "applicants": summary["applicants"],
        "last_application": str(summary.get("last_application") or "")[:10],
        "graded": quality.get("graded"),
        "clear_the_bar": quality.get("clear"),
        "bar": quality.get("bar"),
        "top_score": quality.get("top"),
        "repost_needed": quality.get("repost"),
        "on_the_board": summary["pipeline"],
        "top_candidates": [{"name": c["name"], "score": c["score"]}
                           for c in summary["top"]],
        "notable_backgrounds": [
            {"name": p["name"], "worked_at": p["employers"],
             "studied_at": p["schools"]} for p in summary["notable"][:8]],
        "findings": [f["text"] for f in summary["findings"]],
    }


def facts_hash(summary: dict) -> str:
    blob = json.dumps({"v": VERSION, **facts(summary)}, sort_keys=True,
                      default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


SYSTEM = (
    "You are a recruiting analyst at Ajaia writing for a hiring manager. You "
    "are given the facts on one open role as JSON. Write a summary of three "
    "or four plain sentences: whether there are good candidates, who stands "
    "out and why (their score, or where they worked or studied), where "
    "things stand on interviews, and the one next step you recommend.\n\n"
    "Use ONLY the facts given. Do not invent a name, a number, an employer "
    "or a school, and do not guess at anything that is missing. Scores are "
    "out of 100 and 'bar' is the score needed to advance. Where somebody "
    "worked or studied is context for a human reader, not a reason to "
    "advance them. Plain text only: no markdown, no bullet points, no "
    "heading, no relative dates such as 'last week'."
)


def narrate(summary: dict) -> str:
    """
    The model's few sentences on one role. Raises evaluator.EvaluationFailed
    or EvaluatorNotConfigured, as every other model call here does.
    """
    raw = evaluator._chat([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(facts(summary), indent=1,
                                               default=str)},
    ], max_tokens=1500)
    text = " ".join(str(raw or "").split())
    if not text:
        raise evaluator.EvaluationFailed("The model returned an empty summary.")
    return text[:1200]


def model_name() -> str:
    return LLM_MODEL
