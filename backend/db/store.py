"""
MongoDB store for portal roles, submissions and AI evaluations.

The portal's CSV export is the source of truth for submissions, and the
`/admin/jobs` pages are the source of truth for roles and their live
assessment text. Both are re-crawled on every ingest, so everything the portal
owns is overwritten each run.

Everything *we* own -- the accept/reject decision and the AI evaluation -- is
written to separate sub-documents that ingest never touches. That split is what
makes re-ingesting safe: a reviewer's decision, or an expensive grading run,
survives the next crawl.
"""

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument, UpdateOne
from pymongo.errors import PyMongoError

from backend.config import (CV_ONLY_ID_BASE, MONGO_URI, MONGO_DB,
                    REVIEW_LINK_DAYS, ROLE_TITLES,
                    SHORTLIST_REQUIRE_COMPLETE_GRID)
from backend.utils import aware as _aware

log = logging.getLogger(__name__)

_client: Optional[MongoClient] = None


class MongoUnavailable(RuntimeError):
    """Raised when MongoDB cannot be reached, so callers can report it cleanly."""


def get_db():
    """
    Return the database handle, connecting on first use.

    The client is cached at module level: PyMongo's client is a connection
    pool, and building a new one per request would leak sockets.
    """
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client[MONGO_DB]


def ping() -> None:
    """Raise MongoUnavailable unless the server answers."""
    try:
        get_db().command("ping")
    except PyMongoError as exc:
        raise MongoUnavailable(
            f"Cannot reach MongoDB at {MONGO_URI}: {exc}"
        ) from exc


def ensure_indexes() -> None:
    """Create the indexes the dashboard queries rely on. Idempotent."""
    db = get_db()
    db.submissions.create_index([("job_id", ASCENDING)])
    db.submissions.create_index([("candidate_email", ASCENDING)])
    db.submissions.create_index([("decision.status", ASCENDING)])
    db.submissions.create_index([("submission_status", ASCENDING)])
    # CV-only records are keyed on the Workable candidate id rather than on a
    # portal submission number, so every upsert in that path looks one up.
    # Sparse: the field exists on a few dozen documents out of eight thousand,
    # and a full index would be almost entirely nulls.
    db.submissions.create_index([("workable_candidate_id", ASCENDING)],
                                sparse=True)
    # The dashboard's default ordering: best score first within a role.
    db.submissions.create_index(
        [("job_id", ASCENDING), ("evaluation.score", DESCENDING)]
    )
    db.submissions.create_index([("submitted_at", DESCENDING)])
    # The pipeline board reads one stage at a time, across every role.
    db.submissions.create_index([("pipeline.stage", ASCENDING)])
    db.roles.create_index([("slug", ASCENDING)])
    # Review links are looked up by token on every request the manager makes,
    # and listed per role by the dashboard.
    db.review_links.create_index([("job_id", ASCENDING)])
    db.review_links.create_index([("manager.email", ASCENDING)])
    # Every request a hiring manager makes asks which roles carry their
    # address, so this one is on the hot path of the access check itself.
    db.roles.create_index([("hiring_managers.email", ASCENDING)])
    # Opt-outs. The _id IS the address, so the lookup every candidate send makes
    # is already served by the primary key -- this one is for reading the list
    # back in the order people left it.
    db.unsubscribes.create_index([("unsubscribed_at", DESCENDING)])
    # The reminder dedupe log. _id is state_key(), so the per-candidate check is
    # served by the primary key; these two are for the dashboard's own reads.
    db.reminders.create_index([("email", ASCENDING)])
    db.reminders.create_index([("last_reminder_at", DESCENDING)])
    # The rejection ledger, keyed by address for the same reason. These three
    # are the page's own ordering and its two filters; the duplicate check that
    # runs per send is the primary key again.
    db.rejections.create_index([("rejected_at", DESCENDING)])
    db.rejections.create_index([("job_id", ASCENDING)])
    db.rejections.create_index([("status", ASCENDING)])


def get_app_secret() -> str:
    """
    A stable server-side signing key, minted once and kept in Mongo.

    Used to sign unsubscribe links. It lives here rather than in .env for the
    same reason the reminder state should: a container has no durable disk, and
    a key regenerated on every deploy would silently invalidate the unsubscribe
    link in every email already sent -- turning "click here to stop" into a
    404 for anyone who kept the message.

    Not an env var, because a secret nobody set is a secret nobody rotates, and
    this one has no rotation story worth the ceremony. Set APP_SECRET in the
    environment to override it if you would rather hold the key yourself; that
    takes precedence and nothing is written.

    upsert with $setOnInsert, so two processes racing on first start agree on
    one value rather than each overwriting the other's.
    """
    override = os.environ.get("APP_SECRET", "").strip()
    if override:
        return override

    db = get_db()
    db.settings.update_one(
        {"_id": "app_secret"},
        {"$setOnInsert": {"value": secrets.token_urlsafe(48),
                          "created_at": now()}},
        upsert=True,
    )
    return db.settings.find_one({"_id": "app_secret"})["value"]


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def upsert_roles(roles: list[dict]) -> int:
    """
    Write crawled roles. `roles` are dicts from portal_crawler.fetch_roles().

    $set (not replace) so that a role's stored assessment text survives a
    crawl that could not reach the assignment page for it.
    """
    if not roles:
        return 0
    ops = [
        UpdateOne(
            {"_id": r["job_id"]},
            {"$set": {**r, "crawled_at": now()}},
            upsert=True,
        )
        for r in roles
    ]
    result = get_db().roles.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def set_role_assessment(job_id: int, assessment: dict) -> None:
    """Attach the live assignment (markdown + metadata) to a role."""
    get_db().roles.update_one(
        {"_id": job_id},
        {"$set": {"assessment": {**assessment, "fetched_at": now()}}},
    )


def _titled(role: Optional[dict]) -> Optional[dict]:
    """
    Swap in the display name for a role whose portal title is not what we want
    on a screen. See config.ROLE_TITLES.

    Applied on read rather than on write, and this is the whole reason it
    works: `upsert_roles` $sets `title` from the crawler on every crawl, so a
    stored title edited by hand survives exactly until the next
    `ingest.py --roles-only` and then silently reverts. Overriding here cannot
    be clobbered, and it keeps the crawl an honest record of what the portal
    says.

    The portal's own name is kept as `portal_title` on every role, listed or
    not, so nothing is lost and a reader who needs to find the role in the
    portal admin still has the string it is filed under there.
    """
    if not role:
        return role
    role["portal_title"] = role.get("title")
    override = ROLE_TITLES.get(role.get("slug"))
    if override:
        role["title"] = override
    return role


def get_roles(job_ids: Optional[set[int]] = None) -> list[dict]:
    """
    All roles, published first, then by title.

    `job_ids` narrows to a set -- how a hiring manager's dashboard is built.
    It is a filter in the query rather than a list comprehension afterwards so
    that "which roles exist" and "which roles you may see" are the same
    question and cannot answer differently. None means no narrowing; an EMPTY
    set means no roles, which is the right answer for an account that owns
    none, not an invitation to show them all.
    """
    query: dict = {} if job_ids is None else {"_id": {"$in": sorted(job_ids)}}
    rows = get_db().roles.find(query).sort([("published", DESCENDING), ("title", ASCENDING)])
    # Sorted by the portal's title in the query, then re-sorted here: a role
    # renamed to "AI Strategist" has to land under A where a reader looks for
    # it, not under "Ajaia ..." where the database still has it.
    return sorted((_titled(r) for r in rows),
                  key=lambda r: (not r.get("published"), (r.get("title") or "").lower()))


def get_role(job_id: int) -> Optional[dict]:
    return _titled(get_db().roles.find_one({"_id": job_id}))


# ---------------------------------------------------------------------------
# Hiring managers
# ---------------------------------------------------------------------------
#
# Who owns the seat. Stored on the role as `hiring_managers`, a list of
# {name, email, title, cal_link}, and written only by set_role_managers() and
# set_manager_cal_link().
#
# Safe under ingest for the same reason `decision` and `evaluation` are safe on
# a submission: upsert_roles() $sets the keys portal_crawler produced, and
# `hiring_managers` is not one of them. Do not add it to the crawler's output.

def clean_cal_link(value) -> str:
    """
    Normalise a manager's booking link, or "" if there is nothing usable.

    Managers type this into the dashboard themselves, and they type it the way
    they say it: "cal.com/anita/interview", with no scheme. A bare host in an
    href is read by the mail client as a relative path, so the candidate would
    click it and land on a 404 inside our own domain -- the one failure mode
    this whole email exists to avoid. So a missing scheme is added rather than
    rejected, and anything that is not http(s) is dropped, because a `javascript:`
    or `mailto:` link in a candidate-facing button is not a booking page.
    """
    link = str(value or "").strip()
    if not link:
        return ""

    # A scheme that is present is checked, never repaired. Testing for "://"
    # alone is not enough: "javascript:alert(1)" has a scheme and no slashes,
    # so a bare prepend would turn a hostile string into a plausible-looking
    # https URL and store it as a booking page.
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", link)
    if scheme:
        if scheme.group(1).lower() not in ("http", "https"):
            return ""
    else:
        link = "https://" + link.lstrip("/")
    # A space cannot appear in a URL, and one pasted in the middle of a link is
    # a copy that picked up surrounding text -- keeping it would send a link
    # that 404s.
    return "" if " " in link else link


def _clean_manager(entry: dict) -> Optional[dict]:
    """
    Normalise one {name, email, title, cal_link} entry, or None if it has no
    address.

    The address is the only required field -- it is what the send actually
    needs, and it doubles as the identity, so it is lowercased and used for
    de-duplication. A manager with no name falls back to the local part of
    their address rather than an empty greeting.

    `cal_link` is the manager's own booking page, and it is theirs rather than
    the role's: one manager owning three seats books all three in the same
    calendar, and a link stored per role would have to be typed three times and
    would rot in two of them the day they move it.
    """
    email = str(entry.get("email") or "").strip().lower()
    if "@" not in email:
        return None
    name = str(entry.get("name") or "").strip()
    return {
        "name": name or email.split("@")[0].replace(".", " ").title(),
        "email": email,
        "title": str(entry.get("title") or "").strip(),
        "cal_link": clean_cal_link(entry.get("cal_link")),
    }


def set_role_managers(job_id: int, managers: list[dict]) -> list[dict]:
    """
    Replace a role's hiring managers with `managers`, returning what was stored.

    A whole-list replace rather than add/remove calls: the editor sends the
    list it is showing, so a save can never leave the role holding a manager
    the recruiter had just deleted on screen.

    De-duplicated by address, first entry winning, because the same person
    added twice would be mailed twice by one send.
    """
    seen: set[str] = set()
    cleaned = []
    for entry in managers or []:
        manager = _clean_manager(entry)
        if manager is None or manager["email"] in seen:
            continue
        seen.add(manager["email"])
        cleaned.append(manager)

    get_db().roles.update_one(
        {"_id": job_id},
        {"$set": {"hiring_managers": cleaned, "hiring_managers_at": now()}},
    )
    return cleaned


def get_role_managers(job_id: int) -> list[dict]:
    role = get_db().roles.find_one({"_id": job_id}, {"hiring_managers": 1})
    return (role or {}).get("hiring_managers") or []


def set_manager_cal_link(email: str, cal_link: str) -> int:
    """
    Set one manager's booking link on every role they own. Returns roles touched.

    Deliberately account-wide rather than per role. A manager has one calendar,
    and they type this link once from whichever role they happened to be
    looking at -- if it only landed on that role, the same person would send a
    dead link from the next seat they hire for and never know why nobody booked.

    Positional-filter update rather than read-modify-write, so a recruiter
    editing the same role's manager list at the same moment cannot lose it.
    """
    address = str(email or "").strip().lower()
    if "@" not in address:
        return 0
    result = get_db().roles.update_many(
        {"hiring_managers.email": address},
        {"$set": {"hiring_managers.$[m].cal_link": clean_cal_link(cal_link)}},
        array_filters=[{"m.email": address}],
    )
    return result.modified_count


def known_managers() -> list[dict]:
    """
    Every manager on record, across all roles, for the editor's suggestions.

    One person owning three seats should be typed once and picked twice after
    that -- and a name typed slightly differently on each role is how a
    directory rots.
    """
    people: dict[str, dict] = {}
    for role in get_db().roles.find({"hiring_managers": {"$nin": [None, []]}},
                                    {"hiring_managers": 1, "title": 1}):
        for manager in role.get("hiring_managers") or []:
            entry = people.setdefault(manager["email"],
                                      {**manager, "roles": []})
            entry["roles"].append(role.get("title") or str(role["_id"]))
    return sorted(people.values(), key=lambda m: m["name"].lower())


# ---------------------------------------------------------------------------
# Who owns which role
#
# The two queries the dashboard's access rule is built on. A hiring manager
# sees a role because their address is on it -- there is no separate list of
# permissions anywhere, which is what stops access and ownership from drifting
# apart. See auth.visible_job_ids().
# ---------------------------------------------------------------------------

def job_ids_for_manager(email: str) -> set[int]:
    """
    Every job id whose hiring-manager list carries `email`.

    Answered from the roles collection on every request rather than cached on
    the account: taking somebody off a role has to revoke their access in that
    same click, and a cache is a window in which it does not.
    """
    address = str(email or "").strip().lower()
    if "@" not in address:
        return set()
    return {
        role["_id"] for role in
        get_db().roles.find({"hiring_managers.email": address}, {"_id": 1})
    }


def roles_by_manager() -> dict[str, list[dict]]:
    """
    {manager email: [{id, title}, ...]} across every role, for the accounts
    screen -- so an admin adding an account can see what it will be able to
    open before they create it, and can tell an empty account from a real one.
    """
    owned: dict[str, list[dict]] = {}
    for role in get_db().roles.find({"hiring_managers": {"$nin": [None, []]}},
                                    {"hiring_managers": 1, "title": 1}):
        for manager in role.get("hiring_managers") or []:
            owned.setdefault(manager["email"], []).append(
                {"id": role["_id"], "title": role.get("title") or str(role["_id"])})
    return owned


def set_manager_roles(email: str, job_ids, name: str = "",
                      title: str = "") -> dict:
    """
    Make `email` a hiring manager on exactly `job_ids`, and on nothing else.

    The other direction of set_role_managers(): that one edits one role's list
    of people, this one edits one person's list of roles. They write THE SAME
    FIELD -- `hiring_managers` on each role -- which is the point. A separate
    per-account permissions store would be a second answer to "who owns this
    seat", and the day the two disagreed, one of them would be deciding who
    gets the shortlist and the other who can open the dashboard.

    Per-element $push and $pull rather than a whole-list replace, because this
    is editing one entry in a list that belongs to somebody else's screen too:
    a recruiter halfway through adding two managers to a role must not lose
    them because an admin assigned a third person from the accounts panel.

    Their name, title and booking link are carried across from wherever they
    already appear. One person owning three seats has one calendar, and a new
    assignment that arrived with an empty cal_link would silently break the
    interview invitations for that role only.
    """
    address = str(email or "").strip().lower()
    if "@" not in address:
        raise ValueError(f"Not an email address: {email!r}")

    db = get_db()
    wanted = {int(job_id) for job_id in job_ids or []}

    # A job id that is not a role is refused rather than written: it would sit
    # in nobody's list, show up on no screen, and grant nothing -- an
    # assignment that silently did not happen.
    real = {role["_id"] for role in
            db.roles.find({"_id": {"$in": sorted(wanted)}}, {"_id": 1})}
    unknown = sorted(wanted - real)
    wanted = real

    current = job_ids_for_manager(address)
    add, drop = wanted - current, current - wanted

    entry = {"name": str(name or "").strip(), "email": address,
             "title": str(title or "").strip(), "cal_link": ""}
    for role in db.roles.find({"hiring_managers.email": address},
                              {"hiring_managers": 1}):
        for manager in role.get("hiring_managers") or []:
            if manager["email"] == address:
                entry["cal_link"] = manager.get("cal_link") or ""
                entry["name"] = entry["name"] or manager.get("name") or ""
                entry["title"] = entry["title"] or manager.get("title") or ""
                break
        if entry["cal_link"]:
            break
    entry["name"] = entry["name"] or address.split("@")[0].replace(".", " ").title()

    for job_id in add:
        # The $ne guard makes this idempotent: two clicks cannot put the same
        # person on a role twice, which would mail them the shortlist twice.
        db.roles.update_one(
            {"_id": job_id, "hiring_managers.email": {"$ne": address}},
            {"$push": {"hiring_managers": entry},
             "$set": {"hiring_managers_at": now()}},
        )
    for job_id in drop:
        db.roles.update_one(
            {"_id": job_id},
            {"$pull": {"hiring_managers": {"email": address}},
             "$set": {"hiring_managers_at": now()}},
        )

    return {
        "email": address,
        "added": sorted(add),
        "removed": sorted(drop),
        "unknown": unknown,
        "roles": roles_by_manager().get(address, []),
    }


def roles_missing_managers() -> list[dict]:
    """
    Roles that have candidates but nobody to send them to.

    The one piece of shortlist state worth surfacing unprompted: a role can be
    fully graded and still be a dead end, and that is invisible from a card
    that only counts submissions.
    """
    with_subs = set(get_db().submissions.distinct("job_id"))
    return [
        {"id": r["_id"], "title": r.get("title")}
        for r in get_db().roles.find(
            {"hiring_managers": {"$in": [None, []]}}, {"title": 1})
        if r["_id"] in with_subs
    ]


def tier_filter(tier: Optional[str], default_tier: Optional[str] = None) -> dict:
    """
    The query fragment that narrows a role to one tier of its rubric.

    Empty for `tier=None`, which is what every role in the system used to mean
    and what all but one still does: no filter, everybody.

    The default tier carries the unresolved with it, and that is the whole
    subtlety here. A submission has no `rubric_tier` until the resolver has
    matched it to a posting, and grading marks those against the default grid
    -- so the senior dashboard has to show them or they would be graded on a
    standard whose page never lists them. Pass `default_tier` (from
    `rubric_pack.default_tier_for_slug`) and the filter widens to include them
    when the tier asked for IS the default, and stays narrow when it is not.

    `tier="unresolved"` is the third thing you can ask for: only the ones
    nobody has matched yet, which is how a reviewer sees what a resolver run
    has left to do.
    """
    if not tier:
        return {}
    if tier == "unresolved":
        return {"rubric_tier": {"$exists": False}}
    if default_tier and tier == default_tier:
        return {"$or": [{"rubric_tier.tier": tier},
                        {"rubric_tier": {"$exists": False}}]}
    return {"rubric_tier.tier": tier}


def role_tier_counts(job_ids: Optional[set[int]] = None) -> dict[int, dict]:
    """
    Per-role, per-tier tallies: {job_id: {tier: {status: n, "total": n}}}.

    Separate from `role_counts` rather than nested inside it, because that
    function's totals are computed by summing its own buckets and a nested dict
    would land in the sum. Unresolved submissions are counted under
    "unresolved" here rather than folded into the default tier -- the caller
    decides whether to add them, and the dashboard says how many there are.
    """
    match: dict = {}
    if job_ids is not None:
        match["job_id"] = {"$in": sorted(job_ids)}

    stages: list[dict] = []
    if match:
        stages.append({"$match": match})
    stages.append({
        "$group": {
            "_id": {"job_id": "$job_id",
                    "tier": "$rubric_tier.tier",
                    "status": "$decision.status"},
            "n": {"$sum": 1},
        }
    })

    counts: dict[int, dict] = {}
    for row in get_db().submissions.aggregate(stages):
        key = row["_id"]
        tier = key.get("tier") or "unresolved"
        status = key.get("status") or "unknown"
        bucket = counts.setdefault(key["job_id"], {}).setdefault(tier, {})
        bucket[status] = bucket.get(status, 0) + row["n"]
    for role in counts.values():
        for bucket in role.values():
            bucket["total"] = sum(bucket.values())
    return counts


# A verdict whose grid the model only part-filled. Written as "not false" and
# "not true" rather than the positive form on purpose: it has to match verdicts
# from before either field existed, and a positive match would drop every one
# of them out of every shortlist. `grid_complete` has been stored since the
# grids went in; `score_provisional` arrived with the coverage floor. Either one
# saying the grid was short is enough.
PARTIAL_GRID = {
    "$or": [
        {"evaluation.grid_complete": False},
        {"evaluation.score_provisional": True},
    ]
}
COMPLETE_GRID = {
    "evaluation.grid_complete": {"$ne": False},
    "evaluation.score_provisional": {"$ne": True},
}


def top_candidates(job_id: int, limit: int = 20, tier: Optional[str] = None,
                   default_tier: Optional[str] = None) -> list[dict]:
    """
    A role's best-scoring candidates, highest first -- the shortlist.

    Restricted to submissions that were actually marked (`evaluation.score` is
    a number). A pending or artefact-rejected row has no standing to be on a
    hiring manager's desk, and Mongo sorts missing fields as lower than any
    number, so without this filter a thin role would pad its top 20 with
    ungraded people who would read as ranked.

    Restricted, too, to verdicts whose grid the model actually finished. A
    partial grid is renormalised to 100 by the scorer, so it sorts like any
    other score and can sort ABOVE every real one -- a single row marked 5
    renormalises to exactly 100.0. Rank is a claim that these people were
    compared against each other; a submission judged on a third of the rubric
    was not compared with anything, and the manager's page shows no scores at
    all, so nothing downstream would ever give the reader a reason to doubt the
    position. They are held out here and reported by `held_back()` so a
    recruiter re-grades them rather than losing them.
    (SHORTLIST_REQUIRE_COMPLETE_GRID=false ranks them anyway; the rows still
    carry `score_provisional` so every surface can say so.)

    Anyone already moved along the board is left out too: a candidate who is
    booked, hired, or was turned down after an interview is not news to the
    manager who made that call, and re-sending them reads as an ask.

    Answer text and CV text are projected out -- the shortlist needs the links,
    not the megabytes behind them.
    """
    query = {
        **_rankable(job_id, tier, default_tier),
        **(COMPLETE_GRID if SHORTLIST_REQUIRE_COMPLETE_GRID else {}),
    }
    cursor = get_db().submissions.find(
        query, {"submission_markdown": 0, "resume_text": 0}
    ).sort([("evaluation.score", DESCENDING), ("submitted_at", ASCENDING)])
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def _rankable(job_id: int, tier: Optional[str],
              default_tier: Optional[str]) -> dict:
    """Everyone a shortlist could draw on, before the partial-grid rule."""
    return {
        "job_id": job_id,
        "evaluation.score": {"$type": "number"},
        "decision.status": {"$ne": "rejected"},
        "pipeline.stage": {"$in": [None, ""]},
        # A shortlist is a ranking, and a ranking is only meaningful among
        # people marked against the same anchors. Where one assignment is
        # marked at two tiers, the two lists go to the manager separately.
        **tier_filter(tier, default_tier),
    }


def held_back(job_id: int, tier: Optional[str] = None,
              default_tier: Optional[str] = None) -> list[dict]:
    """
    Candidates a shortlist would have considered but for a part-filled grid.

    The other half of the rule in `top_candidates`. Dropping someone silently
    is its own version of the bug it fixes: the shortlist would go out looking
    complete while a candidate nobody re-graded sat outside it. These come back
    so the send path can say how many there are and who, and so the recruiter
    has a re-grade list rather than a suspicion.

    Returned regardless of SHORTLIST_REQUIRE_COMPLETE_GRID -- with the rule off
    they are on the list AND on this one, which is what a recruiter checking
    the send needs to see.
    """
    cursor = get_db().submissions.find(
        {**_rankable(job_id, tier, default_tier), **PARTIAL_GRID},
        {"submission_markdown": 0, "resume_text": 0},
    ).sort([("evaluation.score", DESCENDING), ("submitted_at", ASCENDING)])
    return list(cursor)


def record_shortlist_send(job_id: int, recipients: list[str], count: int,
                          submission_ids: list[int]) -> None:
    """
    Note that a shortlist went out, so the card can say when and to whom.

    Kept as a growing history rather than a single last-sent stamp: "we already
    sent this role's twenty on the 12th" is the question a recruiter asks
    before clicking send, and one overwritten field cannot answer it after the
    second batch.
    """
    entry = {
        "at": now(),
        "to": recipients,
        "count": count,
        "submission_ids": submission_ids,
    }
    get_db().roles.update_one(
        {"_id": job_id},
        {"$set": {"shortlist_last": entry}, "$push": {"shortlist_sends": entry}},
    )


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

# Fields the portal owns. Listed explicitly so that a new column appearing in
# the CSV export cannot silently overwrite one of our own sub-documents.
PORTAL_FIELDS = (
    "job_id", "job_title", "assignment_name", "candidate_name",
    "candidate_email", "resume_link", "video_link", "submission_status",
    "review_status", "screener_rating", "started_at", "submitted_at",
    "reviewed_at", "auto_submitted", "admin_url", "submission_markdown",
)

# Extracted resume text is ours, not the portal's, so it must never appear in
# PORTAL_FIELDS above. upsert_submissions() builds its document with
# {k: rec.get(k) for k in PORTAL_FIELDS}, and the CSV export has no resume_text
# column -- listing it there would set it to None on every ingest and wipe the
# whole backfill. It is written by set_resume() instead, with its own targeted
# $set, for the same reason set_evaluation() exists.
RESUME_FIELDS = (
    "resume_text", "resume_fetched_at", "resume_error", "resume_source_link",
)


# The fields a Workable-sourced record owns, for the CV-only roles that have no
# portal assignment behind them. Same contract as PORTAL_FIELDS: this is what a
# re-ingest overwrites, and `decision`, `evaluation` and `pipeline` are not in
# it because they are ours.
#
# `resume_text` IS here, unlike in PORTAL_FIELDS, and the reason the two differ
# is worth stating. There the text is a second pass over a link the CSV
# supplied, written later by set_resume(), so listing it would blank the
# backfill on every ingest. Here the text arrives in the same call as the rest
# of the record -- Workable hands over the file with the candidate -- so there
# is no second pass to protect, and leaving it out would mean a re-ingest that
# re-read every resume and then threw the text away.
WORKABLE_FIELDS = (
    "job_id", "job_title", "assignment_name", "candidate_name",
    "candidate_email", "candidate_phone", "candidate_headline",
    "candidate_location", "candidate_summary", "linkedin_url", "profile_url",
    "workable_candidate_id", "workable_stage", "workable_experience",
    "workable_education", "workable_skills", "cover_letter",
    "resume_link", "resume_source_link", "resume_filename", "resume_filetype",
    "resume_text", "resume_error",
    "video_link", "submission_markdown", "submission_status", "review_status",
    "started_at", "submitted_at", "auto_submitted", "cv_only",
)


def upsert_workable_candidates(records: list[dict]) -> dict:
    """
    Write candidates pulled from Workable for a role with no assessment.

    Keyed on `workable_candidate_id`, not on `_id`, because these records have
    no portal submission number to be keyed by. An integer id is allocated from
    CV_ONLY_ID_BASE the first time a candidate is seen and never again, which
    is what makes a re-run idempotent: the same candidate lands on the same
    row, keeping whatever decision, evaluation and pipeline stage that row has
    accumulated.

    Allocated sequentially rather than hashed from the Workable id. A hash
    would need no counter and no first-sight lookup, and would also, at some
    point nobody would notice, drop two candidates on one row and overwrite an
    evaluation with somebody else's.

    Returns {"matched": n, "inserted": n}.
    """
    if not records:
        return {"matched": 0, "inserted": 0}

    db = get_db()
    matched = inserted = 0

    for rec in records:
        candidate_id = rec.get("workable_candidate_id") or ""
        if not candidate_id:
            log.warning("Skipping a record with no workable_candidate_id: %s",
                        rec.get("candidate_email") or rec.get("candidate_name"))
            continue

        doc = {k: rec.get(k) for k in WORKABLE_FIELDS}
        doc["synced_at"] = now()
        if rec.get("resume_text") or rec.get("resume_error"):
            doc["resume_fetched_at"] = now()

        existing = db.submissions.find_one(
            {"workable_candidate_id": candidate_id}, {"_id": 1}
        )
        if existing:
            db.submissions.update_one({"_id": existing["_id"]}, {"$set": doc})
            matched += 1
        else:
            doc["_id"] = _next_cv_only_id()
            # Set once, on first sight, and never again. `pending` is the queue
            # the grader reads; a re-ingest must not knock a reviewer's accept
            # or reject back into it, which is why this is here and not in
            # WORKABLE_FIELDS.
            doc["decision"] = {"status": "pending",
                               "reason": "awaiting_evaluation",
                               "source": "auto",
                               "at": now()}
            db.submissions.insert_one(doc)
            inserted += 1

    return {"matched": matched, "inserted": inserted}


def _next_cv_only_id() -> int:
    """
    The next submission id in the CV-only band, allocated atomically.

    One counter document, incremented with find_one_and_update, so two ingests
    running at once cannot hand the same number to two candidates. The counter
    starts at CV_ONLY_ID_BASE and only ever goes up; a deleted record does not
    return its id to the pool, which is the right trade for a number that has
    to stay stable on a candidate's evaluation.
    """
    doc = get_db().counters.find_one_and_update(
        {"_id": "cv_only_submission_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return CV_ONLY_ID_BASE + int(doc["seq"])


def upsert_submissions(records: list[dict]) -> dict:
    """
    Write portal-owned submission fields, leaving `decision` and `evaluation`
    untouched. Returns {"matched": n, "upserted": n}.
    """
    if not records:
        return {"matched": 0, "upserted": 0}

    ops = []
    for rec in records:
        doc = {k: rec.get(k) for k in PORTAL_FIELDS}
        doc["synced_at"] = now()
        ops.append(UpdateOne({"_id": rec["submission_id"]}, {"$set": doc}, upsert=True))

    result = get_db().submissions.bulk_write(ops, ordered=False)
    return {
        "matched": result.modified_count,
        "upserted": result.upserted_count,
    }


def set_decision(submission_id: int, status: str, reason: str, source: str) -> None:
    """
    Record an accept/reject/pending decision.

    `source` is "auto" for the missing-artefact rule or "manual" for a human
    override; apply_auto_rejections() refuses to overwrite a manual one.
    """
    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": {"decision": {
            "status": status,
            "reason": reason,
            "source": source,
            "at": now(),
        }}},
    )


def set_evaluation(submission_id: int, evaluation: dict) -> None:
    """Store an AI evaluation and move the candidate out of the pending pile."""
    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": {
            "evaluation": {**evaluation, "graded_at": now()},
            "decision.status": "scored",
            "decision.reason": "ai_evaluated",
            "decision.source": "auto",
            "decision.at": now(),
        }},
    )


def set_rubric_tier(submission_id: int, tier: Optional[str], source: str,
                    shortcode: Optional[str] = None,
                    note: str = "") -> None:
    """
    Record which tier of a role's rubric this candidate is marked against.

    Only meaningful where one portal assignment serves two postings at
    different seniorities -- today the AI Strategist pair and nothing else.
    Everywhere else a submission has no tier and rubric_pack falls back to the
    slug's single grid, so this field simply never appears.

    `source` says how it was decided and is as load-bearing as the tier itself:
    "workable" for the posting the candidate actually applied to, "manual" for
    a reviewer who moved them across grids by hand -- which both AI Strategist
    rubrics instruct in their section 10 and ask to be noted on the file. A
    manual tier is never overwritten by a resolver run; that is what
    `resolved.source` is checked for before writing.

    Passing tier=None clears it, which is how a reviewer undoes a swap.
    """
    if tier is None:
        get_db().submissions.update_one(
            {"_id": submission_id}, {"$unset": {"rubric_tier": ""}},
        )
        return
    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": {"rubric_tier": {
            "tier": tier,
            "source": source,
            "shortcode": shortcode,
            "note": note,
            "at": now(),
        }}},
    )


def submissions_missing_tier(job_id: int) -> list[dict]:
    """
    Submissions on a role that no tier has been resolved for yet.

    Identity fields only: the resolver matches on email and writes back by id,
    and a role here can hold megabytes of answer text nobody is reading.
    """
    cursor = get_db().submissions.find(
        {"job_id": job_id, "rubric_tier": {"$exists": False}},
        {"_id": 1, "candidate_email": 1, "candidate_name": 1},
    )
    return list(cursor)


def tier_counts(job_id: int) -> dict[str, int]:
    """How many of a role's submissions sit at each tier, unresolved included."""
    counts: dict[str, int] = {}
    for row in get_db().submissions.aggregate([
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$rubric_tier.tier", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"] or "unresolved"] = row["n"]
    return counts


def set_resume(submission_id: int, text: str, error: str, source_link: str) -> None:
    """
    Store the text extracted from a candidate's resume.

    A targeted $set of four named fields, never a document replace, so that a
    write here cannot disturb `decision`, `evaluation` or anything the portal
    owns -- the same containment set_evaluation() gets.

    Both outcomes are recorded. A resume that would not download stores an
    empty `resume_text` and a reason in `resume_error`, which is what makes the
    backfill re-runnable: `resume_fetched_at` says the work was attempted, so
    the next run skips it instead of paying for the same 404 again.

    `resume_source_link` is the URL the text came from. It is what
    needs_resume() compares against the current `resume_link` -- without it a
    candidate who re-uploads their CV would keep the text from the old one
    forever, since the other three fields cannot tell the two apart.
    """
    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": {
            "resume_text": text,
            "resume_error": error,
            "resume_fetched_at": now(),
            "resume_source_link": source_link,
        }},
    )


def needs_resume(retry_errors: bool = False, limit: int = 0,
                 transient_only: bool = False) -> list[dict]:
    """
    Submitted candidates whose resume has not been read yet.

    Restricted to `submitted`, which is the population the grader can act on.
    The in-progress rows carry a link too, but they are people who never handed
    anything in, and fetching them would roughly double the run for nobody.

    Skips anything already attempted against the same link, so a re-run costs
    only the rows that are genuinely new. Pass retry_errors=True to take
    another pass at the ones that failed -- worth doing after a run that hit a
    network problem, pointless for the private-file and profile-page failures
    that make up most of them.

    transient_only narrows that retry to the failures that were about the
    moment rather than the link: rate limits, timeouts, dropped connections.
    That is the one worth running after a long backfill, because Google starts
    throttling part-way through and those rows are readable, just not then --
    whereas a private file will be exactly as private on the second attempt.
    """
    query: dict = {
        "submission_status": "submitted",
        "resume_link": {"$nin": [None, ""]},
    }
    projection = {"resume_link": 1, "resume_source_link": 1,
                  "resume_fetched_at": 1, "resume_error": 1,
                  "candidate_email": 1, "job_id": 1}

    rows = []
    for doc in get_db().submissions.find(query, projection):
        link = (doc.get("resume_link") or "").strip()
        attempted = doc.get("resume_fetched_at") is not None
        # A changed link is new work whatever the previous outcome was.
        stale = (doc.get("resume_source_link") or "").strip() != link
        error = doc.get("resume_error") or ""
        # Imported here rather than at module scope on purpose. This is the
        # store, and `scraping` sits above it -- a module-level import would be
        # the one place in the project where a lower layer reaches up into a
        # higher one, and it would make `import store` (which is to say,
        # everything) pull in requests, pypdf and python-docx to answer one
        # question about a string. is_transient() stays in resume_reader
        # because which failures are worth retrying is that module's knowledge,
        # not the database's.
        from backend.scraping.resume_reader import is_transient
        wanted = bool(error) and (
            is_transient(error) if transient_only else retry_errors
        )
        if attempted and not stale and not wanted:
            continue
        rows.append(doc)
        if limit and len(rows) >= limit:
            break
    return rows


def resume_stats() -> dict:
    """Counts for the --resumes summary: attempted, extracted, failed, pending."""
    subs = get_db().submissions
    submitted = {"submission_status": "submitted",
                 "resume_link": {"$nin": [None, ""]}}
    return {
        "with_link": subs.count_documents(submitted),
        "attempted": subs.count_documents({**submitted,
                                           "resume_fetched_at": {"$ne": None}}),
        "extracted": subs.count_documents({**submitted,
                                           "resume_text": {"$nin": [None, ""]}}),
        "failed": subs.count_documents({**submitted,
                                        "resume_error": {"$nin": [None, ""]}}),
    }


def resume_error_spread() -> list[tuple[str, int]]:
    """Failure reasons, commonest first, for the end-of-run report."""
    pipeline = [
        {"$match": {"submission_status": "submitted",
                    "resume_error": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$resume_error", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    return [(row["_id"], row["n"]) for row in get_db().submissions.aggregate(pipeline)]


def evaluations_for(job_id: Optional[int] = None) -> list[dict]:
    """Every scored submission, whole documents, for backup before a reset."""
    query: dict = {"evaluation": {"$exists": True}}
    if job_id is not None:
        query["job_id"] = job_id
    return list(get_db().submissions.find(query))


def clear_evaluations(job_id: Optional[int] = None,
                      keep_manual: bool = True) -> int:
    """
    Drop AI evaluations and return those candidates to the grading queue.

    The inverse of set_evaluation(): unset `evaluation` and put the decision
    back to `pending`, which is the only state ungraded() reads. Without the
    decision reset the rows would sit at `scored` with nothing to show for it
    and never be picked up again.

    A grading run is cheap to repeat and a human review is not, so a decision
    someone made by hand is left alone by default -- re-grading is a statement
    about the model, not about the reviewer who overrode it.

    Take a backup first. Returns the number of submissions reset.
    """
    query: dict = {"evaluation": {"$exists": True}}
    if job_id is not None:
        query["job_id"] = job_id
    if keep_manual:
        query["decision.source"] = {"$ne": "manual"}

    result = get_db().submissions.update_many(
        query,
        {"$unset": {"evaluation": ""},
         "$set": {
             "decision.status": "pending",
             "decision.reason": "awaiting_evaluation",
             "decision.source": "auto",
             "decision.at": now(),
         }},
    )
    return result.modified_count


def get_submission(submission_id: int) -> Optional[dict]:
    return get_db().submissions.find_one({"_id": submission_id})


def list_submissions(
    job_id: Optional[int] = None,
    status: Optional[str] = None,
    include_markdown: bool = False,
    limit: int = 0,
    tier: Optional[str] = None,
    default_tier: Optional[str] = None,
) -> list[dict]:
    """
    Submissions for the dashboard, best score first.

    `submission_markdown` is excluded unless asked for -- a single role can hold
    15 MB of answer text, which no list view needs.

    `resume_text` goes with it, and for the same reason rather than a different
    one: up to 8 KB per candidate, which on a 300-candidate role is 2.4 MB of
    CV text shipped to a list view that shows none of it. The drawer fetches a
    single submission when a reviewer opens one, and that path is unprojected.
    """
    query: dict = {}
    if job_id is not None:
        query["job_id"] = job_id
    if status:
        query["decision.status"] = status
    query.update(tier_filter(tier, default_tier))

    projection = ({"resume_text": 0} if include_markdown
                  else {"submission_markdown": 0, "resume_text": 0})
    cursor = get_db().submissions.find(query, projection).sort([
        ("evaluation.score", DESCENDING),
        ("submitted_at", DESCENDING),
    ])
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def role_counts() -> dict[int, dict]:
    """
    Per-role tallies for the dashboard's role sections, in one aggregation
    rather than a query per role (26 roles would otherwise be 26 round trips).
    """
    pipeline = [{
        "$group": {
            "_id": {"job_id": "$job_id", "status": "$decision.status"},
            "n": {"$sum": 1},
        }
    }]
    counts: dict[int, dict] = {}
    for row in get_db().submissions.aggregate(pipeline):
        job_id = row["_id"]["job_id"]
        status = row["_id"].get("status") or "unknown"
        bucket = counts.setdefault(job_id, {})
        bucket[status] = bucket.get(status, 0) + row["n"]
    for bucket in counts.values():
        bucket["total"] = sum(bucket.values())
    return counts


def list_rejected(job_id: Optional[int] = None,
                  job_ids: Optional[set[int]] = None) -> list[dict]:
    """
    Every rejected candidate, for the bulk rejection-email list.

    Deliberately narrow: just what an email needs. Answer text and evaluations
    are excluded, so pulling all 335 at once stays small.

    De-duplicated by email address. One person can sit more than one
    assessment, and a mail-merge that sends them two rejections in the same
    minute is worse than one that sends none -- the earliest submission wins,
    since that is the application they have been waiting on longest.
    """
    query: dict = {"decision.status": "rejected"}
    if job_id is not None:
        query["job_id"] = job_id
    # `job_ids` is the caller's access scope, not a filter the user chose, so
    # it is applied on top of `job_id` rather than instead of it -- a manager
    # asking for a role they do not own gets nothing, not everything.
    elif job_ids is not None:
        query["job_id"] = {"$in": sorted(job_ids)}

    projection = {
        "candidate_name": 1, "candidate_email": 1, "job_id": 1,
        "job_title": 1, "submitted_at": 1, "decision": 1,
        "video_link": 1, "resume_link": 1, "admin_url": 1,
    }
    cursor = get_db().submissions.find(query, projection).sort([
        ("job_title", ASCENDING), ("submitted_at", ASCENDING),
    ])

    seen: set[str] = set()
    rows = []
    for doc in cursor:
        email = (doc.get("candidate_email") or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        rows.append(doc)
    return rows


# ---------------------------------------------------------------------------
# The rejection ledger
# ---------------------------------------------------------------------------
#
# ONE ROW PER PERSON WE HAVE TOLD NO, KEYED BY THEIR EMAIL ADDRESS.
#
# Why this is not a field on `submissions`. Most of the people in it are not in
# that collection at all: several hundred were turned down at the CV screen, or
# inside Workable, before they ever sat an assessment, and the recruiter mailed
# them by hand out of a BCC field. There is no submission to hang a flag on,
# and inventing one per rejected applicant would put four hundred fake
# assessments in front of every count on the dashboard.
#
# So the ledger is keyed by the only thing every rejected person has: their
# address. That makes the question this exists to answer -- "have we already
# told this person no?" -- a primary-key lookup, and it makes it answerable for
# somebody who was rejected through a route this system has never seen.
#
# IT IS A LEDGER, NOT A SUPPRESSION LIST. `unsubscribes` is the suppression
# list and it means something else entirely: "stop mailing me". A person in
# here has been mailed exactly once, on purpose, and is welcome to hear from us
# about the next opening. Merging the two would quietly blacklist every
# rejected candidate from every future role.
#
# `status` is what actually happened to the message:
#
#     sent        this system sent it, and Brevo accepted it
#     recorded    somebody mailed them outside this system and typed them in
#     failed      we tried and it bounced -- they are still owed a reply
#
# `failed` is written rather than dropped for that last reason. A rejection
# that never arrived is a candidate still waiting, and that is invisible if
# only successes are kept.

# The statuses that mean this person has heard from us, so a second send would
# be a second rejection landing in the same inbox. `failed` is absent on
# purpose: retrying it is the point.
REJECTION_DELIVERED = ("sent", "recorded")


def clean_email(value) -> str:
    """Addresses are keyed and compared lower-case, everywhere."""
    return str(value or "").strip().lower()


def record_rejection(email: str, name: str = "", job_id: Optional[int] = None,
                     job_title: str = "", status: str = "recorded",
                     source: str = "manual", by: str = "",
                     subject: str = "", note: str = "",
                     submission_id: Optional[int] = None,
                     error: str = "") -> str:
    """
    Write one person into the ledger. Returns "added" or "updated".

    Upsert on the address, so importing the same pasted list twice is the same
    as importing it once -- which is the ordinary case, because a recruiter
    pasting four hundred addresses does not know which of them they already
    pasted last week.

    FIRST CONTACT IS $setOnInsert AND NEVER MOVES. `first_rejected_at` is when
    this person was first told no, and re-running an import must not rewrite it
    to today -- that is the field anyone asking "how long ago did we turn them
    down" is reading. `rejected_at` beside it is the most recent write, so the
    pair says both.

    A FAILED SEND NEVER OVERWRITES A GOOD ONE. Somebody successfully mailed in
    March and then caught by a bounce in a re-run is still somebody who has
    been told; downgrading their row would put them back in the next batch.
    """
    address = clean_email(email)
    if not address:
        raise ValueError("A rejection needs an email address.")

    stamp = now()
    latest: dict = {"status": status, "source": source, "rejected_at": stamp}
    for key, value in (("name", str(name or "").strip()),
                       ("job_title", str(job_title or "").strip()),
                       ("by", clean_email(by)), ("subject", subject),
                       ("note", str(note or "").strip())):
        # Blank means "nothing new to say about this", not "erase what is
        # there". A bulk send that knows the address and not the name must not
        # wipe a name an earlier import supplied.
        if value:
            latest[key] = value
    if job_id is not None:
        latest["job_id"] = job_id
    if submission_id is not None:
        latest["submission_id"] = submission_id
    latest["error"] = error or None

    if status == "failed":
        existing = get_db().rejections.find_one({"_id": address}, {"status": 1})
        if existing and existing.get("status") in REJECTION_DELIVERED:
            latest["status"] = existing["status"]
            latest["last_error"] = error or None
            latest.pop("error", None)

    result = get_db().rejections.update_one(
        {"_id": address},
        {"$set": latest, "$setOnInsert": {"first_rejected_at": stamp}},
        upsert=True,
    )
    return "added" if result.upserted_id is not None else "updated"


def record_rejections(entries: list[dict], job_id: Optional[int] = None,
                      job_title: str = "", status: str = "recorded",
                      source: str = "manual", by: str = "",
                      note: str = "") -> dict:
    """
    Write a whole pasted list at once. Returns {added, updated, total}.

    One bulk_write rather than four hundred round trips, which is the
    difference between an import that returns and one the browser gives up on.
    `entries` is [{"email", "name"?}, ...]; anything without an address is
    dropped by the caller before it reaches here.
    """
    stamp = now()
    ops = []
    addresses = set()
    for entry in entries:
        address = clean_email(entry.get("email"))
        if not address or address in addresses:
            continue
        addresses.add(address)
        latest: dict = {"status": status, "source": source,
                        "rejected_at": stamp, "error": None}
        name = str(entry.get("name") or "").strip()
        if name:
            latest["name"] = name
        if by:
            latest["by"] = clean_email(by)
        if note:
            latest["note"] = note
        if job_id is not None:
            latest["job_id"] = job_id
        if job_title:
            latest["job_title"] = job_title
        ops.append(UpdateOne(
            {"_id": address},
            {"$set": latest, "$setOnInsert": {"first_rejected_at": stamp}},
            upsert=True,
        ))

    if not ops:
        return {"added": 0, "updated": 0, "total": 0}

    result = get_db().rejections.bulk_write(ops, ordered=False)
    added = len(result.upserted_ids or {})
    return {"added": added, "updated": len(addresses) - added,
            "total": len(addresses)}


def already_rejected(emails) -> set[str]:
    """
    Which of these addresses have already been told no. One query, not one per
    candidate.

    FAILS CLOSED, like unsubscribe.suppressed_among() and for the same reason:
    the cost of an unreadable database here is a second rejection landing in
    somebody's inbox, which cannot be taken back. A ledger we cannot read stops
    the send rather than waving five hundred messages through it.
    """
    wanted = {clean_email(e) for e in emails if clean_email(e)}
    if not wanted:
        return set()
    try:
        rows = get_db().rejections.find(
            {"_id": {"$in": sorted(wanted)},
             "status": {"$in": list(REJECTION_DELIVERED)}}, {"_id": 1})
        return {row["_id"] for row in rows}
    except PyMongoError as exc:
        log.error("Cannot read the rejection ledger (%s). Treating all %d "
                  "addresses as already rejected rather than risk sending a "
                  "second turn-down.", exc, len(wanted))
        return set(wanted)


def rejection_for(email: str) -> Optional[dict]:
    """One person's ledger row, or None. The duplicate check for a single send."""
    address = clean_email(email)
    if not address:
        return None
    return get_db().rejections.find_one({"_id": address})


def rejections_for(emails) -> dict[str, dict]:
    """
    The ledger rows for these addresses, keyed by address. One query.

    already_rejected() answers yes-or-no and is what a send gates on; this
    answers WHEN and HOW, which is what a list on screen has to say. "Already
    told" beside a name is a fact somebody will want to check -- told when, by
    which route -- and a boolean cannot answer that.

    Fails OPEN, unlike already_rejected(). The cost of an unreadable ledger
    here is a column that does not draw; the cost there is a second rejection
    in somebody's inbox. Same data, different consequence, so a different
    default -- and the send never reads this one.
    """
    wanted = {clean_email(e) for e in emails if clean_email(e)}
    if not wanted:
        return {}
    try:
        return {row["_id"]: row for row in
                get_db().rejections.find({"_id": {"$in": sorted(wanted)}})}
    except PyMongoError as exc:
        log.error("Cannot read the rejection ledger (%s). The list will not "
                  "show who has already been told.", exc)
        return {}


def list_rejections(job_id: Optional[int] = None, status: Optional[str] = None,
                    search: str = "", limit: int = 0) -> list[dict]:
    """
    The ledger, most recently rejected first.

    `search` matches the address or the name, case-insensitively -- the
    recruiter's question is nearly always "did we already write to this one
    person", typed as half their address.
    """
    query: dict = {}
    if job_id is not None:
        query["job_id"] = job_id
    if status:
        query["status"] = status
    term = str(search or "").strip()
    if term:
        pattern = re.escape(term)
        query["$or"] = [{"_id": {"$regex": pattern, "$options": "i"}},
                        {"name": {"$regex": pattern, "$options": "i"}}]

    cursor = get_db().rejections.find(query).sort([("rejected_at", DESCENDING)])
    if limit:
        cursor = cursor.limit(int(limit))
    return list(cursor)


def count_rejections(job_id: Optional[int] = None, status: Optional[str] = None,
                     search: str = "") -> int:
    """How many rows the same filters match, so a capped list can say so."""
    query: dict = {}
    if job_id is not None:
        query["job_id"] = job_id
    if status:
        query["status"] = status
    term = str(search or "").strip()
    if term:
        pattern = re.escape(term)
        query["$or"] = [{"_id": {"$regex": pattern, "$options": "i"}},
                        {"name": {"$regex": pattern, "$options": "i"}}]
    return get_db().rejections.count_documents(query)


def delete_rejections(emails) -> int:
    """
    Take people back out of the ledger. Returns how many rows went.

    The undo for a paste that caught the wrong addresses. It does not un-send
    anything -- it says "this system should stop believing these people have
    been told", which is what puts them back in front of the next send.
    """
    addresses = sorted({clean_email(e) for e in emails if clean_email(e)})
    if not addresses:
        return 0
    return get_db().rejections.delete_many(
        {"_id": {"$in": addresses}}).deleted_count


def rejection_stats() -> dict:
    """
    How many people are in the ledger, and how each of them heard.

    One aggregation rather than a count per status, so the header on the page
    costs the same whether there are four rows or four thousand.
    """
    rows = get_db().rejections.aggregate([
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ])
    by_status = {(row["_id"] or "unknown"): row["n"] for row in rows}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "sent": by_status.get("sent", 0),
        "recorded": by_status.get("recorded", 0),
        "failed": by_status.get("failed", 0),
    }


# ---------------------------------------------------------------------------
# Manager review links
# ---------------------------------------------------------------------------
#
# One row per (role, manager, send). The token is the document's _id, which is
# what makes a lookup a primary-key hit rather than a scan -- this runs on
# every request a manager makes.
#
# The link is the credential, so the document carries everything needed to
# decide what it may do: which role, which manager, which candidates, when it
# expires, and whether it has been revoked. Nothing is inferred from the URL
# beyond the token itself.
#
# `submission_ids` freezes the list to exactly what was emailed. A manager who
# opens the link a week later sees the twenty people they were sent, not a
# quietly different twenty -- and cannot act on a candidate who was never in
# their list even if they guess the id. Refreshing it is a new send, which is
# a new link, which is an email saying so.


def create_review_link(job_id: int, manager: dict, submission_ids: list[int],
                       days: int = REVIEW_LINK_DAYS,
                       source: str = "shortlist") -> str:
    """
    Mint a review token for one manager on one role. Returns the token.

    `source` records how it came to exist -- "shortlist" for the recruiter's
    send, "dashboard" for a manager who asked for their own from the board.
    Recorded rather than inferred because the two revoke differently in
    practice: killing a shortlist link takes away something a manager was sent
    and may still need, while a self-served one they can always re-open.
    """
    token = secrets.token_urlsafe(32)
    stamp = now()
    get_db().review_links.insert_one({
        "_id": token,
        "job_id": job_id,
        "manager": {"name": manager.get("name") or manager.get("email"),
                    "email": manager.get("email"),
                    "title": manager.get("title") or ""},
        "submission_ids": list(submission_ids),
        "created_at": stamp,
        "expires_at": stamp + timedelta(days=days),
        "revoked": False,
        "opened_at": None,
        "last_seen_at": None,
        "views": 0,
        "actions": [],
        "source": source,
    })
    return token


def live_dashboard_link(job_id: int, email: str) -> Optional[dict]:
    """
    This manager's own still-valid self-served link for this role, if any.

    So that pressing the button twice does not leave two live credentials for
    the same person and the same list. Only "dashboard" links are reused: the
    one a recruiter mailed is scoped to the candidates that were *sent*, and
    quietly widening it to today's top twenty would hand somebody a bigger list
    than the one they were given.
    """
    address = str(email or "").strip().lower()
    if not address:
        return None
    return get_db().review_links.find_one({
        "job_id": job_id,
        "manager.email": address,
        "source": "dashboard",
        "revoked": False,
        "expires_at": {"$gt": now()},
    }, sort=[("created_at", DESCENDING)])


def refresh_dashboard_link(token: str, submission_ids: list[int],
                           days: int = REVIEW_LINK_DAYS) -> None:
    """
    Re-point a manager's self-served link at today's list, and push its expiry.

    The board moves under it: invite three people and they leave the shortlist,
    so the twenty this token was minted against is not the twenty the manager
    is looking at ten minutes later. Rewriting the list rather than minting a
    second token keeps one live credential per manager per role, which is the
    number that can actually be reasoned about when one has to be revoked.

    ONLY EVER CALLED FOR source="dashboard" LINKS -- see live_dashboard_link().
    Doing this to a mailed shortlist link would silently change the list
    somebody was sent after the fact.
    """
    get_db().review_links.update_one(
        {"_id": token, "source": "dashboard"},
        {"$set": {"submission_ids": list(submission_ids),
                  "expires_at": now() + timedelta(days=days)}},
    )


def get_review_link(token: str) -> Optional[dict]:
    """
    The link document, or None if the token is unknown.

    Deliberately does NOT check expiry or revocation -- the caller does, so it
    can tell a manager "this link expired on the 3rd" instead of the same blank
    404 an invented token gets. Those are different situations and a person
    holding a real-but-stale link needs to be told which one they are in.
    """
    if not token or not isinstance(token, str):
        return None
    return get_db().review_links.find_one({"_id": token})


def review_link_state(link: Optional[dict]) -> str:
    """"ok", "unknown", "revoked" or "expired" -- the one place that rule lives."""
    if link is None:
        return "unknown"
    if link.get("revoked"):
        return "revoked"
    # `aware()` returns None for a field that holds neither a datetime nor an
    # ISO string. Walrus rather than a bare truth test so an unusable value
    # falls through to "ok" instead of raising -- the old spelling read
    # `.tzinfo` off whatever it was handed, which turned one corrupt document
    # into a 500 on the manager's review link.
    expires = _aware(link.get("expires_at"))
    if expires and expires < now():
        return "expired"
    return "ok"


def touch_review_link(token: str) -> None:
    """
    Record that the link was opened.

    First open is kept apart from the latest one: "sent a week ago and never
    opened" is the thing a recruiter chases, and it is not recoverable from a
    last-seen stamp that moves every time.

    Two updates rather than one. The obvious single-call trick -- $min on
    `opened_at` -- silently never fires: the field starts as null, null sorts
    below every date in BSON, so $min keeps choosing the null forever and every
    link reads as never opened however often it was used. The second update is
    a primary-key hit that matches nothing after the first view.
    """
    stamp = now()
    links = get_db().review_links
    links.update_one({"_id": token},
                     {"$set": {"last_seen_at": stamp}, "$inc": {"views": 1}})
    links.update_one({"_id": token, "opened_at": None},
                     {"$set": {"opened_at": stamp}})


def record_review_action(token: str, submission_id: int, stage: Optional[str],
                         note: Optional[str] = None) -> None:
    """
    Append what a manager did to their own link's audit trail.

    Duplicated on purpose: the move is already on the submission's pipeline
    history, but that history is about the candidate, and this one is about the
    link. "What did this token do while it was live" is the question asked when
    a link is suspected of having been forwarded, and it cannot be answered by
    walking 4,000 submissions.
    """
    get_db().review_links.update_one(
        {"_id": token},
        {"$push": {"actions": {"submission_id": submission_id, "stage": stage,
                               "note": note, "at": now()}}},
    )


def revoke_review_link(token: str) -> bool:
    """Kill a link. Returns False if the token was already unknown."""
    result = get_db().review_links.update_one(
        {"_id": token}, {"$set": {"revoked": True, "revoked_at": now()}})
    return result.matched_count > 0


def list_review_links(job_id: Optional[int] = None,
                      active_only: bool = False) -> list[dict]:
    """Links for the dashboard, newest first, so a recruiter can revoke one."""
    query: dict = {}
    if job_id is not None:
        query["job_id"] = job_id
    if active_only:
        query["revoked"] = False
        query["expires_at"] = {"$gt": now()}
    return list(get_db().review_links.find(query).sort([("created_at", DESCENDING)]))


def submissions_for_review(link: dict) -> list[dict]:
    """
    The candidates a review link may show, in the order they were sent.

    Ordered in Python rather than by Mongo: `submission_ids` is the ranking the
    manager already has in their email and their spreadsheet, and a $in query
    comes back in storage order, which would silently renumber their list.

    Answer text and CV text are projected out, and so is `evaluation` -- the
    score must not reach the manager's browser at all, not even in a payload
    the page happens not to render. A field that is only hidden by the template
    is one careless `JSON.stringify` away from being visible.

    One fact from inside `evaluation` does have to reach them, and it is
    fetched by a SECOND query with an allowlist projection rather than by
    poking a hole in the exclusion above. A part-filled grid is renormalised to
    100 by the scorer, so it ranks like a real score; the manager sees rank and
    no number, which means on this page a partial grade is completely
    invisible -- position 1 with nothing to suggest it was not earned. The flag
    lands as a top-level `grading_incomplete` bool. Widening the exclusion list
    to `evaluation.score` and friends would have done the same job as a
    denylist, and the next field added to a verdict would have shipped to a
    manager's browser by default.
    """
    ids = link.get("submission_ids") or []
    if not ids:
        return []
    found = {
        doc["_id"]: doc
        for doc in get_db().submissions.find(
            {"_id": {"$in": ids}, "job_id": link["job_id"]},
            {"submission_markdown": 0, "resume_text": 0, "evaluation": 0},
        )
    }
    for doc in get_db().submissions.find(
        {"_id": {"$in": ids}, "job_id": link["job_id"], **PARTIAL_GRID},
        {"_id": 1},
    ):
        if doc["_id"] in found:
            found[doc["_id"]]["grading_incomplete"] = True
    return [found[i] for i in ids if i in found]


# ---------------------------------------------------------------------------
# Hiring pipeline
# ---------------------------------------------------------------------------
#
# What happens to a candidate *after* the score: the interview they were called
# to, and the offer or rejection that followed.
#
# This lives in its own `pipeline` sub-document rather than in `decision.status`
# for two reasons. Ingest overwrites everything the portal owns on every run,
# and a hiring manager's decision has to survive that -- the same containment
# `decision` and `evaluation` already get. And `decision` answers a different
# question: it says what the assessment concluded, which is what the grading
# queue and the missing-artefact reject list are built on. A candidate rejected
# after an interview is still a `scored` submission with the score that earned
# them the interview; overwriting that would lose the record of why they were
# ever seen, and would drop them into the artefact-rejection mail merge, which
# is a different email to a different person.

# In process order. `shortlist` is the absence of a stage rather than a value:
# every scored candidate is implicitly there until someone moves them.
PIPELINE_STAGES = ("interview", "hired", "rejected")


def set_pipeline_stage(
    submission_id: int,
    stage: Optional[str],
    interview_at: Optional[str] = None,
    interviewer: Optional[str] = None,
    note: Optional[str] = None,
    reason: Optional[str] = None,
    source: str = "manual",
    by: Optional[str] = None,
) -> None:
    """
    Move a candidate along the pipeline: interview -> hired or rejected.

    `stage=None` takes them back out of it, which is the undo for a misclick.
    The history is kept either way -- "was scheduled for an interview on the
    12th and pulled back out" is a fact about the candidacy, and a board that
    silently forgets it cannot be audited.

    Detail fields are only written when supplied, so marking an interviewed
    candidate hired does not erase when the interview was.

    `source` says which surface the move came from -- "manual" for the
    recruiter's dashboard, "manager" for a hiring manager acting on their
    review link -- and `by` names the person. Both go on the document AND into
    the history entry, so a hire can still be attributed after the same
    candidate has been moved three more times. Without this the board could say
    someone was hired but never who decided it, which is the first question
    asked about any hire that goes wrong.
    """
    if stage is not None and stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown pipeline stage: {stage}")

    stamp = now()
    fields: dict = {
        "pipeline.stage": stage,
        "pipeline.at": stamp,
        "pipeline.source": source,
    }
    entry: dict = {"stage": stage, "at": stamp, "source": source}
    for key, value in (("interview_at", interview_at), ("interviewer", interviewer),
                       ("note", note), ("reason", reason), ("by", by)):
        if value is None:
            continue
        fields[f"pipeline.{key}"] = value
        entry[key] = value

    # $set on named leaves plus $push on the history array: never a $set of
    # `pipeline` itself, which would both wipe the history and conflict with
    # the push on the same path.
    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": fields, "$push": {"pipeline.history": entry}},
    )


def record_stage_email(submission_id: int, stage: str, to: str,
                       subject: str, cal_link: str = "",
                       error: Optional[str] = None) -> dict:
    """
    Note that a candidate was mailed about a stage move -- or that the send
    failed.

    A history rather than a flag, for the reason record_shortlist_send() keeps
    one: "has this person already been told they were rejected" is the question
    asked immediately before pressing the button a second time, and a boolean
    that says yes cannot say when, to which address, or with whose booking link
    on it. A rescheduled interview writes a second entry, and the pair is the
    audit trail for a candidate who says they got two different times.

    Failures are recorded too. A rejection email that bounced is a candidate
    still waiting to hear, and that is invisible if only successes are written.
    """
    entry: dict = {
        "stage": stage,
        "at": now(),
        "to": to,
        "subject": subject,
        "ok": error is None,
    }
    if cal_link:
        entry["cal_link"] = cal_link
    if error:
        entry["error"] = error

    get_db().submissions.update_one(
        {"_id": submission_id},
        {"$set": {"pipeline.last_email": entry},
         "$push": {"pipeline.emails": entry}},
    )
    return entry


def last_stage_email(submission: dict, stage: str) -> Optional[dict]:
    """
    The most recent successful mail for a stage, from a submission already in
    hand.

    Takes the document rather than an id because every caller has just fetched
    it, and this is the duplicate-send check -- a second round trip to answer
    "did we already send this" would be a round trip on the hot path of every
    move.
    """
    for entry in reversed((submission.get("pipeline") or {}).get("emails") or []):
        if entry.get("stage") == stage and entry.get("ok"):
            return entry
    return None


def list_pipeline(stage: Optional[str] = None,
                  job_id: Optional[int] = None,
                  job_ids: Optional[set[int]] = None) -> list[dict]:
    """
    Candidates sitting at a pipeline stage, for the board.

    Ordered by what the stage is for: interviews by when they are, soonest
    first, so today's is at the top; the closed stages by when they were
    decided, most recent first. Answer text and CV text are projected out for
    the reason list_submissions() gives.
    """
    query: dict = {"pipeline.stage": stage if stage
                   else {"$in": list(PIPELINE_STAGES)}}
    if job_id is not None:
        query["job_id"] = job_id
    elif job_ids is not None:                     # the caller's access scope
        query["job_id"] = {"$in": sorted(job_ids)}

    order = ([("pipeline.interview_at", ASCENDING)] if stage == "interview"
             else [("pipeline.at", DESCENDING)])
    cursor = get_db().submissions.find(
        query, {"submission_markdown": 0, "resume_text": 0}
    ).sort(order)
    return list(cursor)


def pipeline_counts() -> dict:
    """
    Stage tallies, overall and per role, in one aggregation.

    Returns {"stages": {stage: n}, "by_role": {job_id: {stage: n}}} -- the
    board's header counts and the per-role card counts come off the same trip.
    """
    pipeline = [
        {"$match": {"pipeline.stage": {"$in": list(PIPELINE_STAGES)}}},
        {"$group": {
            "_id": {"job_id": "$job_id", "stage": "$pipeline.stage"},
            "n": {"$sum": 1},
        }},
    ]
    stages: dict[str, int] = {s: 0 for s in PIPELINE_STAGES}
    by_role: dict[int, dict] = {}
    for row in get_db().submissions.aggregate(pipeline):
        job_id = row["_id"]["job_id"]
        stage = row["_id"]["stage"]
        stages[stage] = stages.get(stage, 0) + row["n"]
        bucket = by_role.setdefault(job_id, {})
        bucket[stage] = bucket.get(stage, 0) + row["n"]
    return {"stages": stages, "by_role": by_role}


def ungraded(job_id: Optional[int] = None, limit: int = 0,
             tier: Optional[str] = None,
             default_tier: Optional[str] = None) -> list[dict]:
    """
    Submissions eligible for AI grading: submitted, not auto-rejected, and not
    already scored. Includes the answer markdown, since that is what gets sent
    to the model.
    """
    query = {
        "submission_status": "submitted",
        "decision.status": "pending",
        "evaluation": {"$exists": False},
    }
    if job_id is not None:
        query["job_id"] = job_id
    query.update(tier_filter(tier, default_tier))
    cursor = get_db().submissions.find(query).sort([("submitted_at", ASCENDING)])
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)
