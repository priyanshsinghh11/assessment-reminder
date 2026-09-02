"""
The hand-off, and everything that leaves the building because of it.

The top N for a role, the managers it goes to, the review links that carry it,
the spreadsheet, and the bulk rejection sender with the ledger of who has
already been told.

WAS THREE MODULES -- views_shortlist.py, views_links.py and
views_rejections.py. Grouped here because they share one question: who outside
this system is about to receive something. Read this file before changing
anything a real person gets. Each former module's own notes are kept below, as
the section banners.
"""

import secrets
import threading

from flask import Response, jsonify, request
from datetime import datetime, timezone

from backend.config import (AUTH_ENABLED, SHORTLIST_SHOW_SCORES,
                            SHORTLIST_SIZE, PUBLIC_BASE_URL, REVIEW_LINK_DAYS,
                            SHORTLIST_MAX, PIPELINE_EMAILS_ENABLED,
                            REJECTION_MAX_PER_SEND)
from backend.db import store
from backend.mail import shortlist, rejections, unsubscribe as unsubscribe_mod

from backend.web.app import (_is_admin, _json_safe, _mongo_guard,
                             _require_admin, _role_guard, _run_lock, _tier_arg,
                             app, log, INTERVIEW_IS_THE_MANAGERS,
                             _current_user, _scope, RUN_JOB_MAX,
                             RUN_JOB_RETENTION)


# --------------------------------------------------------------------------
# The top N, the managers, the spreadsheet, the send  (was views_shortlist.py)
# --------------------------------------------------------------------------
# Hiring managers and shortlists.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hiring managers and shortlists
#
# The end of the funnel: who owns a seat, and the top-N hand-off that goes to
# them once its assessments are graded. Scores never leave the building through
# these routes -- see the note at the top of shortlist.py.
# ---------------------------------------------------------------------------

@app.route("/api/roles/<int:job_id>/managers", methods=["GET", "POST"])
def api_role_managers(job_id: int):
    """
    Read or replace the hiring managers who own a role.

    POST takes the whole list -- {"managers": [{name, email, title, cal_link},
    ...]} -- and replaces what was there. Removing someone is sending the list
    without them, so the editor never has to reason about a separate delete
    call landing out of order with a save.

    `cal_link` is the manager's own cal.com page, and it is the link a
    candidate is sent when this manager moves them to Interview. Stored per
    manager rather than per role: one person books every seat they own out of
    the same calendar.

    WRITING THIS LIST IS AN ADMIN ACTION, and it is the one place where that
    matters most: this list IS the access rule. A manager who could POST here
    could add their own address to a role and read it a second later, which
    would make every other check on this page decorative. Reading it is fine
    for anyone who can already see the role -- it is who to talk to.
    """
    error = _mongo_guard() or _role_guard(job_id)
    if error:
        return error

    role = store.get_role(job_id)

    if request.method == "GET":
        return jsonify({"job_id": job_id, "title": role.get("title"),
                        "managers": store.get_role_managers(job_id)})

    error = _require_admin()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    managers = body.get("managers")
    if not isinstance(managers, list):
        return jsonify({"error": "managers must be a list."}), 400
    if any(not isinstance(m, dict) for m in managers):
        return jsonify({"error": "Each manager must be an object."}), 400

    # Addresses that would not survive normalisation are named rather than
    # dropped quietly: a typo'd address that vanishes on save looks like the
    # save failed, and the recruiter would type it again the same way.
    bad = [str(m.get("email") or "(blank)") for m in managers
           if "@" not in str(m.get("email") or "")]
    if bad:
        return jsonify({
            "error": f"Not an email address: {', '.join(bad[:5])}."
        }), 400

    # Same rule for the booking link, and for the same reason: a link that is
    # silently dropped on save is one the manager believes is stored, right up
    # until a candidate is moved and the send refuses.
    bad_links = [str(m.get("cal_link")) for m in managers
                 if str(m.get("cal_link") or "").strip()
                 and not store.clean_cal_link(m.get("cal_link"))]
    if bad_links:
        return jsonify({
            "error": f"Not a usable booking link: {', '.join(bad_links[:3])}. "
                     f"Paste the full cal.com URL."
        }), 400

    stored = store.set_role_managers(job_id, managers)
    return jsonify({
        "message": (f"{len(stored)} hiring manager(s) on {role.get('title')}."
                    if stored else
                    f"Cleared the hiring managers on {role.get('title')}."),
        "managers": stored,
        "known_managers": store.known_managers(),
        # Editing this list is editing who can open this role. Said in the
        # response so the recruiter reads it where the change happened, not in
        # a doc they would have to already know to go and look for.
        "access_note": (f"{len(stored)} account(s) with these addresses can "
                        f"now open {role.get('title')} in the dashboard."
                        if stored and AUTH_ENABLED else None),
    })


def _scores_arg(value=None):
    """
    Whether this hand-off carries the AI score, and whether the caller may ask.

    Returns (include_scores, error_response). THIS IS WHERE THE POLICY IS
    ENFORCED, not in shortlist.py -- that module builds what it is handed and
    has no idea who is on the other end of the request.

    Three cases, and the middle one is the point:

    * Recruiting team, no answer given -- SHORTLIST_SHOW_SCORES, the default.
    * Recruiting team, ticked or unticked -- their choice, either way. This is
      a per-send call now, and unticking it on an installation that has the
      config flag on has to work as well as ticking it on one that does not.
    * A hiring-manager account -- never, and asking is a 403 rather than a
      quiet list without scores in it. A manager who typed `?scores=1` should
      be told the answer is no; one who did not ask is served the same rows as
      always, which is why a bare request from them is not an error even when
      the config default is on.

    That last branch is the same rule as MANAGER_SUBMISSION_FIELDS, arriving
    through a different door: a manager reads the dashboard's own screens as
    well as their email, and a route that leaked the number here would undo the
    projection the review page does at the database.
    """
    asked = "" if value is None else str(value).strip().lower()
    wanted = (SHORTLIST_SHOW_SCORES if asked == ""
              else asked in ("1", "true", "yes", "on"))
    if _is_admin():
        return wanted, None
    if wanted and asked:
        return False, (jsonify({
            "error": "Scores are the recruiting team's. This list carries rank, "
                     "contact details and links to the work itself.",
            "auth": "forbidden",
        }), 403)
    return False, None


@app.route("/api/shortlist/<int:job_id>")
def api_shortlist(job_id: int):
    """
    Preview a role's top-N hand-off: the rows, and who it would go to.

    `preview=1` also renders the email exactly as it would be sent, so the
    recruiter reads the real thing rather than a description of it.
    """
    error = _mongo_guard() or _role_guard(job_id)
    if error:
        return error

    role = store.get_role(job_id)
    tier, _default_tier, error = _tier_arg(role)
    if error:
        return error
    limit = request.args.get("limit", default=SHORTLIST_SIZE, type=int)
    scores, error = _scores_arg(request.args.get("scores"))
    if error:
        return error
    managers = store.get_role_managers(job_id)
    rows = shortlist.rows(job_id, limit, tier=tier or "", include_scores=scores)
    # Named for the posting, not the assignment, wherever the tier is set --
    # the preview has to read as the mail the manager will actually get.
    role = shortlist.role_view(role, tier or "")

    payload = {
        "role": {"id": job_id, "title": role.get("title"),
                 "slug": role.get("slug"), "tier": tier},
        "managers": managers,
        "limit": limit,
        "candidates": rows,
        "total_scored": len(rows),
        # Scored, but not rankable: the AI stopped part-way through their
        # rubric, so their renormalised total is not comparable with anyone
        # else's. Surfaced in the preview and not only on the send, because
        # "re-grade these three first" is a decision to make BEFORE the mail
        # goes out.
        "held_back": shortlist.held_back(job_id, tier=tier or ""),
        "filename": shortlist.filename(role),
        # What the spreadsheet would carry, so the page can set its own tick
        # from the server's default on first load rather than guessing at it
        # and then contradicting the file the download hands back.
        "show_scores": scores,
        "last_send": _json_safe(role["shortlist_last"]) if role.get("shortlist_last") else None,
    }
    if request.args.get("preview"):
        first = managers[0].get("name") if managers else ""
        payload["email"] = shortlist.build_email(
            role, rows, first, request.args.get("note") or "",
            # No review token here -- this list has not been sent, so there is
            # nobody to mint one for. `preview` draws the button anyway, inert
            # and labelled, so this screen stops reading as an email with no
            # link in it when the delivered one is the opposite.
            dashboard_url=shortlist.dashboard_link(job_id), preview=True)
    return jsonify(payload)


@app.route("/api/shortlist/<int:job_id>/xlsx")
def api_shortlist_xlsx(job_id: int):
    """
    Download the same spreadsheet the email would attach.

    The escape hatch for a manager who is not in the system yet: the recruiter
    grabs the sheet and sends it themselves, and it is the identical file.
    """
    error = _mongo_guard() or _role_guard(job_id)
    if error:
        return error

    role = store.get_role(job_id)
    tier, _default_tier, error = _tier_arg(role)
    if error:
        return error
    limit = request.args.get("limit", default=SHORTLIST_SIZE, type=int)
    scores, error = _scores_arg(request.args.get("scores"))
    if error:
        return error
    rows = shortlist.rows(job_id, limit, tier=tier or "", include_scores=scores)
    role = shortlist.role_view(role, tier or "")
    if not rows:
        return jsonify({
            "error": f"{role.get('title')} has no scored candidates yet."
        }), 409

    try:
        blob = shortlist.build_xlsx(role, rows)
    except shortlist.ShortlistError as exc:
        return jsonify({"error": str(exc)}), 503

    name = shortlist.filename(role)
    return Response(
        blob,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.route("/api/shortlist/send", methods=["POST"])
def api_send_shortlist():
    """
    Mail a role's shortlist to its hiring managers.

    Body: {job_id, limit?, note?, to?}. `to` overrides the stored managers for
    a test send to yourself -- it does not change who owns the role.

    Shares the run lock with grading, ingest and grid derivation: two clicks
    on send would otherwise put the same twenty candidates in a manager's inbox
    twice, which is exactly the mistake the lock exists to stop.
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

    limit = body.get("limit") or SHORTLIST_SIZE
    if not isinstance(limit, int) or limit < 1:
        return jsonify({"error": "limit must be a positive number."}), 400

    tier, _default_tier, error = _tier_arg(store.get_role(job_id),
                                           body.get("tier"))
    if error:
        return error

    # The recruiter's tick, carried from the screen they previewed on. Refused
    # rather than ignored for a manager account, same as `to` below: this route
    # mails a file, and a manager who could set this would be mailing
    # themselves the scores the rest of the product keeps from them.
    scores, error = _scores_arg(body.get("include_scores"))
    if error:
        return error

    override = body.get("to")
    if override is not None and not isinstance(override, list):
        return jsonify({"error": "to must be a list of recipients."}), 400
    # `to` mails the shortlist -- names, addresses, CV links -- to whoever is
    # named in it. That is a recruiter's test-send-to-myself, and in a hiring
    # manager's hands it would be a way to forward a candidate list anywhere
    # they liked without leaving the page. Without the override the send goes
    # to the role's own managers, which is the only address list this is for.
    if override is not None and not _is_admin():
        return jsonify({
            "error": "Only the recruiting team can redirect a shortlist send. "
                     "Sending without `to` mails this role's hiring managers.",
            "auth": "forbidden",
        }), 403

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        result = shortlist.send_shortlist(
            job_id, limit=limit, recipients=override,
            note=str(body.get("note") or ""), tier=tier or "",
            include_scores=scores,
        )
    except shortlist.ShortlistError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        log.exception("shortlist send failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    sent, failed = result["sent"], result["failed"]
    if not sent:
        # Every recipient failed. Reported as an error rather than a summary,
        # because nothing was delivered and the recruiter must act again.
        return jsonify({
            "error": "Nothing was sent. " +
                     "; ".join(f["error"] for f in failed[:3]),
            "result": result,
        }), 502

    message = (f"Sent the top {result['count']} for {result['role']} to "
               f"{', '.join(sent)}.")
    # Said out loud on the one screen that reports the send. Handing a manager
    # the scores is a decision worth seeing confirmed after the mail has gone,
    # not only in a tickbox before it.
    if result.get("scores"):
        message += " The attached sheet carries the AI score."
    if failed:
        message += (f" Failed for {', '.join(f['email'] for f in failed)} -- "
                    f"{failed[0]['error']}")
    return jsonify({"message": message, "result": result})


# --------------------------------------------------------------------------
# Review links and manager booking links  (was views_links.py)
# --------------------------------------------------------------------------
# Review links, from the recruiter's side.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Review links, from the recruiter's side
# ---------------------------------------------------------------------------

@app.route("/api/shortlist/<int:job_id>/links")
def api_review_links(job_id: int):
    """Live and dead review links for a role, so a recruiter can revoke one."""
    error = _mongo_guard() or _role_guard(job_id)
    if error:
        return error

    links = []
    for link in store.list_review_links(job_id=job_id):
        links.append({
            "token": link["_id"],
            "url": shortlist.review_link(link["_id"]),
            "manager": link["manager"],
            "state": store.review_link_state(link),
            "created_at": link["created_at"].isoformat(),
            "expires_at": link["expires_at"].isoformat() if link.get("expires_at") else None,
            "opened_at": link["opened_at"].isoformat() if link.get("opened_at") else None,
            "views": link.get("views", 0),
            "actions": len(link.get("actions") or []),
            "candidates": len(link.get("submission_ids") or []),
        })
    return jsonify({"links": links, "public_base_url": PUBLIC_BASE_URL,
                    "unreachable": shortlist.is_loopback(PUBLIC_BASE_URL),
                    "link_days": REVIEW_LINK_DAYS})


# NOT under /api/review/. That prefix is the TOKEN surface: _guard_auth skips
# it on purpose, because the review page carries its own credential in the URL
# and has no accounts behind it. An endpoint that needs to know who is signed
# in therefore cannot live there -- it would run with g.user unset, and the
# first thing it hit would be _role_guard answering 404 for a role the caller
# genuinely owns. It sits beside /api/managers/cal-link instead, which is the
# other route scoped to "the manager who is asking".
@app.route("/api/managers/review-link", methods=["POST"])
def api_my_review_link():
    """
    A signed-in hiring manager's own review workspace for a role they own.

    Body: {job_id, limit?}. Returns {url, token, count} -- the page where they
    read the candidates, pick who to meet, and send the invitation.

    WHY THIS EXISTS. Moving somebody to interview is deliberately the manager's
    move and nobody else's (see INTERVIEW_IS_THE_MANAGERS), and the composer
    that writes the invitation lives on the review page. That page was only
    ever reachable through a token mailed by a recruiter -- so a manager who
    had archived the email, or joined the role after the last send, could not
    invite anybody until somebody re-sent them a shortlist. This is the same
    workspace, asked for by someone the server can already identify, rather
    than a second copy of the composer bolted onto the dashboard.

    ENTITLEMENT IS BY NAME ON THE ROLE, NOT BY ACCOUNT TYPE. The check is
    "is the caller in this role's hiring_managers list", which is a stricter
    question than _role_guard's "may the caller see this role" -- an admin sees
    every role and must NOT be able to mint themselves an invite workspace,
    because the invitation is signed with the manager's name and points at the
    manager's calendar. An admin who is genuinely also a hiring manager on the
    role is on that list, and passes for the right reason.
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

    user = _current_user()
    address = (user or {}).get("_id") or ""
    role = store.get_role(job_id) or {}
    mine = next((m for m in (role.get("hiring_managers") or [])
                 if str(m.get("email") or "").strip().lower() == str(address).lower()),
                None)
    # With auth off there is no "who", so there is nobody this could be scoped
    # to. Refused rather than guessed at.
    if not AUTH_ENABLED or mine is None:
        return jsonify({
            "error": INTERVIEW_IS_THE_MANAGERS,
            "auth": "forbidden",
        }), 403

    # The same top-N the board is showing them and the same rule the shortlist
    # email uses -- one definition of "the top 20", in store.top_candidates().
    limit = body.get("limit")
    size = SHORTLIST_SIZE if not isinstance(limit, int) else limit
    size = max(1, min(size, SHORTLIST_MAX))
    ids = [sub["_id"] for sub in store.top_candidates(job_id, size)]
    if not ids:
        return jsonify({
            "error": "Nobody on this role is waiting for a decision yet — "
                     "candidates appear here once they have been scored.",
        }), 409

    # One live self-served credential per manager per role, re-pointed at
    # today's list rather than joined by a second token every click.
    existing = store.live_dashboard_link(job_id, address)
    if existing:
        token = existing["_id"]
        store.refresh_dashboard_link(token, ids)
    else:
        token = store.create_review_link(job_id, mine, ids, source="dashboard")

    log.info("Review workspace opened by %s on role %s (%d candidates)",
             address, job_id, len(ids))
    return jsonify({
        "url": shortlist.review_link(token),
        "token": token,
        "count": len(ids),
        "role": role.get("title") or "",
    })


@app.route("/api/shortlist/links/revoke", methods=["POST"])
def api_revoke_review_link():
    """
    Kill one review link. {token}.

    The answer to a manager leaving, or to a link that was forwarded. Not a
    delete: the row stays so its audit trail -- when it was opened, what it
    moved -- outlives the access it granted.
    """
    error = _mongo_guard()
    if error:
        return error

    token = (request.get_json(silent=True) or {}).get("token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "token must be a string."}), 400

    # Revocation is reached by token rather than by role, so the role has to be
    # looked up from the link before anything is killed. Without this, a token
    # seen in a forwarded email would be revocable by anyone with an account.
    link = store.get_review_link(token)
    unknown = jsonify({"error": "No such review link."}), 404
    if link is None:
        return unknown
    allowed = _scope()
    if allowed is not None and link.get("job_id") not in allowed:
        return unknown

    if not store.revoke_review_link(token):
        return unknown
    return jsonify({"message": "Review link revoked."})


@app.route("/api/managers/cal-link", methods=["POST"])
def api_set_cal_link():
    """
    Set a manager's booking link. {email, cal_link}.

    Account-wide rather than per role -- see set_manager_cal_link(). It is the
    link the candidate is sent when this manager marks someone for interview,
    so a manager owning three seats types it once.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    raw = str(body.get("cal_link") or "").strip()
    if "@" not in email:
        return jsonify({"error": "email must be an address."}), 400

    # This writes to every role the address owns, which is wider than any one
    # role -- so a manager may set only their own. The link is what a candidate
    # is sent to book with; pointing somebody else's at your own calendar would
    # quietly take over their interviews.
    user = _current_user()
    if not _is_admin() and (user is None or email != user["_id"]):
        return jsonify({
            "error": "You can only set your own booking link.",
            "auth": "forbidden",
        }), 403

    cleaned = store.clean_cal_link(raw)
    # A link that was typed but did not survive normalisation is refused rather
    # than saved as blank: silently clearing it would show the manager an empty
    # box and no reason, and they would type the same thing again.
    if raw and not cleaned:
        return jsonify({
            "error": f"That does not look like a booking link: {raw[:80]}"
        }), 400

    touched = store.set_manager_cal_link(email, cleaned)
    if not touched:
        return jsonify({"error": f"{email} is not a manager on any role."}), 404
    return jsonify({
        "message": (f"Booking link saved for {email} on {touched} role(s)."
                    if cleaned else f"Booking link cleared for {email}."),
        "cal_link": cleaned,
        "known_managers": store.known_managers() if _is_admin() else [],
    })


# --------------------------------------------------------------------------
# The bulk turn-down, and the ledger  (was views_rejections.py)
# --------------------------------------------------------------------------
# Rejections -- the ledger, the paste box, and the bulk send.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Rejections -- the ledger, the paste box, and the bulk send
# ---------------------------------------------------------------------------
#
# RECRUITING TEAM ONLY, every route. One click here mails several hundred real
# people, which puts it in the same bracket as /api/run and above everything a
# hiring manager is trusted with.
#
# WHAT THE DASHBOARD ACTUALLY USES IS TWO OF THESE ROUTES:
#
#   /import   "I have already emailed these people." The right-pointing move on
#             the rejection list. Written down so nothing offers them again.
#             NOTHING IS EMAILED, and the response says so plainly, because a
#             button that silently mails 400 people when you expected it to
#             file them is unrecoverable.
#
#   /remove   the same move leftwards. It un-sends nothing; it makes this
#             system stop believing these people were told.
#
# The rest -- /parse, /preview, /send -- are the bulk send: write the message,
# read the preview, mail everyone one personalised copy in the background. It
# is complete and tested and NOTHING IN THE UI CALLS IT, deliberately: the
# recruiter sends rejections from their own mail client for now, and the
# dashboard's job is only to remember who. Left in place because the day that
# changes, this is the part that would have to be rebuilt from nothing; delete
# it with rejections.py and tests/test_rejections.py if that day is not coming.
#
# Everything here meets at the ledger in db/store.py, which every rejection
# path reads before sending and writes after -- including the board's own in
# candidate_mail. That is the whole point: one answer to "has this person
# already been told", wherever the telling happened.

_reject_jobs: dict[str, dict] = {}
_reject_jobs_lock = threading.Lock()

# Its own lock, NOT _run_lock. A five-hundred-message batch takes minutes, and
# _run_lock is also what grading and tier resolution queue behind -- sharing it
# would freeze the evaluations page for the length of a mail merge that has
# nothing to do with it. What this lock does guarantee is the thing that
# matters: two clicks cannot start two batches over the same list, because the
# second is refused before it can begin.
_reject_lock = threading.Lock()


def _reject_snapshot(job: dict) -> dict:
    """What the page is told. Copied under the lock; never the live dict."""
    return {
        "job": job["id"],
        "state": job["state"],
        "done": job["done"],
        "total": job["total"],
        "message": job["message"],
        "error": job["error"],
        "totals": job["totals"],
        "started_at": job["started_at"].isoformat(),
        "finished_at": (job["finished_at"].isoformat()
                        if job["finished_at"] else None),
    }


def _sweep_reject_jobs() -> None:
    """Drop finished batches nobody is still watching. Same rule as the runs."""
    cutoff = datetime.now(timezone.utc) - RUN_JOB_RETENTION
    with _reject_jobs_lock:
        for job_id in [j for j, job in _reject_jobs.items()
                       if job["finished_at"] and job["finished_at"] < cutoff]:
            del _reject_jobs[job_id]
        if len(_reject_jobs) > RUN_JOB_MAX:
            finished = sorted((job["finished_at"], job_id)
                              for job_id, job in _reject_jobs.items()
                              if job["finished_at"])
            for _stamp, job_id in finished[:len(_reject_jobs) - RUN_JOB_MAX]:
                del _reject_jobs[job_id]


def _run_reject_job(job_id: str, entries: list, subject: str, message: str,
                    job_id_role, job_title: str, by: str, note: str,
                    resend: bool) -> None:
    """
    The batch itself, on its own thread. OWNS _reject_lock AND RELEASES IT.

    The caller takes the lock before starting this thread, for the reason
    _run_send_job gives: a second click should be refused by the request that
    made it rather than race into a job that then finds it cannot run.
    """
    def progress(done: int, total: int, totals: dict) -> None:
        with _reject_jobs_lock:
            job = _reject_jobs[job_id]
            job.update(done=done, total=total, totals=totals,
                       message=f"Sent {totals['sent']} of {total}…")

    try:
        totals = rejections.send_bulk(
            entries, subject=subject, message=message, job_id=job_id_role,
            job_title=job_title, by=by, note=note, resend=resend,
            progress=progress,
        )
        parts = [f"Sent {totals['sent']} rejection(s)."]
        if totals["failed"]:
            parts.append(f"{totals['failed']} failed.")
        if totals["already"]:
            parts.append(f"{totals['already']} had already been told.")
        if totals["unsubscribed"]:
            parts.append(f"{totals['unsubscribed']} have opted out.")
        if totals.get("aborted"):
            parts.append(totals["aborted"])
        with _reject_jobs_lock:
            job = _reject_jobs[job_id]
            job.update(state="done", totals=totals, message=" ".join(parts),
                       done=job["total"],
                       finished_at=datetime.now(timezone.utc))
        log.info("Rejection batch %s finished: %s", job_id, " ".join(parts))
    except Exception as exc:
        # Recorded on the job as well as logged. A background failure with
        # nowhere to surface is a send that stops and reports nothing --
        # and here, one that may already have mailed two hundred people.
        log.exception("Rejection batch %s failed", job_id)
        with _reject_jobs_lock:
            job = _reject_jobs[job_id]
            job.update(state="failed", error=f"{type(exc).__name__}: {exc}",
                       message="The send stopped early. Anyone already mailed "
                               "is in the ledger and will not be mailed again.",
                       finished_at=datetime.now(timezone.utc))
    finally:
        _reject_lock.release()


def _who() -> str:
    """The signed-in address, for the `by` column on a ledger row."""
    return ((_current_user() or {}).get("email") or "").strip().lower()


def _recipients(body: dict) -> tuple[list, list, object]:
    """
    (entries, unreadable, error) from a request body.

    Accepts either `text` -- the pasted blob, which is what the box on the page
    sends -- or `recipients`, a list of {email, name} for the tick-a-row path.
    Both land as the same parsed, de-duplicated list, so the two ways in cannot
    behave differently.
    """
    entries: list[dict] = []
    unreadable: list[str] = []

    raw = body.get("recipients")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                found, bad = rejections.parse_recipients(item)
                entries += found
                unreadable += bad
            elif isinstance(item, dict):
                address = store.clean_email(item.get("email"))
                if unsubscribe_mod.looks_like_email(address):
                    entries.append({"email": address,
                                    "name": str(item.get("name") or "").strip()})
                else:
                    unreadable.append(str(item.get("email") or "")[:120])

    text = body.get("text")
    if isinstance(text, str) and text.strip():
        found, bad = rejections.parse_recipients(text)
        entries += found
        unreadable += bad

    # De-duplicated ACROSS both inputs, not within each: a page that pastes a
    # list and also ticks two rows from it must not mail those two twice.
    merged: dict[str, dict] = {}
    for entry in entries:
        current = merged.get(entry["email"])
        if current is None:
            merged[entry["email"]] = entry
        elif entry.get("name") and not current.get("name"):
            current["name"] = entry["name"]

    if not merged:
        return [], unreadable, (jsonify({
            "error": "No email addresses in that. Paste them one per line, or "
                     "straight out of a BCC field.",
            "unreadable": unreadable[:20],
        }), 400)
    return list(merged.values()), unreadable, None


def _role_title(job_id) -> tuple[object, str, object]:
    """(job_id, title, error) for an optional role on a rejection batch."""
    if job_id is None:
        return None, "", None
    if not isinstance(job_id, int):
        return None, "", (jsonify({"error": "job_id must be a number."}), 400)
    error = _role_guard(job_id)
    if error:
        return None, "", error
    return job_id, (store.get_role(job_id) or {}).get("title") or "", None


@app.route("/api/rejections")
def api_rejections():
    """
    The ledger: everybody who has been told no, however they were told.

    `search`, `status` and `job_id` narrow it; `limit` caps what is drawn.
    The count is answered separately from the rows, so a capped list can say
    how much it is not showing rather than quietly ending.
    """
    error = _require_admin()
    if error:
        return error
    error = _mongo_guard()
    if error:
        return error

    job_id = request.args.get("job_id", type=int)
    status = request.args.get("status") or None
    search = request.args.get("search") or ""
    limit = request.args.get("limit", type=int) or 500

    rows = [_json_safe(r) for r in store.list_rejections(
        job_id=job_id, status=status, search=search, limit=limit)]
    # _json_safe renames _id to id, and for this collection the _id IS the
    # address -- said out loud under its own name so the page is not reading
    # an email out of a field called "id".
    for row in rows:
        row["email"] = row.get("id")

    return jsonify({
        "rejections": rows,
        "shown": len(rows),
        "matching": store.count_rejections(job_id=job_id, status=status,
                                           search=search),
        "stats": store.rejection_stats(),
        "defaults": {"subject": rejections.DEFAULT_SUBJECT,
                     "message": rejections.DEFAULT_MESSAGE,
                     "placeholders": list(rejections.PLACEHOLDERS)},
        "mail": {"enabled": PIPELINE_EMAILS_ENABLED,
                 "max_per_send": REJECTION_MAX_PER_SEND},
    })


@app.route("/api/rejections/parse", methods=["POST"])
def api_parse_rejections():
    """
    Read a pasted list and say what a send would do with it -- WITHOUT sending.

    This is the number the recruiter needs before pressing anything: of the
    four hundred addresses just pasted, how many are new, how many have already
    been told, how many have opted out, and which lines did not parse at all.

    Sends nothing and records nothing, so it can be called on every keystroke
    if the page wants.
    """
    error = _require_admin()
    if error:
        return error
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    entries, unreadable, failure = _recipients(body)
    if failure:
        return failure

    decided = rejections.plan(entries, resend=bool(body.get("resend")))
    return jsonify({
        "total": len(entries),
        "mailable": len(decided["mailable"]),
        "already": decided["already"],
        "unsubscribed": decided["unsubscribed"],
        "unreadable": unreadable[:20],
        "unreadable_total": len(unreadable),
        "recipients": entries,
        "skipped": decided["skipped"],
        "over_cap": len(entries) > REJECTION_MAX_PER_SEND,
        "max_per_send": REJECTION_MAX_PER_SEND,
    })


@app.route("/api/rejections/import", methods=["POST"])
def api_import_rejections():
    """
    Record people as already rejected. SENDS NOTHING.

    The other half of this page, and the half that comes first in practice: the
    recruiter has already mailed four hundred people out of a BCC field, and
    this is how the system finds out, so the next send does not do it again.

    Body: {text | recipients, job_id?, note?}. The note is free text kept on the
    row -- "rejected at CV screen, Aug round" is the sort of thing that answers
    a question six months later that nothing else here can.
    """
    error = _require_admin()
    if error:
        return error
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    entries, unreadable, failure = _recipients(body)
    if failure:
        return failure

    job_id, job_title, failure = _role_title(body.get("job_id"))
    if failure:
        return failure

    result = store.record_rejections(
        entries, job_id=job_id, job_title=job_title, status="recorded",
        source="manual", by=_who(), note=(body.get("note") or "").strip())

    message = (f"Recorded {result['total']} candidate(s) as already rejected "
               f"— {result['added']} new, {result['updated']} already on the "
               f"list. Nothing was emailed.")
    if unreadable:
        message += f" {len(unreadable)} line(s) had no address in them."
    log.info("Rejection import by %s: %s", _who() or "unknown", message)

    return jsonify({"message": message, **result,
                    "unreadable": unreadable[:20],
                    "stats": store.rejection_stats()})


@app.route("/api/rejections/remove", methods=["POST"])
def api_remove_rejections():
    """
    Take people back out of the ledger -- the undo for a paste that caught the
    wrong addresses.

    It un-sends nothing. What it does is make this system stop believing these
    people have been told, which puts them back in front of the next send. Said
    in as many words in the response, because "removed" could be read as the
    opposite.
    """
    error = _require_admin()
    if error:
        return error
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    emails = body.get("emails")
    if not isinstance(emails, list) or not emails:
        return jsonify({"error": "emails must be a non-empty list."}), 400

    removed = store.delete_rejections(emails)
    log.info("%s removed %d row(s) from the rejection ledger",
             _who() or "unknown", removed)
    return jsonify({
        "message": f"Removed {removed} from the ledger. They will be included "
                   f"in the next rejection send.",
        "removed": removed,
        "stats": store.rejection_stats(),
    })


@app.route("/api/rejections/preview", methods=["POST"])
def api_preview_rejection():
    """
    The rejection exactly as one candidate would receive it.

    Rendered against a real recipient when the body carries one, so the
    unsubscribe link in the footer is the link that recipient will get. Falls
    back to a sample name, which renders the same message with no link under it
    -- honest, for a mail that is not going anywhere.
    """
    error = _require_admin()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    job_id, job_title, failure = _role_title(body.get("job_id"))
    if failure:
        return failure

    name = str(body.get("name") or "").strip()
    to = store.clean_email(body.get("email"))
    if not name and not to:
        first = ((body.get("recipients") or [None])[0]
                 if isinstance(body.get("recipients"), list) else None)
        if isinstance(first, dict):
            name, to = str(first.get("name") or ""), store.clean_email(first.get("email"))

    email = rejections.build_email(
        name or "Sample Candidate",
        subject=body.get("subject") or "",
        message=body.get("message") or "",
        role_title=job_title,
        to_email=to if unsubscribe_mod.looks_like_email(to) else "",
    )
    return jsonify({
        "email": {key: email[key] for key in ("subject", "html", "text")},
        "to": to or "",
        "to_name": name or "Sample Candidate",
        "message": email["message"],
    })


@app.route("/api/rejections/send", methods=["POST"])
def api_send_rejections():
    """
    Mail everybody on the list their rejection. THE BUTTON THAT REACHES
    HUNDREDS OF REAL PEOPLE.

    Body: {text | recipients, subject?, message?, job_id?, note?, resend?}.

    STARTED, NOT AWAITED, for the reason spelled out above _run_jobs: a batch
    of four hundred takes minutes, every proxy in between gives up first, and
    "failed" on screen beside four hundred delivered messages is the worst
    possible pairing. Returns 202 and a job id; the page polls it.

    Everything that decides who is actually mailed -- the opt-out list, the
    ledger, the per-send cap -- is in rejections.send_bulk() rather than here,
    so the CLI or a future scheduled run cannot get a different answer.
    """
    error = _require_admin()
    if error:
        return error
    error = _mongo_guard()
    if error:
        return error
    if not PIPELINE_EMAILS_ENABLED:
        return jsonify({"error": "Candidate emails are switched off on this "
                                 "server (PIPELINE_EMAILS_ENABLED=0)."}), 409

    body = request.get_json(silent=True) or {}
    entries, unreadable, failure = _recipients(body)
    if failure:
        return failure

    job_id, job_title, failure = _role_title(body.get("job_id"))
    if failure:
        return failure

    if len(entries) > REJECTION_MAX_PER_SEND:
        return jsonify({
            "error": f"That is {len(entries)} recipients, over the "
                     f"{REJECTION_MAX_PER_SEND} cap for one send. Split it, or "
                     f"raise REJECTION_MAX_PER_SEND if you meant it."
        }), 409

    # Refused BEFORE the lock is taken: a batch with nobody new in it is not a
    # queue collision, and telling the recruiter "a send is already running"
    # would be a lie about why nothing happened.
    decided = rejections.plan(entries, resend=bool(body.get("resend")))
    if not decided["mailable"]:
        return jsonify({
            "error": "Nobody on that list is new. "
                     f"{decided['already']} have already been told and "
                     f"{decided['unsubscribed']} have opted out.",
            "already": decided["already"],
            "unsubscribed": decided["unsubscribed"],
        }), 409

    if not _reject_lock.acquire(blocking=False):
        return jsonify({"error": "A rejection send is already in progress."}), 409

    handed_over = False
    try:
        _sweep_reject_jobs()
        batch = secrets.token_urlsafe(9)
        with _reject_jobs_lock:
            _reject_jobs[batch] = {
                "id": batch,
                "state": "running",
                "done": 0,
                "total": len(decided["mailable"]),
                "message": f"Sending to {len(decided['mailable'])} candidate(s)…",
                "error": None,
                "totals": None,
                "started_at": datetime.now(timezone.utc),
                "finished_at": None,
            }
        worker = threading.Thread(
            target=_run_reject_job,
            args=(batch, entries, body.get("subject") or "",
                  body.get("message") or "", job_id, job_title, _who(),
                  (body.get("note") or "").strip(), bool(body.get("resend"))),
            name=f"reject-{batch}",
            daemon=True,
        )
        worker.start()
        # The lock is the worker's now, and _run_reject_job releases it.
        handed_over = True
        log.info("Rejection batch %s started by %s (%d mailable of %d)",
                 batch, _who() or "unknown", len(decided["mailable"]),
                 len(entries))
        return jsonify({
            "job": batch,
            "state": "running",
            "queued": len(decided["mailable"]),
            "already": decided["already"],
            "unsubscribed": decided["unsubscribed"],
            "unreadable": unreadable[:20],
            "message": f"Sending to {len(decided['mailable'])} candidate(s). "
                       f"This page will keep you posted.",
            "poll": f"/api/rejections/send/{batch}",
        }), 202
    finally:
        if not handed_over:
            _reject_lock.release()


@app.route("/api/rejections/send/<job_id>")
def api_rejection_status(job_id: str):
    """How a rejection batch is going. Polled by the page that started it."""
    error = _require_admin()
    if error:
        return error
    with _reject_jobs_lock:
        job = _reject_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "No such send. It may have been swept."}), 404
        return jsonify(_reject_snapshot(job))
