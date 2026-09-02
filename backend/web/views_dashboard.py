"""
Sign-in, and the reminder dashboard.

Everything served at the root of the dashboard process: the login endpoints and
account administration, the static frontend, the last portal scan, the log tail,
and the reminder run itself.

WAS THREE MODULES. views_auth.py and views_dashboard.py, merged -- they are one
surface (the pages a signed-in recruiter loads and the endpoints those pages
call) and splitting them only meant deciding, per endpoint, which of two files
to open. Each former module's own notes are kept below, as the section banners.
"""

import json
import secrets
import threading

from flask import g, jsonify, request, redirect, send_from_directory
from datetime import datetime, timedelta, timezone

from backend import auth
from backend.config import (AUTH_ENABLED, SESSION_COOKIE, ASSESSMENT_JOBS,
                            DAYS_BETWEEN_REMINDERS, LOG_FILE,
                            MAX_REMINDERS_PER_CANDIDATE,
                            REMINDER_AFTER_BUSINESS_DAYS,
                            REMINDER_UNTIL_BUSINESS_DAYS, SCAN_CACHE_FILE,
                            STATE_DIR)
from backend.db import store
from backend.mail.reminder import gather_state, send_batch, PortalUnavailable

from backend.web.app import (_clear_session_cookies, _client_ip, _current_user,
                             _mongo_guard, _require_admin, _safe_next, _scope,
                             _set_session_cookies, app, FRONTEND_DIR,
                             RUN_JOB_MAX, RUN_JOB_RETENTION, _run_lock, log)


# --------------------------------------------------------------------------
# Sign-in, sessions, and the accounts panel  (was views_auth.py)
# --------------------------------------------------------------------------
# Auth API.
#
# Signing in and out, the account list, and the two password paths.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """
    {email, password} -> a session cookie.

    A wrong address and a wrong password give the same answer, deliberately: a
    sign-in form that distinguishes them is a way of asking whether a given
    person works here.
    """
    error = _mongo_guard()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    try:
        user, token, csrf = auth.login(
            body.get("email"), body.get("password"),
            ip=_client_ip(),
            agent=request.headers.get("User-Agent", ""),
        )
    except auth.RateLimited as exc:
        # 429, not 401. A wrong password and "you have been asked to stop" are
        # different answers, and Retry-After tells an honest client how long to
        # wait instead of making it guess. It still says nothing about whether
        # the account exists.
        response = jsonify({"error": str(exc)})
        response.headers["Retry-After"] = str(exc.retry_after)
        return response, 429
    except auth.AuthError as exc:
        return jsonify({"error": str(exc)}), 401

    response = jsonify({
        "user": user,
        "csrf": csrf,
        "next": _safe_next(body.get("next") or ""),
        # A manager landing on the reminders dashboard would find an empty page
        # and a Sync button they cannot press. Their dashboard is evaluations.
        "home": "/" if user["is_admin"] else "/evaluations.html",
    })
    return _set_session_cookies(response, token, csrf)


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """End this session server-side, then clear the cookies."""
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    return _clear_session_cookies(jsonify({"message": "Signed out."}))


@app.route("/api/auth/me")
def api_me():
    """
    Who is signed in, and what their account can reach.

    The page reads this to decide what to draw. IT IS NOT WHAT DECIDES WHAT
    THEY GET -- every route makes its own check. Hiding a button the server
    would refuse anyway is courtesy; it is never the lock.
    """
    if not AUTH_ENABLED:
        return jsonify({"auth_enabled": False, "user": None, "csrf": ""})
    user = _current_user()
    if user is None:
        return jsonify({"auth_enabled": True, "user": None, "csrf": ""}), 401

    payload = auth.public_user(user)
    scope = _scope()
    return jsonify({
        "auth_enabled": True,
        "user": payload,
        "csrf": g.csrf,
        # None means every role. A count, not the ids -- the page has no use
        # for a list it is about to fetch anyway.
        "role_count": None if scope is None else len(scope),
        "home": "/" if payload["is_admin"] else "/evaluations.html",
    })


@app.route("/api/auth/password", methods=["POST"])
def api_change_password():
    """
    Change your own password. {current_password, new_password}.

    Needs the old one even though there is already a session: a dashboard left
    open on an unlocked laptop should not be a way to take the account.
    """
    error = _mongo_guard()
    if error:
        return error
    if not AUTH_ENABLED:
        return jsonify({"error": "Accounts are disabled on this server."}), 400

    user = _current_user()
    body = request.get_json(silent=True) or {}
    try:
        auth.change_own_password(user["_id"],
                                 body.get("current_password") or "",
                                 body.get("new_password") or "")
    except auth.AuthError as exc:
        return jsonify({"error": str(exc)}), 400

    # set_password() ended every session, this one included. The reply hands
    # back a fresh one rather than signing the person out of the tab they are
    # standing in at the exact moment they succeed.
    token, csrf = auth.start_session(
        user["_id"],
        ip=_client_ip(),
        agent=request.headers.get("User-Agent", ""))
    response = jsonify({"message": "Password changed.",
                        "user": auth.public_user(auth.get_user(user["_id"])),
                        "csrf": csrf,
                        "home": "/" if auth.is_admin(auth.get_user(user["_id"]))
                                else "/evaluations.html"})
    return _set_session_cookies(response, token, csrf)


@app.route("/api/auth/users", methods=["GET", "POST"])
def api_users():
    """
    The accounts screen. Admin only.

    GET lists every account with the roles each one can currently open -- read
    from the roles themselves, so it is the truth rather than a copy of it.

    POST creates one: {email, name?, title?, role?, password?}. Omit the
    password and one is generated and returned ONCE. From that moment it exists
    only as a hash, and there is no screen anywhere that can show it again.
    """
    error = _mongo_guard() or _require_admin()
    if error:
        return error

    if request.method == "GET":
        return jsonify({"users": auth.list_users(),
                        "known_managers": store.known_managers()})

    body = request.get_json(silent=True) or {}
    try:
        user, password = auth.create_user(
            body.get("email"), name=body.get("name") or "",
            role=body.get("role") or "manager",
            password=body.get("password") or "",
            title=body.get("title") or "",
            must_change=True)
    except auth.AuthError as exc:
        return jsonify({"error": str(exc)}), 400

    owned = store.roles_by_manager().get(user["email"], [])
    message = f"Account created for {user['email']}."
    if not user["is_admin"] and not owned:
        # Worth saying out loud: an account with no roles is not broken, it is
        # a manager nobody has put on a seat yet -- and they will open the
        # dashboard to an empty grid and assume it is.
        message += (" They are not a hiring manager on any role yet, so they "
                    "will see nothing until you add them to one.")
    return jsonify({
        "message": message,
        "user": user,
        "roles": owned,
        "password": password if not body.get("password") else None,
        "users": auth.list_users(),
    })


@app.route("/api/auth/users/<email>", methods=["PATCH", "DELETE"])
def api_user(email: str):
    """
    Edit or remove one account. Admin only.

    PATCH takes {name?, title?, role?, active?, password?}; DELETE removes it.
    Both refuse to take away the last working admin -- an empty admin list is a
    dashboard nobody can get back into without editing .env and restarting.
    """
    error = _mongo_guard() or _require_admin()
    if error:
        return error

    address = str(email or "").strip().lower()
    target = auth.get_user(address)
    if target is None:
        return jsonify({"error": f"No account for {address}."}), 404

    me = _current_user()
    last_admin = (target.get("role") == "admin"
                  and target.get("active", True)
                  and auth.admin_count() <= 1)

    if request.method == "DELETE":
        if last_admin:
            return jsonify({"error": "That is the last admin account. Promote "
                                     "somebody else first."}), 409
        auth.delete_user(address)
        return jsonify({"message": f"Removed {address}.",
                        "users": auth.list_users()})

    body = request.get_json(silent=True) or {}
    demoting = body.get("role") == "manager" or body.get("active") is False
    if last_admin and demoting:
        return jsonify({"error": "That is the last admin account. Promote "
                                 "somebody else first."}), 409
    # An admin who demotes themselves by accident is locked out of the screen
    # they did it on, and the fix is a shell. Refuse; another admin can.
    if me is not None and address == me["_id"] and demoting:
        return jsonify({"error": "You cannot remove your own admin access. "
                                 "Ask another admin to do it."}), 409

    try:
        updated = auth.update_user(
            address, name=body.get("name"), title=body.get("title"),
            role=body.get("role"), active=body.get("active"))
        if body.get("password"):
            auth.set_password(address, body["password"], must_change=True)
    except auth.AuthError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"message": f"Updated {address}.", "user": updated,
                    "users": auth.list_users()})


@app.route("/api/auth/users/<email>/roles", methods=["POST"])
def api_user_roles(email: str):
    """
    Put an account on roles, or take it off them. `{job_ids: [...]}`. Admin only.

    The other door onto the same list. `POST /api/roles/<job_id>/managers`
    edits one role's people; this edits one person's roles -- and both write
    `hiring_managers` on the role, so there is still exactly one answer to who
    owns a seat. A separate per-account permission would be a second answer,
    and the day the two disagreed one of them would be deciding who can open
    the dashboard while the other decided who gets the shortlist.

    Which means this is not only an access change: these are the addresses a
    role's shortlist is emailed to, and the people its candidates can be moved
    to Interview by. The reply says so rather than leaving it to be discovered
    when the next send goes somewhere unexpected.
    """
    error = _mongo_guard() or _require_admin()
    if error:
        return error

    address = str(email or "").strip().lower()
    user = auth.get_user(address)
    if user is None:
        return jsonify({"error": f"No account for {address}."}), 404

    job_ids = (request.get_json(silent=True) or {}).get("job_ids")
    if not isinstance(job_ids, list):
        return jsonify({"error": "job_ids must be a list."}), 400
    if any(not isinstance(job_id, int) for job_id in job_ids):
        return jsonify({"error": "Every job id must be a number."}), 400

    try:
        result = store.set_manager_roles(
            address, job_ids,
            name=user.get("name") or "", title=user.get("title") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    titles = {role["id"]: role["title"] for role in result["roles"]}

    def named(ids):
        return ", ".join(titles.get(job_id, str(job_id)) for job_id in ids)

    parts = []
    if result["added"]:
        parts.append(f"added to {named(result['added'])}")
    if result["removed"]:
        # The removed roles are gone from `titles` by definition, so they are
        # named by id -- better than a silent count.
        parts.append(f"removed from {len(result['removed'])} role(s)")
    message = (f"{address}: {'; '.join(parts)}." if parts
               else f"No change to {address}.")
    if result["added"] and not auth.is_admin(user):
        message += " They can open those roles now, and their shortlists will "
        message += "be emailed to them."
    if result["unknown"]:
        message += f" Ignored unknown job id(s): {result['unknown']}."

    return jsonify({
        "message": message,
        "roles": result["roles"],
        "users": auth.list_users(),
        # The role cards carry an owner chip, so the grid behind this panel is
        # now stale -- the page reloads it rather than showing two truths.
        "stale_roles": True,
    })


@app.route("/api/auth/users/<email>/password", methods=["POST"])
def api_reset_password(email: str):
    """
    Reset somebody's password to a new temporary one. Admin only.

    Returns it once, marks the account must-change, and ends every session it
    has open -- a reset is either a lockout being fixed or a leak being closed,
    and both mean any cookie still out there has to stop working.
    """
    error = _mongo_guard() or _require_admin()
    if error:
        return error

    address = str(email or "").strip().lower()
    if auth.get_user(address) is None:
        return jsonify({"error": f"No account for {address}."}), 404

    body = request.get_json(silent=True) or {}
    password = body.get("password") or auth.generate_password()
    try:
        auth.set_password(address, password, must_change=True)
    except auth.AuthError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({
        "message": f"New password set for {address}. It is shown once -- send "
                   f"it to them now; they will be asked to change it.",
        "password": password,
        "users": auth.list_users(),
    })


# --------------------------------------------------------------------------
# The reminder dashboard: static files, state, logs, the run  (was views_dashboard.py)
# --------------------------------------------------------------------------
# The static frontend, /api/state, /api/logs and the reminder run.
#
# Also the background job table the run polls. `_last_state` lives
# here rather than in app.py because api_state(), api_run() and
# _remember() rebind it with `global`, and a `global` binds in the
# module the function is written in.
#
# Split out of server.py, which was 4,673 lines and 51 routes. The app and
# everything shared come from backend.web.app; this module imports from
# there and nowhere else in backend.web.
# --------------------------------------------------------------------------

# The reminder scan the whole page is drawn from. Rebound by api_state(),
# api_run() and _remember() below -- see the note in app.py for why it is
# here and not there.
_last_state: dict | None = None



# How old a scan may be before a live send refuses to work from it. This is no
# longer a cue to go and scan -- with automatic scanning off, the send stops and
# asks for a Sync portal click instead. The rule itself stands: a stale picture
# would email someone who started the assessment in the meantime.
STATE_MAX_AGE = timedelta(minutes=15)


def _state_age(state: dict) -> timedelta:
    generated = datetime.fromisoformat(state["generated_at"])
    return datetime.now(timezone.utc) - generated


def _load_cached_state() -> dict | None:
    """The scan left over from a previous run of this server, if any."""
    if not SCAN_CACHE_FILE.exists():
        return None
    try:
        state = json.loads(SCAN_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable scan cache %s: %s", SCAN_CACHE_FILE, exc)
        return None
    # Anything without a timestamp cannot be aged, and every consumer here
    # wants to know how stale the picture is.
    return state if isinstance(state, dict) and state.get("generated_at") else None


def _remember(state: dict) -> None:
    """Hold a fresh scan in memory and on disk."""
    global _last_state
    _last_state = state
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SCAN_CACHE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        # The dashboard still works off the in-memory copy; only surviving a
        # restart is lost.
        log.warning("Could not write scan cache %s: %s", SCAN_CACHE_FILE, exc)


def _state_payload(state: dict) -> dict:
    return {
        "last_run": state["generated_at"],
        "last_run_mode": "scan",
        "stale": _state_age(state) > STATE_MAX_AGE,
        "scanned": True,
        "config": state["config"],
        "portal": state["portal"],
        "jobs": state["jobs"],
        "candidates": state["candidates"],
    }


def _empty_payload() -> dict:
    """
    What the dashboard gets before anyone has synced.

    An empty table rather than an error: there is nothing wrong, the portal
    simply has not been asked yet, and saying so is the point of pausing the
    automatic scan.
    """
    return {
        "last_run": None,
        "last_run_mode": None,
        "stale": True,
        "scanned": False,
        "message": "No scan yet -- click Sync portal to pull the current picture.",
        "config": {
            "reminder_after_business_days": REMINDER_AFTER_BUSINESS_DAYS,
            "reminder_until_business_days": REMINDER_UNTIL_BUSINESS_DAYS,
            "max_reminders_per_candidate": MAX_REMINDERS_PER_CANDIDATE,
            "days_between_reminders": DAYS_BETWEEN_REMINDERS,
        },
        "portal": None,
        "jobs": [{"shortcode": code, "label": job["label"]}
                 for code, job in ASSESSMENT_JOBS.items()],
        "candidates": [],
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

# The reminders dashboard is the recruiting team's page: it lists candidates
# across every role, and its buttons mail hundreds of them. A hiring manager
# who lands on it is sent to the page that is actually theirs rather than shown
# an empty table and a Sync button that refuses -- an empty page reads as a
# broken one, and the next thing that happens is a message asking why.
ADMIN_PAGES = ("index.html", "app.js")


def _manager_home():
    return redirect("/evaluations.html")


@app.route("/")
def index():
    if AUTH_ENABLED and not auth.is_admin(_current_user()):
        return _manager_home()
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    if (AUTH_ENABLED and filename in ADMIN_PAGES
            and not auth.is_admin(_current_user())):
        return _manager_home()
    return send_from_directory(FRONTEND_DIR, filename)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    """
    The current picture.

    Reads never scrape. Without ?refresh=1 this hands back the last scan
    whatever its age -- or an empty table if there has never been one -- and
    lets the page show how old it is. Only "Sync portal" passes ?refresh=1, and
    that is the single path in this file that touches the portal.
    """
    global _last_state

    # Admin only, and not because of the scan: this payload is every candidate
    # on every role with their email address, which is the whole of what a
    # manager account exists not to see.
    error = _require_admin()
    if error:
        return error

    if not ASSESSMENT_JOBS:
        return jsonify({"error": "ASSESSMENT_JOBS is empty in config.py"}), 500

    force = request.args.get("refresh", "").lower() in ("1", "true", "yes")

    if not force:
        if _last_state is None:
            _last_state = _load_cached_state()
        if _last_state is None:
            return jsonify(_empty_payload())
        return jsonify(_state_payload(_last_state))

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409
    try:
        state = gather_state()
    except PortalUnavailable as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("gather_state failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        _run_lock.release()

    _remember(state)
    return jsonify(_state_payload(state))


@app.route("/api/logs")
def api_logs():
    # The log names candidates and roles across the whole funnel line by line.
    error = _require_admin()
    if error:
        return error

    limit = request.args.get("limit", default=200, type=int)
    if not LOG_FILE.exists():
        return jsonify({"lines": []})
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"lines": lines[-limit:]})


# ---------------------------------------------------------------------------
# Background sends
# ---------------------------------------------------------------------------
#
# A live run mails N candidates one Brevo POST at a time, each with its own
# timeout, and it used to do that inside the HTTP request that asked for it.
# Two things follow from that, and the second is the bad one:
#
#   The recruiter's browser waits with a spinner for as long as the whole batch
#   takes, which for a few hundred candidates is minutes.
#
#   ANY PROXY IN BETWEEN GIVES UP FIRST. nginx defaults to 60 seconds, App
#   Service to about 230, and Cloud Run to whatever it was configured with.
#   When it does, the recruiter is shown a failure -- while the send carries on
#   in a thread nobody is watching, because nothing about a dropped client
#   connection stops the loop. "Failed" on screen and hundreds of emails
#   delivered is the worst possible pairing, and the obvious reaction to it is
#   to press the button again.
#
# So a live run is started, not awaited. The request returns 202 with a job id
# the moment the work is accepted; the page polls for how it is going. The run
# holds _run_lock for its whole life, so a second click still gets the same 409
# it always did.
#
# Jobs are kept in memory, which is correct for what they are: a progress
# readout for the click that started them. The DURABLE record of what was sent
# is the reminder log, written per candidate as each send succeeds, and that is
# what stops a duplicate after a restart -- not this.
_run_jobs: dict[str, dict] = {}
_run_jobs_lock = threading.Lock()



def _sweep_run_jobs() -> None:
    """Drop finished jobs that are old enough that nobody is still watching."""
    cutoff = datetime.now(timezone.utc) - RUN_JOB_RETENTION
    with _run_jobs_lock:
        stale = [job_id for job_id, job in _run_jobs.items()
                 if job["finished_at"] and job["finished_at"] < cutoff]
        for job_id in stale:
            del _run_jobs[job_id]
        # A hard ceiling as well as an age one, so a burst cannot grow this
        # without bound before the age sweep gets a chance to run.
        if len(_run_jobs) > RUN_JOB_MAX:
            finished = sorted(
                ((job["finished_at"], job_id)
                 for job_id, job in _run_jobs.items() if job["finished_at"]),
            )
            for _stamp, job_id in finished[:len(_run_jobs) - RUN_JOB_MAX]:
                del _run_jobs[job_id]


def _job_snapshot(job: dict) -> dict:
    """What the page is told. Copied under the lock; never the live dict."""
    return {
        "job": job["id"],
        "state": job["state"],
        "mode": job["mode"],
        "message": job["message"],
        "error": job["error"],
        "totals": job["totals"],
        "recorded": job["recorded"],
        "started_at": job["started_at"].isoformat(),
        "finished_at": (job["finished_at"].isoformat()
                        if job["finished_at"] else None),
    }


def _run_send_job(job_id: str, candidates: list, mode: str,
                  limit, emails) -> None:
    """
    The batch itself, on its own thread. OWNS _run_lock AND RELEASES IT.

    The caller acquires the lock before starting this thread rather than
    letting the thread acquire it, so that a second click is refused by the
    request that made it instead of racing to start a job that then discovers
    it cannot run.
    """
    try:
        totals, recorded = send_batch(
            candidates,
            dry_run=(mode == "dry-run"),
            preview=(mode == "preview"),
            limit=limit,
            only_emails=emails,
        )
        message = (f"Sent {totals['reminders_sent']} reminder(s)."
                   + (f" {totals['errors']} failed." if totals["errors"] else "")
                   + (f" {totals['unsubscribed']} unsubscribed."
                      if totals.get("unsubscribed") else ""))
        with _run_jobs_lock:
            job = _run_jobs[job_id]
            job.update(state="done", totals=totals, recorded=recorded,
                       message=message,
                       finished_at=datetime.now(timezone.utc))
        log.info("Run %s finished: %s", job_id, message)
    except Exception as exc:
        # Logged AND recorded on the job. A background failure with nowhere to
        # surface is how a run silently does nothing and reports nothing.
        log.exception("Background send %s failed", job_id)
        with _run_jobs_lock:
            job = _run_jobs[job_id]
            job.update(state="failed", error=f"{type(exc).__name__}: {exc}",
                       message="The send stopped early.",
                       finished_at=datetime.now(timezone.utc))
    finally:
        _run_lock.release()


@app.route("/api/run/status/<job_id>")
def api_run_status(job_id: str):
    """How a background send is going. Polled by the page that started it."""
    error = _require_admin()
    if error:
        return error
    with _run_jobs_lock:
        job = _run_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "No such run. It may have been swept."}), 404
        return jsonify(_job_snapshot(job))


@app.route("/api/run", methods=["POST"])
def api_run():
    """
    mode:   "scan-only" | "preview" | "dry-run" | "live"
    limit:  optional cap on emails sent this run
    emails: optional list -- send only to these candidates

    "preview" prints each email to the terminal running this server and sends
    nothing. Nothing is recorded either, so the same candidates can be
    previewed repeatedly and remain selectable afterwards.

    Every mode works from the scan the dashboard already holds. None of them
    starts one: with automatic scanning paused, a send that finds no scan -- or
    a live send that finds a stale one -- stops and asks for a Sync portal
    click rather than quietly hitting the portal itself.
    """
    global _last_state

    # The single most dangerous button in the building: a live run mails
    # hundreds of candidates across every role. Recruiting team only.
    error = _require_admin()
    if error:
        return error

    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "scan-only")
    limit = body.get("limit")
    emails = body.get("emails")

    if mode not in ("scan-only", "preview", "dry-run", "live"):
        return jsonify({"error": f"Unknown mode: {mode}"}), 400
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return jsonify({"error": "limit must be a number"}), 400

    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress."}), 409

    # Set when the lock's ownership passes to a background worker, which then
    # becomes responsible for releasing it. Without this the finally: below
    # would release a lock the worker is still relying on, and a second click
    # arriving mid-send would be let straight through into a concurrent batch.
    handed_over = False

    try:
        # "scan-only" is the one mode whose entire job is the scan, so it is
        # the one mode still allowed to run one -- it is a deliberate click,
        # the same as Sync portal.
        if mode == "scan-only":
            state = gather_state()
            _remember(state)
        else:
            if _last_state is None:
                _last_state = _load_cached_state()
            if _last_state is None:
                return jsonify({
                    "error": "No scan to work from. Click Sync portal first."
                }), 409
            if mode == "live" and _state_age(_last_state) > STATE_MAX_AGE:
                return jsonify({
                    "error": "The last scan is over "
                             f"{int(STATE_MAX_AGE.total_seconds() // 60)} minutes old. "
                             "Click Sync portal before sending, so nobody who has "
                             "since started gets an email."
                }), 409
            state = _last_state

        candidates = state["candidates"]
        eligible = [c for c in candidates if not c["portal_status"]]

        if mode == "scan-only":
            return jsonify({
                "message": f"Scan complete: {len(eligible)} of "
                           f"{len(candidates)} in window have not started.",
                "totals": {"in_window": len(candidates), "eligible": len(eligible)},
            })

        # A LIVE SEND IS STARTED, NOT AWAITED. See the note above _run_jobs.
        # preview and dry-run stay synchronous: neither touches the network, so
        # both finish in the time it takes to format the text, and making the
        # page poll for them would be ceremony over nothing.
        if mode == "live":
            _sweep_run_jobs()
            job_id = secrets.token_urlsafe(9)
            with _run_jobs_lock:
                _run_jobs[job_id] = {
                    "id": job_id,
                    "state": "running",
                    "mode": mode,
                    "message": f"Sending to up to {len(eligible)} candidate(s)…",
                    "error": None,
                    "totals": None,
                    "recorded": [],
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                }
            worker = threading.Thread(
                target=_run_send_job,
                args=(job_id, candidates, mode, limit, emails),
                name=f"send-{job_id}",
                daemon=True,
            )
            worker.start()
            # The lock is now the worker's, and _run_send_job releases it. The
            # finally: below must not, so it is handed over explicitly.
            handed_over = True
            log.info("Run %s started in the background (%d eligible)",
                     job_id, len(eligible))
            return jsonify({
                "job": job_id,
                "state": "running",
                "message": "Sending. This page will keep you posted.",
                "poll": f"/api/run/status/{job_id}",
            }), 202

        totals, recorded = send_batch(
            candidates,
            dry_run=(mode == "dry-run"),
            preview=(mode == "preview"),
            limit=limit,
            only_emails=emails,
        )

        if mode == "preview":
            message = (
                f"Printed {totals['previewed']} email(s) to the terminal "
                f"running server.py. Nothing was sent."
            )
        else:
            verb = "Would send" if mode == "dry-run" else "Sent"
            message = (
                f"{verb} {totals['reminders_sent']} reminder(s)."
                + (f" {totals['errors']} failed." if totals["errors"] else "")
            )
        # `recorded` lets the page update the rows it just sent to without
        # asking for another scan. Nothing rescanned here -- scan-only returned
        # above -- so the page is never out of step with us.
        return jsonify({
            "message": message,
            "totals": totals,
            "recorded": recorded,
            "state": None,
        })

    except PortalUnavailable as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        log.exception("run failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        if not handed_over:
            _run_lock.release()
