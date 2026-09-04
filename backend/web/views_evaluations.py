"""
The evaluations page: what a submission is worth, and where the candidate is.

Roles and their candidates, the rubric behind a score, the grading and ingest
runs that produce one, and the pipeline stage a candidate has been moved to.

WAS THREE MODULES -- views_evaluations.py, views_grading.py and
views_pipeline.py. They are the API of a single page (evaluations.html) and
every one of them is reached from it; the split put three files between a
reader and one screen. Each former module's own notes are kept below, as the
section banners.
"""


from datetime import datetime
from flask import jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend import auth
from backend.config import (AUTH_ENABLED, MANAGER_DASHBOARD_SCORES,
                            SHORTLIST_MAX, SHORTLIST_SIZE, LLM_CONCURRENCY)
from backend.db import store
from backend.grading import evaluator, rubric_pack, tier_resolver, grader
from backend.mail import candidate_mail
from backend.pipeline import ingest

from backend.web.app import (INTERVIEW_IS_THE_MANAGERS,
                             MANAGER_INVITES_FROM_COMPOSER, _current_user,
                             _is_admin, _json_safe, _mongo_guard, _project,
                             _role_guard, _run_lock, _scope,
                             _scoped_stage_counts, _submission_guard,
                             _tier_arg, _tier_options, app, log, _require_admin)


# --------------------------------------------------------------------------
# Roles, candidates, rubrics and decisions  (was views_evaluations.py)
# --------------------------------------------------------------------------
# Evaluations API -- roles, candidates and AI scores out of MongoDB.
#
# The role list, one role's candidates, a submission, the rubric behind
# it, the rejected list, decisions, and the tier resolution.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Evaluations API -- roles, candidates and AI scores out of MongoDB
#
# These read from Mongo rather than the portal, so they answer instantly. The
# portal is only touched by /api/evaluations/ingest.
# ---------------------------------------------------------------------------





def _rubric_source(role: dict) -> str | None:
    """
    "pack" when the rubric pack covers this assessment, "derived" when a grid
    file has been written for it, None when neither -- which is the one case
    where grading needs a model call before it can start.
    """
    slug = role.get("slug")
    if not slug:
        return None
    if rubric_pack.for_slug(slug):
        return "pack"
    return "derived" if evaluator.grid_path(slug).exists() else None


def _split_by_tier(role: dict, card: dict, tiers: dict) -> list[dict]:
    """
    One card per posting where a role is marked at two tiers, else the card.

    The two cards share a role id and differ in `tier`, which is what the page
    sends back on every request about them. Their counts are that tier's own,
    and the default tier's card carries the unresolved -- the ones no resolver
    run has matched to a posting yet -- because grading marks those against the
    default grid, so that is the queue they are actually in. `unresolved` on
    the card is the honest version of that: how many of this card's people are
    here by fallback rather than by evidence.

    The pipeline chips are left on the default tier's card alone rather than
    split. A booked interview belongs to a person, not to a standard, and
    dividing three interviews across two cards by how each person was marked
    would answer a question nobody asked while making "how many interviews on
    this assignment" take two glances.
    """
    available, default_tier = _tier_options(role)
    if not available:
        return [card]

    labels = tier_resolver.posting_labels(role.get("slug"))
    unresolved = tiers.get("unresolved", {})
    # Default tier first. `tiers_for_slug` returns them alphabetically, which
    # would put the associate card ahead of the senior one for no reason a
    # reader could name; the default is the one that carries the unmatched, so
    # it is also the one to land on.
    ordered = ([default_tier] if default_tier in available else []) +               [t for t in available if t != default_tier]
    cards = []
    for tier in ordered:
        tally = dict(tiers.get(tier, {}))
        if tier == default_tier:
            for key, n in unresolved.items():
                tally[key] = tally.get(key, 0) + n
        cards.append({
            **card,
            "tier": tier,
            "tier_default": tier == default_tier,
            "title": labels.get(tier) or f"{card['title']} ({tier})",
            "assignment_title": card["title"],
            "unresolved": unresolved.get("total", 0) if tier == default_tier else 0,
            "counts": {
                "total": tally.get("total", 0),
                "pending": tally.get("pending", 0),
                "scored": tally.get("scored", 0),
                "rejected": tally.get("rejected", 0),
                "in_progress": tally.get("in_progress", 0),
            },
            "pipeline": (card["pipeline"] if tier == default_tier
                         else {"interview": 0, "hired": 0, "rejected": 0}),
        })
    return cards


@app.route("/api/evaluations/roles")
def api_roles():
    """
    The roles this account may see, with their per-status candidate tallies.

    For an admin that is every role. For a hiring manager it is only the roles
    their address is on -- and the ones it is not on are absent rather than
    greyed out, because a card saying "AI Strategist -- locked" still tells
    them the seat exists and how many people applied to it.

    The header numbers are narrowed with them. A manager whose two roles have
    six interviews between them should read six, not the company's ninety.
    """
    error = _mongo_guard()
    if error:
        return error

    scope = _scope()
    counts = store.role_counts()
    tier_tallies = store.role_tier_counts(job_ids=scope)
    stages = store.pipeline_counts()
    if scope is not None:
        stages = _scoped_stage_counts(stages, scope)
    roles = []
    for role in store.get_roles(job_ids=scope):
        tally = counts.get(role["_id"], {})
        stage_tally = stages["by_role"].get(role["_id"], {})
        assessment = role.get("assessment") or {}
        card = {
            "id": role["_id"],
            "title": role.get("title"),
            "slug": role.get("slug"),
            "published": role.get("published", False),
            "apply_url": role.get("apply_url"),
            "admin_url": role.get("admin_url"),
            "assessment_name": assessment.get("name"),
            "has_assessment": bool(assessment.get("markdown")),
            # Who this role's shortlist goes to, and when it last went. Both
            # ride along with the roles rather than sitting behind a click:
            # "which of my roles has nobody to send to" is a question about the
            # whole grid, and it cannot be answered one card at a time.
            "managers": role.get("hiring_managers") or [],
            "shortlist_last": _json_safe(role["shortlist_last"])
                              if role.get("shortlist_last") else None,
            # A role is gradeable when the pack covers its assessment or a
            # grid has been derived for it. The badge says which, since a pack
            # grid is a hand-authored standard and a derived one is not.
            "rubric_source": _rubric_source(role),
            "counts": {
                "total": tally.get("total", 0),
                "pending": tally.get("pending", 0),
                "scored": tally.get("scored", 0),
                "rejected": tally.get("rejected", 0),
                "in_progress": tally.get("in_progress", 0),
            },
            # Where this role's people are after the score. Counted separately
            # from the tallies above rather than folded into them: someone
            # booked for an interview is still a scored submission, and a card
            # whose segments stopped summing to its total would read as a bug.
            "pipeline": {
                "interview": stage_tally.get("interview", 0),
                "hired": stage_tally.get("hired", 0),
                "rejected": stage_tally.get("rejected", 0),
            },
        }
        roles.extend(_split_by_tier(role, card, tier_tallies.get(role["_id"], {})))
    user = _current_user()
    return jsonify({
        "roles": roles,
        "evaluator_configured": evaluator.is_configured(),
        "pipeline": stages["stages"],
        # The directory of everyone who owns a seat anywhere in the company.
        # It exists to autocomplete the manager editor, which is an admin
        # screen; handing it to a manager would hand them a staff list and the
        # roles each person is hiring for.
        "known_managers": store.known_managers() if _is_admin() else [],
        # What the page may draw. NOT what the page may have -- every route
        # above makes its own check, and this is only here so a manager is not
        # shown buttons that would answer 403.
        "user": auth.public_user(user) if user else None,
        "is_admin": _is_admin(),
        # Whether the score column is drawn at all. Sent rather than derived
        # from `is_admin`, because for a manager the answer is a setting --
        # MANAGER_DASHBOARD_SCORES -- and the page must not have to guess it
        # from whether the rows it happens to be holding carry an `evaluation`.
        # That test reads "not graded yet" as "not allowed" and would blank the
        # column on a role whose grading has not run.
        "scores_visible": _is_admin() or MANAGER_DASHBOARD_SCORES,
        "auth_enabled": AUTH_ENABLED,
        "shortlist_size": SHORTLIST_SIZE,
        "shortlist_max": SHORTLIST_MAX,
        # What a stage move does to the candidate's inbox. `auto` is off while
        # sending is manual, and the drawer reads it to decide whether it is
        # offering a Send button or explaining that the move sends by itself.
        # One source for the answer, so the button and the server can never
        # disagree about what a click is going to do.
        "candidate_emails": {
            "enabled": candidate_mail.PIPELINE_EMAILS_ENABLED,
            "auto": candidate_mail.PIPELINE_AUTO_EMAIL,
            # The one-click stage move stays shut for EVERYONE, manager
            # included. Entering the interview stage has always meant an
            # invitation was written; a bare board move has no composer behind
            # it, so allowing it here would let somebody sit at "interview"
            # with nothing in their inbox and nobody aware they had been asked.
            # A manager's way in is their own review workspace -- see
            # api_my_review_link -- which moves and writes in one act.
            #
            # Read by the drawer and the board so they offer what the server
            # will actually accept, and say why where the buttons used to be.
            "interview_locked": True,
            # Two wordings of one rule. A manager reading the recruiter's
            # version -- "only the hiring manager can do this" -- on their own
            # role would reasonably conclude we had lost track of who they
            # were. Which wording is not the access rule and does not try to
            # be: entitlement is name-on-the-role, decided by
            # api_my_review_link, and _is_admin() is only a good enough guess
            # at which sentence will read correctly.
            "interview_locked_reason": (INTERVIEW_IS_THE_MANAGERS
                                        if _is_admin()
                                        else MANAGER_INVITES_FROM_COMPOSER),
        },
    })


@app.route("/api/evaluations/role/<int:job_id>")
def api_role_candidates(job_id: int):
    """
    Candidates for one role. `status` filters to a single bucket; omit it for
    everyone. Answer text is excluded -- /api/evaluations/submission fetches it.

    `tier` narrows to one posting where the role is marked at two. The default
    tier includes the candidates nobody has matched to a posting yet, because
    that is the grid they are graded against; `tier=unresolved` asks for only
    those, which is how you see what a resolver run still has to do.
    """
    error = _mongo_guard()
    if error:
        return error

    error = _role_guard(job_id)
    if error:
        return error

    status = request.args.get("status") or None
    limit = request.args.get("limit", default=0, type=int)

    role = store.get_role(job_id)
    tier, default_tier, error = _tier_arg(role)
    if error:
        return error

    candidates = [_json_safe(c) for c in
                  store.list_submissions(job_id=job_id, status=status,
                                         limit=limit, tier=tier,
                                         default_tier=default_tier)]
    # Do not let an old evaluation continue to look current when the stored
    # resume fetch already failed. Grading now blocks these rows; this keeps
    # previously stored scores honest until the role is reloaded/re-graded.
    for candidate in candidates:
        if ((candidate.get("resume_link") or "").strip()
                and (candidate.get("resume_error") or "").strip()):
            candidate["cv_fetch_status"] = "cv_cannot_be_fetched"
            candidate["evaluation"] = None
            candidate["decision"] = {
                "status": "pending",
                "reason": "cv_cannot_be_fetched",
                "source": "auto",
            }
    candidates = _project(candidates)
    tiers, _ = _tier_options(role)
    return jsonify({
        "role": _json_safe(role),
        "candidates": candidates,
        "tier": tier,
        "tiers": list(tiers),
        "default_tier": default_tier,
        # Only meaningful on a tiered role, and the number a reviewer needs
        # before reading anything into the split: all-unresolved means the two
        # cards are a filter over one undivided pile.
        "tier_counts": store.tier_counts(job_id) if tiers else None,
    })


@app.route("/api/evaluations/submission/<int:submission_id>")
def api_submission(submission_id: int):
    """
    One submission including the full answer markdown, for the detail drawer.

    The role's hiring managers ride along under `managers`. The drawer is where
    an interview is actually booked, and the invitation needs a calendar: a
    manager who had to go and find their own link on another panel before
    scheduling would paste it fresh every time, which is how three different
    links end up on one person.
    """
    error = _mongo_guard()
    if error:
        return error

    sub = store.get_submission(submission_id)
    error = _submission_guard(sub, submission_id)
    if error:
        return error

    payload = _project(_json_safe(sub))
    if ((payload.get("resume_link") or "").strip()
            and not (payload.get("resume_text") or "").strip()):
        payload["cv_fetch_status"] = "cv_cannot_be_fetched"
        payload["evaluation"] = None
        payload["decision"] = {
            "status": "pending",
            "reason": "cv_cannot_be_fetched",
            "source": "auto",
        }
    payload["managers"] = store.get_role_managers(sub.get("job_id"))
    return jsonify(payload)


@app.route("/api/evaluations/rubric/<int:job_id>")
def api_rubric(job_id: int):
    """
    The standard this role's candidates are marked against: the family's
    scoring grid from the rubric pack -- four blocks, every criterion with its
    weight and its 5 / 3 / 1 anchors -- plus the auto-fails, the two-minute
    triage, the GIA overlay, the reviewer notes and the known gaps.

    Reads the pack or the derived grid file; derives nothing. A role with
    neither still returns the fixed architecture (blocks, bands, routing) with
    exists=false, so a reviewer can see the shape of the marking before
    spending a model call.

    `?tier=` picks between a family's grids where it has more than one, which
    today is the AI Strategist pair and nothing else. The payload's `tiers`
    lists what is available and `tier` says which is being shown, so a page
    that never sends the parameter still gets a complete, correct answer.
    """
    error = _mongo_guard() or _role_guard(job_id)
    if error:
        return error

    role = store.get_role(job_id)
    tier = (request.args.get("tier") or "").strip() or None
    detail = evaluator.rubric_detail(role, tier)
    detail["role"] = {"id": role["_id"], "title": role.get("title"),
                      "slug": role.get("slug")}
    detail["assessment_name"] = (role.get("assessment") or {}).get("name")
    detail["assessment_url"] = (role.get("assessment") or {}).get("url")
    # How this role's candidates actually divide across those grids, which is
    # the number that says whether the tier mapping has ever been run: all of
    # them "unresolved" means everyone is being marked against the default.
    if detail.get("tiers"):
        detail["tier_counts"] = store.tier_counts(job_id)
    return jsonify(detail)


@app.route("/api/evaluations/rubric", methods=["POST"])
def api_derive_rubric():
    """
    Derive a pack-shaped grid for a role the pack does not cover -- one model
    call, written to assessments/grid-<slug>.json.

    Pack-covered roles are refused rather than regenerated: their grid is
    hand-authored against the live task content, and replacing it with model
    output would throw that away silently. Edit rubric_pack/_grids.py to move
    that bar.

    force=true regenerates over an existing derived file, which discards any
    hand edits, so the UI asks first. Scores already on record were marked
    against the old grid; every evaluation stores its grid_version so they can
    be told apart and re-graded.
    """
    error = _mongo_guard()
    if error:
        return error

    if not evaluator.is_configured():
        return jsonify({
            "error": "AI evaluation is not configured. Set LLM_API_KEY in .env."
        }), 503

    body = request.get_json(silent=True) or {}
    job_id = body.get("job_id")
    force = bool(body.get("force"))

    if not isinstance(job_id, int):
        return jsonify({"error": "job_id must be a number."}), 400
    error = _role_guard(job_id)
    if error:
        return error
    role = store.get_role(job_id)

    covered = rubric_pack.for_slug(role.get("slug"))
    if covered:
        return jsonify({
            "error": f"{role.get('title')} is covered by the "
                     f"{covered['unit']} grid in the rubric pack, which is "
                     f"hand-authored from the live assessment. Edit "
                     f"backend/grading/rubric_pack/_grids.py to change that "
                     f"standard."
        }), 409

    # Shares the run lock with grading and scanning: all three are slow
    # outbound calls, and two grid writes for one role would race on the file.
    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        grid = evaluator.derive_grid(role, force=force)
    except (evaluator.EvaluationFailed, evaluator.EvaluatorNotConfigured) as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("grid derivation failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    detail = evaluator.rubric_detail(role)
    detail["role"] = {"id": role["_id"], "title": role.get("title"),
                      "slug": role.get("slug")}
    repairs = grid.get("repairs") or []
    detail["message"] = (
        f"Grid {'regenerated' if force else 'written'} for "
        f"{role.get('title')} ({detail['path']})."
        + (f" {'; '.join(repairs)}." if repairs else "")
    )
    return jsonify(detail)


@app.route("/api/evaluations/rejected")
def api_rejected():
    """
    Every rejected candidate, for sending rejection emails in one batch.

    `job_id` narrows to a single role. Addresses are de-duplicated across
    roles, so a candidate who sat two assessments appears once.

    EVERY ROW SAYS WHETHER THAT PERSON HAS ALREADY BEEN TOLD. Without it this
    list only knows who the assessment rejected, not who has heard about it --
    so the next round, when twenty new people land in it, "select all" would
    hand back the twenty new ones AND the two hundred who were mailed last
    month. That is a second rejection for two hundred people, from a list that
    looked correct.

    Answered from the rejection ledger in one query. `already_told` is the flag
    the page unticks on; `told_at` and `told_how` are there because "already
    told" is a claim somebody will want to check.
    """
    error = _mongo_guard()
    if error:
        return error

    job_id = request.args.get("job_id", type=int)
    # Without a job_id this is every rejected candidate in the company. Scoped
    # in the query rather than filtered afterwards, and a job_id outside the
    # scope is refused rather than quietly widened back out.
    if job_id is not None:
        error = _role_guard(job_id)
        if error:
            return error
    rows = [_json_safe(r) for r in
            store.list_rejected(job_id=job_id, job_ids=_scope())]

    told = store.rejections_for(r.get("candidate_email") for r in rows)

    # Tally by reason so the UI can say what the rule actually caught.
    reasons: dict[str, int] = {}
    for row in rows:
        reason = (row.get("decision") or {}).get("reason") or "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1

        entry = told.get(store.clean_email(row.get("candidate_email")))
        # A `failed` row is NOT already told. We tried and it bounced, so that
        # candidate is still waiting to hear -- putting them back in the list is
        # the whole reason failures are kept rather than dropped.
        row["already_told"] = bool(
            entry and entry.get("status") in store.REJECTION_DELIVERED)
        if entry:
            row["told_at"] = (entry.get("rejected_at").isoformat()
                              if isinstance(entry.get("rejected_at"), datetime)
                              else entry.get("rejected_at"))
            row["told_how"] = entry.get("status")

    waiting = sum(1 for r in rows if not r["already_told"])
    return jsonify({
        "candidates": rows,
        "total": len(rows),
        "reasons": reasons,
        # Split out rather than left for the page to count, so the two surfaces
        # that show this list cannot disagree about how many people are owed a
        # rejection.
        "already_told": len(rows) - waiting,
        "waiting": waiting,
    })


@app.route("/api/evaluations/decision", methods=["POST"])
def api_decision():
    """
    Override a decision by hand -- pull someone out of the reject box, or
    reject someone the rule let through.

    Recorded with source "manual", which ingest.py will not overwrite.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    status = body.get("status")

    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400
    if status not in ("pending", "rejected", "scored", "in_progress"):
        return jsonify({"error": f"Unknown status: {status}"}), 400
    error = _submission_guard(store.get_submission(submission_id), submission_id)
    if error:
        return error

    store.set_decision(submission_id, status,
                       body.get("reason") or "manual_override", "manual")
    return jsonify({"message": f"Moved {submission_id} to {status}.",
                    "submission": _json_safe(store.get_submission(submission_id))})


@app.route("/api/evaluations/tiers/resolve", methods=["POST"])
def api_resolve_tiers():
    """
    Work out which posting each of a role's candidates applied to.

    The two dashboards on a tiered assignment are a filter over one pile of
    submissions, and the portal's export carries nothing that says which
    posting anyone came from -- only Workable does. This is what fills that in:
    one paginated candidate list per posting, matched on email, written onto
    the submission. Until it has run, every candidate sits on the default
    tier's card, which is the honest place for them because that is also the
    grid they would be graded against.

    Only what is unresolved, unless `force`. A tier a reviewer set by hand is
    never touched either way -- see store.set_rubric_tier.

    Changes no scores. A candidate already graded keeps the verdict the old
    tier produced until they are re-graded, and the response says how many that
    is so nobody reads a moved card as a re-marked one.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    job_id = body.get("job_id")
    if not isinstance(job_id, int):
        return jsonify({"error": "job_id must be a number."}), 400

    error = _role_guard(job_id)
    if error:
        return error
    role = store.get_role(job_id)

    tiers, _ = _tier_options(role)
    if not tiers:
        return jsonify({
            "error": f"{role.get('title')} is marked by one grid, so there is "
                     f"nothing to resolve."
        }), 409

    # Shares the run lock with grading and ingest: it is the same kind of slow
    # outbound work, and two of these at once would fetch both candidate lists
    # twice to reach the same answer.
    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        result = tier_resolver.resolve_role(role, store,
                                            force=bool(body.get("force")))
    except tier_resolver.TierResolutionFailed as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("tier resolution failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    counts = store.tier_counts(job_id)
    parts = [f"Matched {result['written']} candidate(s) to a posting."]
    if result["unresolved"]:
        parts.append(f"{result['unresolved']} not found on either posting; "
                     f"they stay on the default tier.")
    if result["both"]:
        parts.append(f"{result['both']} applied to both — check before "
                     f"rejecting them on the background row.")
    return jsonify({"message": " ".join(parts), **result,
                    "tier_counts": counts})


@app.route("/api/evaluations/tier", methods=["POST"])
def api_set_tier():
    """
    Move one candidate to the other tier of their family's rubric, by hand.

    Both AI Strategist rubrics ask for this in as many words: a new graduate
    who applied to the senior posting and submitted genuinely strong work is
    graded on the associate grid rather than rejected on senior background
    anchors, and an applicant to the associate posting with six years behind
    them moves the other way. The instruction in both is to make the swap and
    note it on the file, which is what `source: "manual"` is -- the resolver
    reads that flag and leaves those candidates alone on every later run.

    Does not regrade. A tier change moves the standard, not the score, and the
    score it produced was real under the old one; re-run grading for this
    candidate to mark them against the new grid. Send tier=null to clear a
    swap and hand the candidate back to the resolver.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    tier = body.get("tier")

    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400

    submission = store.get_submission(submission_id)
    error = _submission_guard(submission, submission_id)
    if error:
        return error

    role = store.get_role(submission.get("job_id"))
    available = rubric_pack.tiers_for_slug((role or {}).get("slug"))
    if not available:
        return jsonify({
            "error": f"{(role or {}).get('title') or 'This role'} is marked by "
                     f"one grid, so there is no other tier to move to."
        }), 409
    if tier is not None and tier not in available:
        return jsonify({
            "error": f"Unknown tier {tier!r}. This role is marked at: "
                     f"{', '.join(available)}."
        }), 400

    store.set_rubric_tier(submission_id, tier, source="manual",
                          note=(body.get("note") or "").strip())
    name = submission.get("candidate_name") or submission_id
    message = (f"{name} will be marked against the {tier} grid. Re-grade to "
               f"apply it." if tier else
               f"Cleared {name}'s tier. The next resolver run will set it from "
               f"the posting they applied to.")
    return jsonify({"message": message, "tier": tier,
                    "tiers": list(available),
                    "submission": _json_safe(store.get_submission(submission_id))})


@app.route("/api/pipeline")
def api_pipeline():
    """
    The board: who is booked for an interview, who was hired, who was turned
    down after being seen.

    `stage` narrows to one of those; omit it for all three. `job_id` narrows to
    a role. Counts always come back for every stage, so the tabs can show their
    totals without three more requests.
    """
    error = _mongo_guard()
    if error:
        return error

    stage = request.args.get("stage") or None
    job_id = request.args.get("job_id", type=int)

    if stage is not None and stage not in store.PIPELINE_STAGES:
        return jsonify({"error": f"Unknown stage: {stage}"}), 400
    if job_id is not None:
        error = _role_guard(job_id)
        if error:
            return error

    scope = _scope()
    rows = _project([_json_safe(r) for r in
                     store.list_pipeline(stage=stage, job_id=job_id,
                                         job_ids=scope)])
    counts = store.pipeline_counts()
    if scope is not None:
        counts = _scoped_stage_counts(counts, scope)
    return jsonify({
        "stage": stage,
        "candidates": rows,
        "total": len(rows),
        "counts": counts,
        "stages": list(store.PIPELINE_STAGES),
    })


# --------------------------------------------------------------------------
# Grading and portal ingest runs  (was views_grading.py)
# --------------------------------------------------------------------------
# Grading and portal ingest, from the dashboard.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

def _grade_one(submission_id: int):
    """
    Evaluate one named candidate, whatever queue they are in.

    The role-level route only ever reaches `pending` submissions, which is right
    for a backfill and wrong for a reviewer who wants a score for one particular
    person: the candidate they are curious about is often precisely the one the
    queue skipped -- auto-rejected for a missing artefact, or already scored and
    worth a second opinion after a rubric edit.

    So the decision status is deliberately not consulted. The one thing still
    refused is a submission with no answer text, because there is nothing to
    mark and the model would be paid to say so.
    """
    submission = store.get_submission(submission_id)
    error = _submission_guard(submission, submission_id)
    if error:
        return error

    if not (submission.get("submission_markdown") or "").strip():
        return jsonify({
            "error": "This submission has no answer text, so there is nothing "
                     "to grade. Candidates who started but never submitted have "
                     "no answer on the portal."
        }), 400

    role = store.get_role(submission.get("job_id"))
    if role is None:
        return jsonify({
            "error": f"No role with job id {submission.get('job_id')}. "
                     f"Run `python ingest.py --roles-only` first."
        }), 404

    was_scored = bool(submission.get("evaluation"))

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        grid = evaluator.derive_grid(role)
        tier_resolver.ensure_resolved(role, store)
        submission = store.get_submission(submission_id) or submission
        verdict = grader.grade_and_store(submission, role, grid)
    except (evaluator.EvaluationFailed, evaluator.EvaluatorNotConfigured) as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("single-candidate grading failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    name = submission.get("candidate_name") or submission.get("candidate_email") or submission_id
    return jsonify({
        "message": f"{'Re-scored' if was_scored else 'Scored'} {name}: "
                   f"{verdict['score']:.1f} — {verdict['recommendation']}.",
        "graded": [{"submission_id": submission_id,
                    "candidate_name": submission.get("candidate_name"),
                    **verdict}],
        "failed": [],
        "remaining": len(store.ungraded(job_id=role["_id"])),
        "submission": _json_safe(store.get_submission(submission_id)),
    })


@app.route("/api/evaluations/grade", methods=["POST"])
def api_grade():
    """
    Grade submissions with the AI evaluator.

    Two modes. `submission_id` grades that one candidate on demand, whatever
    state they are in -- see _grade_one(). `job_id` walks the role's pending
    queue, which is the bulk path.

    Runs inline, so keep `limit` small from the dashboard -- a whole role can be
    hundreds of model calls. Use grade.py for a full backfill.
    """
    error = _mongo_guard()
    if error:
        return error

    if not evaluator.is_configured():
        return jsonify({
            "error": "AI evaluation is not configured. Set LLM_API_KEY in .env."
        }), 503

    body = request.get_json(silent=True) or {}
    job_id = body.get("job_id")
    limit = body.get("limit", 5)

    submission_id = body.get("submission_id")
    if submission_id is not None:
        if not isinstance(submission_id, int):
            return jsonify({"error": "submission_id must be a number."}), 400
        return _grade_one(submission_id)

    if not isinstance(job_id, int):
        return jsonify({"error": "job_id or submission_id must be a number."}), 400
    try:
        limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be a number."}), 400

    error = _role_guard(job_id)
    if error:
        return error
    role = store.get_role(job_id)

    # Which half of a two-tier role this run is for. The dashboard sends it
    # because the two cards each have their own Grade button and each should
    # walk its own queue -- a senior run that quietly graded twelve associate
    # candidates would leave the other card looking done without anyone having
    # read it.
    tier, default_tier, error = _tier_arg(role, body.get("tier"))
    if error:
        return error

    # Shares the reminder lock: both paths make slow outbound calls, and one
    # long grading run should not overlap a scan.
    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        grid = evaluator.derive_grid(role)
        # Which posting each candidate applied to, where that decides which of
        # a family's grids they are marked against. No-op for every role but
        # the AI Strategist pair, and best-effort even there: an unresolved
        # candidate is graded against the default grid rather than skipped.
        tier_resolver.ensure_resolved(role, store)
        pending = store.ungraded(job_id=job_id, limit=limit, tier=tier,
                                 default_tier=default_tier)
        graded, failed = [], []

        # Concurrent, because on a queueing provider the wait is idle time
        # rather than work: the calls sit behind each other for no reason when
        # run single-file. grade.py has done it this way for the CLI path all
        # along; this brings the dashboard's batch into line.
        def one(sub):
            return sub, grader.grade_and_store(sub, role, grid)

        with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
            futures = [pool.submit(one, sub) for sub in pending]
            for future in as_completed(futures):
                try:
                    sub, verdict = future.result()
                except evaluator.EvaluationFailed as exc:
                    failed.append({"error": str(exc)})
                    continue
                graded.append({
                    "submission_id": sub["_id"],
                    "candidate_name": sub.get("candidate_name"),
                    **verdict,
                })
    except (evaluator.EvaluationFailed, evaluator.EvaluatorNotConfigured) as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("grading failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    remaining = len(store.ungraded(job_id=job_id, tier=tier,
                                   default_tier=default_tier))
    message = f"Graded {len(graded)} submission(s); {remaining} still pending."
    if failed:
        message += f" {len(failed)} failed."
    return jsonify({"message": message, "graded": graded,
                    "failed": failed, "remaining": remaining})


@app.route("/api/evaluations/ingest", methods=["POST"])
def api_ingest():
    """
    Re-crawl the portal into Mongo.

    Skips the roles crawl by default: it is ~52 admin page fetches and the
    assessments rarely change, whereas submissions do.

    Pulls the review queues named in INGEST_REVIEW_BUCKETS -- the untouched one
    and Pending Review. `review_status` in the body overrides that for a one-off
    sync; an unknown queue is refused rather than passed to the portal, which
    would answer it with the default rows and a 200.
    """
    # Admin only. This is a full re-crawl of the portal on behalf of the whole
    # company -- roughly a minute of scraping that rewrites every role's
    # submissions. It is not a per-role action a manager should be able to fire
    # from their own seat, however harmless the button looks.
    error = _mongo_guard() or _require_admin()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    include_roles = bool(body.get("include_roles"))

    buckets = body.get("review_status") or None
    if isinstance(buckets, str):
        buckets = buckets.split(",")
    try:
        ingest.resolve_buckets(buckets)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        summary = ingest.run(skip_roles=not include_roles, review_buckets=buckets)
    except Exception as exc:
        log.exception("ingest failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    subs = summary.get("submissions", {})
    screening = summary.get("screening", {})
    message = (
        f"Synced {subs.get('downloaded', 0)} record(s): "
        f"{subs.get('new', 0)} new. "
        f"{screening.get('pending', 0)} pending, "
        f"{screening.get('rejected', 0)} auto-rejected."
    )
    # Said out loud rather than left in the log: a short queue looks exactly
    # like a quiet week from the dashboard.
    failed = subs.get("failed_queues") or []
    if failed:
        message += (f" The {', '.join(failed)} queue(s) would not download -- "
                    f"those candidates are missing from this sync. Sync again.")
    return jsonify({"message": message, "summary": summary})


# --------------------------------------------------------------------------
# Pipeline stages, and the mail that announces one  (was views_pipeline.py)
# --------------------------------------------------------------------------
# The hiring pipeline, and who may start an interview.
#
# Stage moves, the mail each move does or does not send, and the preview.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

@app.route("/api/pipeline", methods=["POST"])
def api_set_pipeline():
    """
    Move one candidate along the pipeline.

    Body: {submission_id, stage, interview_at?, interviewer?, note?, reason?,
           notify?, cal_link?, manager_email?, email_note?, resend?}.
    `stage: null` pulls them back out to the shortlist, keeping the history.

    `stage: "interview"` IS REFUSED HERE. It is the hiring manager's move, made
    on their review link -- see INTERVIEW_IS_THE_MANAGERS above. The other
    stages, and the pull back out, are unchanged.

    THE MOVE DOES NOT EMAIL ANYONE while PIPELINE_AUTO_EMAIL is off, which is
    how the system ships. The board records where somebody is; the candidate
    hears about it from a Send click in their drawer, after a person has read
    the preview. /api/pipeline/send is that click.

    `notify` is still honoured when it is passed outright -- that is what the
    Send button does under the covers -- and omitting it means "whatever the
    automation switch says", so setting PIPELINE_AUTO_EMAIL=1 puts the send
    back on the move without a line of this changing.

    `email_note` is what the candidate reads. It is a DIFFERENT FIELD from
    `note`/`reason` on purpose: those are internal, the drawer labels them
    "anything the next reader needs", and quietly forwarding a reviewer's
    private remark to the person it is about is the kind of leak that ends a
    feature. Nothing reaches the candidate unless it was typed into the box
    that says so.

    Deliberately does not touch `decision` or `evaluation`: a hire is a fact
    about the person, not a re-marking of their assessment, and the score that
    got them here has to stay readable next to the outcome.

    The stage move is committed before the send, and is not rolled back if the
    send fails. Where a candidate stands is a fact about the process; a Brevo
    outage should not silently un-reject someone, so a failed mail comes back
    as a warning on a move that did happen.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    stage = body.get("stage")

    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400
    if stage is not None and stage not in store.PIPELINE_STAGES:
        return jsonify({"error": f"Unknown stage: {stage}"}), 400

    # Refused before the document is even read, so a blocked call leaves no
    # trace on the board and no half-written interview to tidy up.
    if stage == "interview":
        return jsonify({"error": INTERVIEW_IS_THE_MANAGERS,
                        "needs": "manager_review"}), 403

    submission = store.get_submission(submission_id)
    error = _submission_guard(submission, submission_id)
    if error:
        return error

    # Text fields are stored as given; the datetime is a string from a
    # datetime-local input, kept verbatim so it means the wall-clock time the
    # interviewer typed rather than a timezone we guessed for them.
    def field(name: str) -> str | None:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    role = store.get_role(submission.get("job_id")) or {}
    cal_link = field("cal_link") or ""
    manager_email = field("manager_email") or ""
    # Unasked means "follow the switch", and the switch is off: a move is a
    # move. Passed outright it is obeyed either way, which is how the drawer's
    # Send button gets a mail out of this route while nothing else does.
    notify = body.get("notify")
    if notify is None:
        notify = (candidate_mail.PIPELINE_AUTO_EMAIL
                  and candidate_mail.stage_is_mailed(stage))
    else:
        notify = bool(notify)

    # No booking-link pre-check here any more: the only stage that needed one
    # is refused above, and a dead check left behind reads like a live one.

    # A link typed at the moment of booking is kept on the manager who owns it,
    # so the next candidate costs a click rather than a paste. Stored
    # account-wide -- one manager, one calendar, however many seats they own.
    if cal_link and manager_email:
        store.set_manager_cal_link(manager_email, cal_link)

    store.set_pipeline_stage(
        submission_id, stage,
        interview_at=field("interview_at"),
        interviewer=field("interviewer"),
        note=field("note"),
        reason=field("reason"),
    )

    name = submission.get("candidate_name") or f"submission {submission_id}"
    said = {"hired": "marked hired",
            "rejected": "marked rejected"}.get(stage, "returned to the shortlist")
    message = f"{name} {said}."

    mail: dict = {"sent": False, "reason": "Not requested."}
    if notify:
        # Re-read, so the invitation quotes back the interview time that was
        # just written rather than the one it replaced.
        moved = store.get_submission(submission_id) or submission
        try:
            mail = candidate_mail.send_stage_email(
                moved, role, stage,
                cal_link=cal_link,
                interviewer=field("interviewer") or "",
                manager_email=manager_email,
                note=field("email_note") or "",
                force=bool(body.get("resend")),
            )
        except candidate_mail.CandidateMailError as exc:
            mail = {"sent": False, "reason": str(exc)}
        except Exception as exc:
            log.exception("stage mail failed")
            mail = {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}

        message += (f" Emailed {mail['to']}." if mail.get("sent")
                    else f" Not emailed: {mail.get('reason', 'no reason given')}")
    elif candidate_mail.stage_is_mailed(stage):
        # Said out loud on every silent move. The board now records and the
        # sending is a separate click, and a manager who assumed otherwise
        # would leave a candidate waiting on an email nobody asked for.
        message += " Nobody was emailed — open their card and click Send."

    return jsonify({
        "message": message,
        "mail": mail,
        "counts": store.pipeline_counts(),
        "submission": _json_safe(store.get_submission(submission_id)),
    })


@app.route("/api/pipeline/preview")
def api_pipeline_preview():
    """
    The candidate email for a stage move, exactly as it would be sent.

    `submission_id` and `stage` are required; `cal_link`, `manager_email`,
    `interviewer` and `email_note` mirror the POST body, so a manager reads the
    real message with their own link in it before anyone clicks send.

    Rendered by the same builder the send uses -- a preview from a second
    template is a preview of nothing.
    """
    error = _mongo_guard()
    if error:
        return error

    submission_id = request.args.get("submission_id", type=int)
    stage = request.args.get("stage") or ""
    if submission_id is None:
        return jsonify({"error": "submission_id is required."}), 400
    if not candidate_mail.stage_is_mailed(stage):
        return jsonify({
            "error": f"No candidate email is sent for {stage or 'this move'}."
        }), 400

    submission = store.get_submission(submission_id)
    error = _submission_guard(submission, submission_id)
    if error:
        return error

    role = store.get_role(submission.get("job_id")) or {}
    try:
        email = candidate_mail.build_stage_email(
            submission, role, stage,
            cal_link=request.args.get("cal_link") or "",
            interviewer=request.args.get("interviewer") or "",
            manager_email=request.args.get("manager_email") or "",
            note=request.args.get("email_note") or "",
        )
    except candidate_mail.CandidateMailError as exc:
        return jsonify({"error": str(exc)}), 409

    already = candidate_mail.already_sent(
        submission, stage, request.args.get("cal_link") or "")
    return jsonify({
        "email": {key: email[key] for key in ("subject", "html", "text")},
        "to": email["to"],
        "to_name": email["to_name"],
        "cal_link": email["cal_link"],
        "manager": email["manager"],
        "already_sent": _json_safe(already) if already else None,
    })


@app.route("/api/pipeline/send", methods=["POST"])
def api_send_stage_email():
    """
    Send one candidate their stage email, because somebody clicked Send.

    Body: {submission_id, stage?, cal_link?, manager_email?, interviewer?,
           email_note?, resend?}. `stage` defaults to where the candidate
           already is, so the ordinary case is "send this person the thing
           their card says they are owed".

    Rejections only. The interview invitation is the manager's to send, from
    the composer on their review link -- see INTERVIEW_IS_THE_MANAGERS.

    /api/pipeline moves people and stays quiet; this one sends and moves
    nobody. Splitting them is the whole point of the manual mode: the board can
    be corrected all afternoon without a single mail going out, and the mail
    that does go out was read in the preview a second earlier.

    The same builder and the same duplicate suppression as the automatic path,
    so switching PIPELINE_AUTO_EMAIL on later changes when the send happens and
    nothing about what is sent.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400

    submission = store.get_submission(submission_id)
    error = _submission_guard(submission, submission_id)
    if error:
        return error

    stage = body.get("stage") or (submission.get("pipeline") or {}).get("stage")
    if not candidate_mail.stage_is_mailed(stage):
        return jsonify({
            "error": f"No candidate email is sent for "
                     f"{stage or 'a candidate who is not in the pipeline'}."
        }), 400

    # An invitation from here would be signed by a manager who never wrote it.
    # Refused for the same reason the move is -- INTERVIEW_IS_THE_MANAGERS.
    if stage == "interview":
        return jsonify({"error": INTERVIEW_IS_THE_MANAGERS,
                        "needs": "manager_review"}), 403

    def field(name: str) -> str:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    role = store.get_role(submission.get("job_id")) or {}
    cal_link = field("cal_link")
    manager_email = field("manager_email")
    interviewer = field("interviewer") or (submission.get("pipeline") or {}).get("interviewer") or ""

    # Kept on the manager who owns it, same as the move route: a link typed
    # once should cost the next candidate a click rather than a paste.
    if cal_link and manager_email:
        store.set_manager_cal_link(manager_email, cal_link)

    try:
        mail = candidate_mail.send_stage_email(
            submission, role, stage,
            cal_link=cal_link,
            interviewer=interviewer,
            manager_email=manager_email,
            note=field("email_note"),
            force=bool(body.get("resend")),
        )
    except candidate_mail.CandidateMailError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        log.exception("manual stage mail failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    name = submission.get("candidate_name") or f"submission {submission_id}"
    message = (f"Sent {name} the rejection at {mail['to']}." if mail.get("sent")
               else f"Not sent: {mail.get('reason', 'no reason given')}")

    return jsonify({
        "message": message,
        "mail": mail,
        "submission": _json_safe(store.get_submission(submission_id)),
    })
