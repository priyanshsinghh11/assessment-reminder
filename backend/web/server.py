#!/usr/bin/env python3
"""
Dashboard backend.

Serves the static frontend and three endpoints on top of the same functions
reminder.py uses, so the dashboard and the CLI can never drift apart.

    GET  /api/state           who is in the window and who has started
    GET  /api/logs?limit=200  tail of logs/reminder.log
    POST /api/run             {mode, limit, emails}

NOTHING HERE SCRAPES ON ITS OWN. A scan hits the portal and Workable and takes
~15 seconds, and it used to start the moment anyone opened the page. Now the
only thing that scans is an explicit "Sync portal" click (GET /api/state with
?refresh=1); every other request is served from the last scan, however old it
is, and the page says how old. The evaluations page works the same way -- its
"Sync portal" button is the only thing that re-crawls into Mongo.

The last scan is written to state/last_scan.json so restarting the server does
not leave the dashboard blank with no way to fill it but another scrape.

WHO SEES WHAT. Everything outside the token-authenticated review surface needs
an account, and an account is one of two things: an admin, who is the
recruiting team and sees every role, or a manager, who sees ONLY the roles
whose hiring-manager list carries their address. That second rule is not a
filter over a full answer -- it is applied in the query, and a role a manager
does not own answers 404 the same way a job id that never existed does. See
the "Accounts, sessions, and who may see which role" section below, and auth.py.

Run with:
    python server.py            # http://127.0.0.1:5000
"""

import argparse
import hmac
import json
import logging
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from flask import (Flask, Response, g, jsonify, redirect, request,
                   send_from_directory)
from markupsafe import escape

from backend.core.config import (
    ASSESSMENT_JOBS,
    AUTH_ENABLED,
    DAYS_BETWEEN_REMINDERS,
    LLM_CONCURRENCY,
    LOG_FILE,
    MANAGER_DASHBOARD_SCORES,
    MAX_REMINDERS_PER_CANDIDATE,
    REMINDER_AFTER_BUSINESS_DAYS,
    REMINDER_UNTIL_BUSINESS_DAYS,
    PIPELINE_EMAILS_ENABLED,
    PUBLIC_BASE_URL,
    REJECTION_MAX_PER_SEND,
    REVIEW_LINK_DAYS,
    SCAN_CACHE_FILE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_HOURS,
    SHORTLIST_MAX,
    SHORTLIST_SHOW_SCORES,
    SHORTLIST_SIZE,
    MAX_REQUEST_BYTES,
    STATE_DIR,
    TRUSTED_PROXY_HOPS,
    UNSUBSCRIBE_MAILTO,
    PROJECT_ROOT,
)
from backend.notifications.reminder import gather_state, send_batch, setup_logging, PortalUnavailable

from backend.accounts import auth
from backend.notifications import candidate_mail
from backend.grading import evaluator
from backend.grading import grader
from backend.pipeline import ingest
from backend.database import mongo_store as store
from backend.grading import rubric_pack
from backend.notifications import rejections
from backend.notifications import shortlist
from backend.notifications import unsubscribe as unsubscribe_mod
from backend.grading import tier_resolver

FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = Flask(__name__, static_folder=None)

# Werkzeug refuses anything larger with 413 before the body is read into
# memory. See MAX_REQUEST_BYTES in config for why this is hardening rather
# than a fix for a demonstrated hole.
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
log = logging.getLogger("server")

# Only one scan or send may run at a time. Without this, two clicks could
# double-send: both would pass the dedupe check before either recorded it.
_run_lock = threading.Lock()
_last_state: dict | None = None


# ---------------------------------------------------------------------------
# Review-only mode
# ---------------------------------------------------------------------------
#
# THE DASHBOARD IS NOT THE THING TO PUT ON A PUBLIC PORT. It now asks for a
# sign-in (see the accounts section below), but /api/run and
# /api/shortlist/send will still mail hundreds of real people, and a password
# form is not the surface to defend that with on the open internet.
#
# But hiring managers have to reach /review/<token> from wherever they are, and
# that is the same Flask app. So exposure is a mode, not a default: run a
# SECOND process with --review-only bound to the public interface, and leave
# the dashboard on loopback. In that mode every path outside the small review
# surface below 404s -- the same answer an unknown URL gets, so a scan cannot
# tell that a dashboard exists on the other side at all.
#
# A denylist of dangerous routes would be the wrong shape here: it fails open,
# and the next endpoint anyone adds is exposed until somebody remembers to add
# it. This is an allowlist of exact prefixes, so a new route is private until
# it is deliberately named.
# The container liveness probe. Named here because both guards below fail
# closed, which is the point of them -- an unlisted path is private, including
# this one. It answers before any session or token is looked at and says
# nothing but that the process is alive: no version, no config, no database
# state. A probe is reachable by definition, so it is also a probe of what an
# unauthenticated caller can learn, and the answer has to stay "nothing".
HEALTH_PATH = "/healthz"

# The candidate's way out of the mailing list. Public by design -- the signed
# token in the URL is the whole credential, exactly like a review link, because
# the person holding it has no account and never will.
#
# IT HAS TO WORK IN REVIEW-ONLY MODE. That is the process facing the internet,
# and PUBLIC_BASE_URL -- which is what the link in every candidate email is
# built from -- points at it. Left off that allowlist, every unsubscribe link
# we send would 404, which is a worse failure than not offering one.
UNSUBSCRIBE_PREFIX = "/unsubscribe/"

REVIEW_PREFIXES = ("/review/", "/api/review/")
REVIEW_FILES = ("review.html", "review.js", "review.css", "styles.css",
                # The composer, shared with the dashboard. The page is
                # unusable without it -- it renders its own markup, so
                # a 401 here is a dialog that never appears rather than
                # a dialog that appears broken.
                "invite.js",
                # The wordmark in the page header, both themes, and the
                # square mark this page's <link rel="icon"> asks for. Three
                # image files, listed by name like everything else here --
                # opening "assets/" as a directory would be the one wildcard on
                # this allowlist, and the next asset dropped in there would be
                # public without anybody deciding it should be.
                #
                # The favicon has to be here or a manager's tab shows the
                # browser's blank page icon and the log fills with 404s for it
                # on every open. It gives nothing away: it is the same mark
                # that is already at the top of the page they are reading.
                "assets/ajaia-logo.png", "assets/ajaia-logo-white.png",
                "assets/ajaia-mark.png")


def _review_only() -> bool:
    return bool(app.config.get("REVIEW_ONLY"))


@app.before_request
def _guard_review_only():
    """404 anything outside the review surface while in review-only mode."""
    if not _review_only():
        return None
    path = request.path
    if path == HEALTH_PATH:
        return None
    if path.startswith(UNSUBSCRIBE_PREFIX):
        return None
    if path.startswith(REVIEW_PREFIXES):
        return None
    # The page's own assets, by exact name. Not "any static file": the frontend
    # directory also holds the dashboard's JS, which names every endpoint it
    # calls and would hand a reader the map.
    if path.lstrip("/") in REVIEW_FILES:
        return None
    return jsonify({"error": "Not found."}), 404


@app.after_request
def _protect_review_urls(response):
    """
    Keep a review token out of everything that would otherwise keep a copy.

    The token sits in the URL path, which is the price of a link a manager can
    click from their phone with no login. That makes the URL itself the secret,
    and URLs leak in three well-known ways -- all three are shut here rather
    than only in the page's <meta> tags, which a non-HTML response does not
    have and a proxy does not read:

      Referer     a click through to a candidate's Google Drive CV would
                  otherwise send the whole review URL to Google.
      caches      a shared or corporate proxy holding the page would serve one
                  manager's list to the next person behind it.
      crawlers    a link pasted anywhere indexable becomes a public shortlist.
    """
    if request.path.startswith(REVIEW_PREFIXES):
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


_UNSUB_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>%(title)s</title>
<style>
  body {{ font-family: 'Poppins', Arial, sans-serif; color: #1b1c1c;
         background: #f7f8fa; margin: 0; padding: 48px 16px; }}
  .card {{ max-width: 520px; margin: 0 auto; background: #fff; padding: 32px;
          border: 1px solid #e4e7ec; border-radius: 8px; }}
  h1 {{ font-size: 20px; margin: 0 0 12px; }}
  p {{ line-height: 1.6; margin: 0 0 16px; }}
  button {{ background: #001d6b; color: #fff; border: 0; border-radius: 6px;
           padding: 12px 20px; font-size: 15px; cursor: pointer; }}
  .muted {{ color: #667085; font-size: 13px; }}
</style></head>
<body><div class="card">%(body)s</div></body></html>"""


def _unsub_page(title: str, body: str, status: int = 200):
    """A tiny self-contained page. No JS, no assets, no session."""
    html = _UNSUB_PAGE.replace("{{", "{").replace("}}", "}") % {
        "title": title, "body": body}
    response = Response(html, status=status, mimetype="text/html")
    # Same treatment review links get: the URL is the credential, so keep it
    # out of referrers, shared caches and search indexes.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@app.route(UNSUBSCRIBE_PREFIX + "<token>", methods=["GET", "POST"])
def unsubscribe(token: str):
    """
    A candidate opting out of assessment mail.

    POST does it. GET asks first, and the split is deliberate: mail clients and
    security scanners follow links in messages to see where they go, and an
    opt-out that happened because something prefetched the URL is a decision
    the candidate never made. RFC 8058 one-click is a POST for exactly this
    reason, so the header advertises the POST and a human gets a button.

    An unrecognised token is answered as an unrecognised token, not a 404 and
    not a redirect to anything that needs a login -- the reader is a candidate
    with no account, and the only useful thing to tell them is who to write to.
    """
    email = unsubscribe_mod.email_for(token)

    if email is None:
        fallback = (f' Write to <a href="mailto:{UNSUBSCRIBE_MAILTO}">'
                    f"{UNSUBSCRIBE_MAILTO}</a> and we will take care of it."
                    if UNSUBSCRIBE_MAILTO else
                    " Please reply to the email you received and we will take "
                    "care of it.")
        return _unsub_page(
            "Link not recognised",
            "<h1>That link is not one of ours</h1><p>It may have been cut "
            "short by your email client, or copied incompletely." + fallback
            + "</p>", status=404)

    if request.method == "GET":
        return _unsub_page(
            "Unsubscribe",
            "<h1>Stop these emails?</h1>"
            f"<p>We will stop sending assessment reminders to "
            f"<strong>{escape(email)}</strong>.</p>"
            '<form method="post"><button type="submit">'
            "Yes, unsubscribe me</button></form>"
            # SAY WHAT IT ACTUALLY DOES. It stops the two bulk-shaped messages
            # -- the reminder and the turn-down -- and not the invitation,
            # which is about a meeting the reader is being offered. A page that
            # promised "reminders only" while the rejection was also suppressed
            # would be a promise this system quietly breaks, and the candidate
            # would never find out.
            "<p class=\"muted\" style=\"margin-top:20px\">This stops "
            "assessment reminders, and the note we send when an application "
            "does not go forward. If we want to interview you, we will still "
            "write. It does not withdraw your application.</p>")

    try:
        added = unsubscribe_mod.suppress(email, source="one-click")
    except store.MongoUnavailable as exc:
        log.error("Unsubscribe failed for %s: %s", email, exc)
        # 503 rather than a page claiming success. A mail client that honours
        # one-click retries a 5xx; a green tick over a write that did not
        # happen is how somebody gets mailed again after opting out.
        return _unsub_page(
            "Something went wrong",
            "<h1>We could not record that just now</h1><p>Please try again in "
            "a few minutes." + (f' Or write to <a href="mailto:'
                                f'{UNSUBSCRIBE_MAILTO}">{UNSUBSCRIBE_MAILTO}'
                                f"</a>." if UNSUBSCRIBE_MAILTO else "")
            + "</p>", status=503)

    log.info("Unsubscribe: %s (%s)", email,
             "new" if added else "already on the list")
    return _unsub_page(
        "Unsubscribed",
        "<h1>Done</h1><p>We will not send any more assessment reminders to "
        f"<strong>{escape(email)}</strong>, or write to you when an "
        "application does not go forward.</p>"
        "<p class=\"muted\">This does not withdraw your application. If you "
        "were in the middle of an assessment, you can still complete it, and "
        "if we want to interview you we will still write.</p>")


@app.route(HEALTH_PATH)
def healthz():
    """
    Liveness: this process is up and serving. Nothing more.

    DELIBERATELY DOES NOT CHECK MONGO. The app is built to survive a database
    outage rather than fall over in one -- main() logs the failure and keeps
    serving, and _guard_auth answers 503 with the reason until it is back, so
    somebody reading the page is told what is wrong. A probe that reported
    unhealthy on the same condition would have the platform kill and restart
    the container instead, turning a database that is briefly unreachable into
    a crash-loop that cannot tell anyone why.
    """
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Accounts, sessions, and who may see which role
# ---------------------------------------------------------------------------
#
# EVERY REQUEST OUTSIDE THE REVIEW SURFACE NEEDS A SESSION, and every request
# that names a role is checked against the roles that session may see. The two
# halves are separate on purpose: being signed in gets you the dashboard, it
# does not get you a role.
#
#   admin     the recruiting team -- every role, plus the machinery: portal
#             sync, reminder sends, who owns which seat, and the accounts.
#   manager   a hiring manager -- only the roles whose `hiring_managers` list
#             carries their address. See auth.visible_job_ids(); the answer is
#             derived from that list on every request, so removing somebody
#             from a role revokes their access in the same click.
#
# A ROLE THEY DO NOT OWN ANSWERS 404, NOT 403. "You may not see role 41" tells
# a manager that role 41 exists and roughly how busy it is, which is the kind
# of thing that leaks out of a company sideways. The answer is the same one a
# job id that was never real gets, and their roles list never mentioned it.
#
# The checks live in the routes rather than in a table of path patterns. A
# pattern table looks tidier and fails open: the next route anyone adds is
# unguarded until somebody remembers to add a line to it. Here the missing call
# is on the screen a reviewer is already reading.

# Reachable with no session at all. Exact paths, and the login page's own
# files -- not "any static file", because the frontend directory also holds the
# dashboard's JS, which names every endpoint it calls.
PUBLIC_PATHS = ("/api/auth/login", HEALTH_PATH)
PUBLIC_FILES = ("login.html", "login.js", "auth.css", "styles.css",
                "favicon.ico")

# What an account that must change its password can still reach. Everything
# else waits until it has: a temporary password an admin read out over a call
# is not a credential anyone should be working under.
PASSWORD_CHANGE_PATHS = ("/api/auth/me", "/api/auth/password",
                         "/api/auth/logout")


def _wants_html() -> bool:
    """True for a browser navigating to a page, false for the page's fetches."""
    return "text/html" in (request.headers.get("Accept") or "")


def _client_ip() -> str:
    """
    The caller's address, as far as it can actually be trusted.

    X-Forwarded-For IS WRITTEN BY THE CLIENT. Anyone can send one, and the
    previous version of this read the raw header whenever it was present -- so
    the address recorded against a session, and the address the sign-in
    throttle counts against, were both whatever the caller typed. A throttle
    keyed on a value the attacker chooses is not a throttle: a new forged
    address per request is a new bucket per request.

    Each proxy in the chain APPENDS the peer it saw, so the rightmost entries
    are the ones added by infrastructure and the leftmost are the client's own
    writing. With TRUSTED_PROXY_HOPS = n, the real client is n entries from the
    right; everything further left is unverifiable and discarded.

    TRUSTED_PROXY_HOPS = 0 (the default) ignores the header outright and uses
    the socket's peer address, which is the correct answer when nothing is in
    front of this process -- and the safe answer when somebody has not yet said
    what is. Setting it too high is the dangerous direction: it reaches back
    into the part of the header the client controls.
    """
    peer = request.remote_addr or ""
    if TRUSTED_PROXY_HOPS <= 0:
        return peer

    chain = [part.strip() for part in
             (request.headers.get("X-Forwarded-For") or "").split(",")
             if part.strip()]
    if not chain:
        return peer

    # n hops back from the right. A chain shorter than the configured hop count
    # means the request did not come through the expected proxies, so the
    # leftmost entry is the closest thing to a real address -- and it is still
    # only as trustworthy as whatever did forward it.
    index = len(chain) - TRUSTED_PROXY_HOPS
    return chain[index] if 0 <= index < len(chain) else chain[0]


def _safe_next(value: str) -> str:
    """
    Only a same-site path may be redirected to after signing in.

    "//evil.example" and "https://evil.example" are both absolute despite the
    first one looking like a path, and a redirect target taken from a URL and
    used unchecked is how a phishing link ends up wearing your own domain.
    """
    target = str(value or "")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _login_redirect():
    """Send a signed-out visitor to the login page, remembering where they were."""
    target = request.full_path if request.query_string else request.path
    return redirect("/login.html?next=" + quote(target, safe=""))


def _current_user() -> dict | None:
    return getattr(g, "user", None)


@app.before_request
def _guard_auth():
    """
    Attach the session's account to `g.user`, and refuse anything without one.

    Runs after _guard_review_only, which has already narrowed review-only mode
    to the token-authenticated review surface. That surface carries its own
    credential in the URL and has no accounts behind it, so it is skipped here
    rather than being given a second, contradictory one.
    """
    g.user = None
    g.csrf = ""
    if not AUTH_ENABLED or _review_only():
        return None

    path = request.path
    if path.startswith(REVIEW_PREFIXES):
        return None

    # Authenticated by its own signed token, like a review link. Placed above
    # the session lookup so that the CSRF check below never applies to it: a
    # mail client sending a one-click POST has no cookie and no CSRF token, and
    # refusing it would break the header on the clients that honour it best.
    if path.startswith(UNSUBSCRIBE_PREFIX):
        return None

    # ...and the four files that surface is built from. They sit at the ROOT,
    # not under /review/, so the prefix check above walks straight past them --
    # "/review.js" is a sibling of "/review/", not a child of it. Without this
    # the page loads and its script 401s, which is not a failure a manager can
    # read: the HTML renders, the header draws, and it sits on "Loading..."
    # for ever, because the script that would have replaced it never arrived.
    #
    # A manager has no account -- the token in their URL is the whole
    # credential -- so demanding a session for these is asking them for
    # something they were never given. REVIEW_FILES rather than "any file in
    # frontend/": that directory also holds the dashboard's own JS, which names
    # every endpoint it calls, and _guard_review_only excludes it for that
    # reason. The same list, so the two modes cannot drift apart.
    if path.lstrip("/") in REVIEW_FILES:
        return None

    # Mongo holds the sessions, so it holds the front door. Say that plainly:
    # without this, an unreachable database looks exactly like a password that
    # stopped working, and the first thing anybody would try is a reset.
    try:
        g.user = auth.session_user(request.cookies.get(SESSION_COOKIE))
    except store.MongoUnavailable as exc:
        return jsonify({"error": f"Cannot sign in: {exc}"}), 503

    public = path in PUBLIC_PATHS or path.lstrip("/") in PUBLIC_FILES

    if g.user is None:
        if public:
            return None
        if _wants_html():
            return _login_redirect()
        return jsonify({"error": "Please sign in.", "auth": "required"}), 401

    # --- signed in from here down ---

    g.csrf = (g.user.get("_session") or {}).get("csrf", "")

    # CSRF. The session cookie is SameSite=Lax, which already stops a form on
    # another site from carrying it into a POST; this is the second lock, for
    # the browser or proxy that decides otherwise. Only state-changing methods
    # are checked -- a GET that needed a token would break every link.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("X-CSRF-Token") or ""
        if not g.csrf or not hmac.compare_digest(sent, g.csrf):
            return jsonify({
                "error": "This session has expired, or the request did not "
                         "carry its token. Reload the page and try again.",
                "auth": "csrf",
            }), 403

    # A temporary password is not a working credential. Nothing but the change
    # itself answers until it has been replaced.
    if g.user.get("must_change") and path not in PASSWORD_CHANGE_PATHS:
        if public or path == "/login.html":
            return None
        if _wants_html():
            return redirect("/login.html?change=1")
        return jsonify({
            "error": "Set a new password before using the dashboard.",
            "auth": "must_change",
        }), 403

    # Somebody already signed in has no use for the sign-in form.
    if path == "/login.html" and not g.user.get("must_change"):
        return redirect(_safe_next(request.args.get("next") or "/"))
    return None


@app.after_request
def _protect_dashboard(response):
    """
    Headers on everything the signed-in dashboard serves.

    Small, and none of it is the access rule -- but the three below each close
    something the rules cannot:

      frame-ancestors  nothing may put this page inside an iframe, so a page
                       on another site cannot sit an invisible copy of the
                       dashboard under a button and collect the clicks.
      nosniff          a candidate's uploaded filename echoed into a JSON error
                       cannot be re-read as HTML by a browser guessing at the
                       type.
      no-store         the sign-in page and every auth reply carry a credential
                       or the form for one; a shared proxy holding either would
                       serve one person's session to the next.
    """
    if _review_only() or request.path.startswith(REVIEW_PREFIXES):
        return response          # the review surface has its own set, above
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path == "/login.html" or request.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store, private"
    return response


def _set_session_cookies(response, token: str, csrf: str):
    """
    Two cookies, and they are deliberately different.

    session  HttpOnly. Script cannot read it, so an injected script cannot post
             it somewhere. It is the credential.
    csrf     readable, because the page has to put it in a request header -- and
             a value that must travel in a header is not a credential on its
             own, it is proof the request came from a page on this origin.
    """
    age = SESSION_TTL_HOURS * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=age, httponly=True,
                        secure=SESSION_COOKIE_SECURE, samesite="Lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=age,
                        httponly=False, secure=SESSION_COOKIE_SECURE,
                        samesite="Lax", path="/")
    return response


def _clear_session_cookies(response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


# --- the two checks every guarded route makes ------------------------------

def _require_admin():
    """
    Error tuple unless the caller is an admin, else None.

    For the machinery: portal syncs, reminder sends, who owns a seat, and the
    accounts themselves. A hiring manager has no business pressing any of it,
    and several of those buttons mail hundreds of real candidates.
    """
    if not AUTH_ENABLED or auth.is_admin(_current_user()):
        return None
    return jsonify({
        "error": "That needs a recruiting-team account.",
        "auth": "forbidden",
    }), 403


def _is_admin() -> bool:
    """Whether the caller is on the recruiting team. True when auth is off."""
    return not AUTH_ENABLED or auth.is_admin(_current_user())


def _scope() -> set[int] | None:
    """The job ids this request may touch; None for an admin (all of them)."""
    return auth.visible_job_ids(_current_user())


# What a manager-role account may see of a submission.
#
# AN ALLOWLIST, for the reason _review_row gives at length: the score is not in
# `evaluation` alone. It is in `rubric_score`, in every row of `grid`, in
# `band`, in `recommendation`, in `triage`, in `gia`, in `cv_assessment` -- and
# in whatever the next version of the grader adds. A subtractive filter leaks
# the field nobody remembered; this one can only ever leak a field somebody
# typed here on purpose.
#
# SCORES ARE A SETTING ON THIS SURFACE, and only on this one. The objection to
# handing a manager a bare "78" -- it decides the interview before they have
# read a word of the work -- is an objection to the number arriving ALONE, in
# an inbox or a spreadsheet, with nothing behind it to argue with. On the
# dashboard it never does: the reader is signed in, they are named on the role,
# and the grid, its per-criterion anchors, the brief, the CV read and the
# partial-grading mark are all in the drawer under the number.
#
# So MANAGER_DASHBOARD_SCORES (default on) adds `evaluation` to the allowlist
# below, and nothing else changes: the shortlist email still obeys
# SHORTLIST_SHOW_SCORES, the spreadsheet still obeys the recruiting team's
# per-send tick in _scores_arg(), and the review page -- the one surface with
# no sign-in, where the token IS the credential -- still never sees a score at
# all. See _review_row.
#
# `submission_markdown` and `resume_text` ARE here on purpose. The work itself
# is exactly what a manager is being asked to read.
MANAGER_SUBMISSION_FIELDS = (
    # Both spellings: _json_safe renames _id to id on its way out, and this
    # runs on either side of it depending on the route. Missing the one the
    # payload actually carries hands the page a list of candidates with no
    # identifier on them, which fails as a blank board rather than as an error.
    "_id", "id",
    "candidate_name", "candidate_email", "job_id", "job_title",
    "assignment_name", "resume_link", "resume_source_link", "video_link",
    "admin_url", "submitted_at", "started_at", "submission_status",
    "auto_submitted", "pipeline", "submission_markdown", "resume_text",
)

# Every score there is, in one field. `rubric_score`, the grid rows, the band,
# the recommendation, the triage, the GIA overlay and the CV read all live
# inside `evaluation` -- so this is one entry rather than eight, and a grader
# that adds a ninth needs no edit here. It is added to the tuple above, not
# checked at read time, so the allowlist stays the single place that says what
# a manager's payload can contain.
MANAGER_SCORE_FIELDS = ("evaluation",)


def _manager_submission(sub: dict) -> dict:
    """
    One submission as a hiring manager may see it.

    Carries the AI verdict while MANAGER_DASHBOARD_SCORES is on, which is how
    it ships; with it off, no score anywhere -- not the number, not the band,
    not a criterion mark.
    """
    fields = MANAGER_SUBMISSION_FIELDS
    if MANAGER_DASHBOARD_SCORES:
        fields += MANAGER_SCORE_FIELDS
    out = {k: sub[k] for k in fields if k in sub}
    # Whether the AI finished the rubric, without a word about what it
    # concluded. The same single fact the review page carries, and for the same
    # reason: rank with no number beside it is unreadable if a part-filled grid
    # can sit at position 1 without saying so. See _review_row.
    ev = sub.get("evaluation") or {}
    out["grading_incomplete"] = bool(
        sub.get("grading_incomplete")
        or ev.get("score_provisional")
        or ev.get("grid_complete") is False)

    # WHETHER, not what. Two more single bits from inside the fields above,
    # for the same reason `grading_incomplete` is here: the page has real work
    # to do that needs them, and neither one carries a judgement.
    #
    # `graded` is "has this been marked at all" -- the test for whether
    # somebody is ready to be invited. Sent whatever MANAGER_DASHBOARD_SCORES
    # says, because with it off the page has no score to read it off, and
    # without it their invite list is empty and the button that is the whole
    # point of their screen sits disabled.
    #
    # `rejected` keeps people already turned down off that same list.
    out["graded"] = isinstance(ev.get("score"), (int, float))
    out["rejected"] = (sub.get("decision") or {}).get("status") == "rejected"

    # WHICH QUEUE THIS CANDIDATE IS IN -- because a hiring manager grades now.
    #
    # `decision.status` is not a hiring decision and never was: it is where the
    # assessment pipeline has got to with this person. Awaiting the AI marker,
    # marked, auto-rejected for a missing artefact, or started-but-never-
    # submitted. Grading is the act of moving somebody from the first of those
    # to the second, so a screen that cannot see the difference cannot offer
    # it -- "Grade pending" counts the pending rows to decide whether there is
    # anything to do, and with no status on the payload that count was zero on
    # every role and the button sat disabled for ever. The status column read
    # "unknown" for the same reason, and the status filter matched nothing.
    #
    # `reason` comes with it, because "Rejected" on its own is the one badge on
    # this page that is actively misleading without it: it means no CV or no
    # video, not a verdict on the work, and a manager who reads it as a verdict
    # will skip the candidate the recruiting team merely has not chased yet.
    #
    # BUILT FIELD BY FIELD, not copied. `decision` also carries `source` and
    # `at`, and will carry whatever the next change to the pipeline adds; the
    # allowlist rule at the top of this section is the whole reason a manager's
    # payload has never leaked a field nobody thought about, and it applies
    # inside a sub-document exactly as it does outside one.
    decision = sub.get("decision") or {}
    if decision:
        out["decision"] = {
            "status": decision.get("status"),
            "reason": decision.get("reason"),
        }
    return out


def _project(payload):
    """
    Narrow a submission, or a list of them, to what a manager may read.

    A no-op for the recruiting team and when auth is off. Called at the point a
    payload leaves the server rather than where it is read, so a route that
    forgets is a route that returns nothing rather than one that returns
    everything.
    """
    if _is_admin():
        return payload
    if isinstance(payload, list):
        return [_manager_submission(item) for item in payload]
    return _manager_submission(payload)


def _scoped_stage_counts(counts: dict, scope: set[int]) -> dict:
    """
    Re-total store.pipeline_counts() over one account's roles.

    The per-role numbers are already right -- they are per role. It is the
    header totals that are the company's rather than this manager's, and a
    stat tile whose number does not match the rows underneath it is read as a
    bug in the rows.
    """
    stages = {stage: 0 for stage in counts["stages"]}
    by_role = {job_id: tally for job_id, tally in counts["by_role"].items()
               if job_id in scope}
    for tally in by_role.values():
        for stage, n in tally.items():
            stages[stage] = stages.get(stage, 0) + n
    return {"stages": stages, "by_role": by_role}


def _role_guard(job_id: int):
    """
    Error tuple unless the caller may see `job_id` AND it exists, else None.

    Both halves answer with the same 404, so a role somebody does not own is
    indistinguishable from a job id that was never real.
    """
    missing = jsonify({"error": f"No role with job id {job_id}."}), 404
    allowed = _scope()
    if allowed is not None and int(job_id) not in allowed:
        return missing
    if store.get_role(job_id) is None:
        return missing
    return None


def _submission_guard(sub: dict | None, submission_id: int):
    """
    The same rule reached through a submission: you may see a candidate exactly
    when you may see the role they applied to.
    """
    missing = jsonify({"error": f"No submission {submission_id}."}), 404
    if sub is None:
        return missing
    allowed = _scope()
    if allowed is not None and sub.get("job_id") not in allowed:
        return missing
    return None


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

# Finished jobs are kept so a page that polls late still learns the outcome,
# and swept so a long-lived process does not accumulate them for ever.
RUN_JOB_RETENTION = timedelta(hours=6)
RUN_JOB_MAX = 50


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


# ---------------------------------------------------------------------------
# Evaluations API -- roles, candidates and AI scores out of MongoDB
#
# These read from Mongo rather than the portal, so they answer instantly. The
# portal is only touched by /api/evaluations/ingest.
# ---------------------------------------------------------------------------

def _json_safe(doc: dict) -> dict:
    """
    Rename Mongo's _id and turn datetimes into ISO strings for the client.

    Lists are walked too, for the pipeline's stage history: Flask would encode
    a bare datetime as an RFC 822 string, and one field arriving in a different
    shape from every other date on the page is a bug waiting to be written
    against it.
    """
    def value_of(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return _json_safe(value)
        if isinstance(value, list):
            return [value_of(v) for v in value]
        return value

    return {("id" if key == "_id" else key): value_of(value)
            for key, value in doc.items()}


def _mongo_guard():
    """Return a (payload, status) error tuple if Mongo is unreachable."""
    try:
        store.ping()
    except store.MongoUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    return None


# ---------------------------------------------------------------------------
# Tiers
#
# One portal assignment can serve two postings graded against two different
# standards -- today the AI Strategist pair and nothing else. Where that is
# true the dashboard shows a card per posting rather than one merged card,
# because a merged one would rank a new graduate against a seven-year
# consultant on a background row worth 40 points and present the result as a
# single queue.
#
# The role id stays one integer through all of it. Every route below still
# guards, fetches and writes against the portal role; `tier` is a filter laid
# over it, never a second role. That is what keeps review links, the pipeline
# board and the rejected list working unchanged -- they address submissions,
# and a submission belongs to exactly one tier however the dashboard is split.
# ---------------------------------------------------------------------------

def _tier_options(role: dict | None) -> tuple[tuple[str, ...], str | None]:
    """The tiers this role can be marked at, and which one is the fallback."""
    slug = (role or {}).get("slug")
    return rubric_pack.tiers_for_slug(slug), rubric_pack.default_tier_for_slug(slug)


def _tier_arg(role: dict | None, value: str | None = None):
    """
    Validate a requested tier against the role, or 400.

    Returns (tier, default_tier, error_response). A tier on a role with one
    grid is refused rather than ignored: a caller asking to see "the associate
    half" of a role that has no halves has a bug, and silently handing back
    everybody would hide it behind a plausible-looking list.
    """
    tiers, default_tier = _tier_options(role)
    tier = (value if value is not None
            else request.args.get("tier") or "").strip() or None
    if not tier:
        return None, default_tier, None
    if not tiers:
        return None, default_tier, (jsonify({
            "error": f"{(role or {}).get('title') or 'This role'} is marked by "
                     f"one grid, so it has no tiers to filter by."
        }), 400)
    if tier not in tiers and tier != "unresolved":
        return None, default_tier, (jsonify({
            "error": f"Unknown tier {tier!r}. This role is marked at: "
                     f"{', '.join(tiers)}."
        }), 400)
    return tier, default_tier, None


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

    candidates = _project([_json_safe(c) for c in
                           store.list_submissions(job_id=job_id, status=status,
                                                  limit=limit, tier=tier,
                                                  default_tier=default_tier)])
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
    output would throw that away silently. Edit rubric_pack.py to move that bar.

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
                     f"rubric_pack.py to change that standard."
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
    never touched either way -- see mongo_store.set_rubric_tier.

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


# ---------------------------------------------------------------------------
# Who may start an interview
# ---------------------------------------------------------------------------
#
# ONE DOOR. A candidate enters the interview stage from a hiring manager's
# review link and from nowhere else -- not from this dashboard, not from the
# board's row buttons, not from a script pointed at this route.
#
# The interview is the manager's decision and the invitation is the manager's
# message: signed with their name, pointing at their calendar, in words they
# wrote. A recruiter scheduling one on their behalf produces an email the
# manager has never read, over a calendar they may have since moved, and the
# candidate books a meeting the interviewer does not know about. Everything
# else on the board stays open to recruiting -- hire, reject, correct, remove.
#
# Enforced in the server rather than by hiding buttons. This dashboard has no
# auth of its own, and a rule that lives in the frontend lasts until the next
# person writes a script against the API.
INTERVIEW_IS_THE_MANAGERS = (
    "Only the hiring manager can move someone to interview, and only from the "
    "review link in their shortlist email. Send them the shortlist from the "
    "Shortlist tab and they invite whoever they want to meet \u2014 the "
    "invitation goes out in their name, over their calendar, in words they "
    "wrote. Marking someone hired, rejected or removing them still works here."
)

# The same rule, said to the person it does NOT block -- the manager on whose
# behalf it exists. They reach the composer through their own review
# workspace; see api_my_review_link for how that is entitled.
MANAGER_INVITES_FROM_COMPOSER = (
    "Tick whoever you want to meet and use Invite to interview. The "
    "invitation goes out in your name, over your calendar, in words you "
    "write — so it is written first and sent from there, rather than by "
    "moving somebody across the board."
)


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
# Everything here meets at the ledger in mongo_store, which every rejection
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


# ---------------------------------------------------------------------------
# Manager review surface
#
# The one part of this server a person outside the company ever touches. Every
# route here is reached with a token and nothing else -- no session, no login,
# no user id in the path -- so each one re-derives what the token may see
# rather than trusting anything the browser sent alongside it.
#
# Three rules hold throughout:
#   1. The token names the role AND the manager AND the exact candidate list.
#      Nothing is scoped by a parameter the caller controls.
#   2. Scores never enter a payload. submissions_for_review() projects
#      `evaluation` out at the database, not at the template.
#   3. An unknown token and a revoked one get different answers, because a
#      person holding a real-but-dead link needs to be told which they have.
# ---------------------------------------------------------------------------

# What a manager may set. `hired` and `rejected` are terminal, `interview` is
# the step before them. Returning someone to the shortlist is not offered: it
# is the recruiter's undo, and a manager who changes their mind should say so
# to a person rather than silently rewind a board other people are reading.
MANAGER_STAGES = ("interview", "hired", "rejected")

# ...and which of them /decision handles. Not `interview`: an invitation is a
# message the manager writes, so it goes through the composer and its own route
# below, where there is a subject, a body and a preview. A one-click interview
# button would send copy nobody read -- which is the thing this whole surface
# was built to stop.
MANAGER_DECISION_STAGES = ("hired", "rejected")

REVIEW_DEAD = {
    "unknown": ("This review link is not valid. It may have been mistyped — "
                "try opening it from the original email again.", 404),
    "revoked": ("This review link has been withdrawn. Ask the recruiter who "
                "sent it for a new one.", 410),
    "expired": ("This review link has expired. Ask the recruiter who sent it "
                "for a new one.", 410),
}


def _review_guard(token: str):
    """
    Resolve a token to its link document, or an error response.

    Returns (link, None) when the link may be used and (None, response)
    when it may not.
    """
    link = store.get_review_link(token)
    state = store.review_link_state(link)
    if state != "ok":
        message, status = REVIEW_DEAD[state]
        return None, (jsonify({"error": message, "state": state}), status)
    return link, None


def _review_row(sub: dict, rank: int) -> dict:
    """
    One candidate as the manager's page sees them.

    Built field by field from a fixed list rather than by copying the document
    and deleting what should not go -- the score is not in `evaluation` alone,
    and a submission gains fields over time. An allowlist cannot leak a field
    nobody thought about.
    """
    pipeline = sub.get("pipeline") or {}
    return {
        "rank": rank,
        "submission_id": sub["_id"],
        "name": sub.get("candidate_name") or "(no name)",
        "email": sub.get("candidate_email") or "",
        "resume_link": sub.get("resume_link") or "",
        "video_link": sub.get("video_link") or "",
        "assessment_url": sub.get("admin_url") or "",
        "submitted_at": shortlist._fmt_date(sub.get("submitted_at")),
        # Where they already are, so a manager coming back to the page sees
        # their own earlier decisions instead of a fresh set of buttons.
        "stage": pipeline.get("stage"),
        "stage_at": pipeline.get("at").isoformat() if pipeline.get("at") else None,
        "stage_by": pipeline.get("by"),
        "note": pipeline.get("note"),
        "interview_at": pipeline.get("interview_at") or None,
        # Whether the invitation actually left. Distinct from the stage: a
        # candidate can sit at `interview` with nothing in their inbox, because
        # the send failed or because mail is switched off, and a manager
        # re-reading the list needs to see which of the two they are looking at
        # before they wait another week for a booking that cannot come.
        "invited_at": _invited_at(sub),
        # The one thing about the grading that reaches this page. Not the
        # score, not the band -- only whether the AI finished the rubric. This
        # page shows rank and no number by design, so without this a candidate
        # whose grid was renormalised from two rows would sit at position 1
        # with nothing to read against it. `store.submissions_for_review` sets
        # it; the score itself never enters the payload.
        "grading_incomplete": bool(sub.get("grading_incomplete")),
    }


def _invited_at(sub: dict) -> str | None:
    """When this candidate was last successfully sent an invitation, or None."""
    previous = store.last_stage_email(sub, "interview")
    at = (previous or {}).get("at")
    return at.isoformat() if at else None


@app.route("/review/<token>")
def review_page(token: str):
    """
    The manager's page. Serves the same HTML whatever the token is.

    The token is checked by /api/review/<token>, which the page calls on load,
    so an invalid link renders a sentence explaining which kind of invalid it
    is rather than a bare 404 from the web server. Serving the shell
    unconditionally also means the token never reaches a Flask error handler,
    a log line or an access log entry as a 404 that says "this one was wrong".
    """
    return send_from_directory(FRONTEND_DIR, "review.html")


@app.route("/api/review/<token>")
def api_review(token: str):
    """Everything the manager's page draws: the role, them, and their list."""
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead

    store.touch_review_link(token)
    role = store.get_role(link["job_id"]) or {}
    rows = [_review_row(sub, i)
            for i, sub in enumerate(store.submissions_for_review(link), start=1)]

    return jsonify({
        "role": {"title": role.get("title"), "slug": role.get("slug")},
        "manager": link["manager"],
        "candidates": rows,
        "expires_at": link["expires_at"].isoformat() if link.get("expires_at") else None,
        "stages": list(MANAGER_STAGES),
        # Which stages reach the candidate, so the page can warn before the
        # click rather than explain after it. `hired` is absent from
        # MAILED_STAGES on purpose -- an offer is a conversation, not a
        # templated mail a board click fires.
        "mailed_stages": list(candidate_mail.MAILED_STAGES),
        # Whether anything on this page reaches a candidate, as two switches
        # rather than one, because they now govern two different clicks:
        #
        #   emails_enabled  the master switch. Off, and nothing leaves the
        #                   building from anywhere -- decisions are recorded
        #                   and recruiting writes to people by hand.
        #   auto_email      whether HIRED and REJECTED mail on the click. Off
        #                   is how the system ships: the decision is recorded
        #                   and a recruiter sends the message after reading it.
        #
        # The interview invitation answers to the master switch and to nothing
        # else, deliberately. It leaves from a composer the manager wrote and
        # previewed a second earlier, and that IS the human read of the message
        # that auto_email exists to force.
        "emails_enabled": candidate_mail.PIPELINE_EMAILS_ENABLED,
        "auto_email": candidate_mail.PIPELINE_AUTO_EMAIL,
        # The tokens a manager may write into the invitation. Served rather
        # than hard-coded in the page, so the list the composer offers cannot
        # drift from the list fill() actually substitutes.
        "placeholders": list(candidate_mail.PLACEHOLDERS),
        # Whether this manager can actually invite anyone. An interview mail
        # with no booking link is refused, and a manager should find that out
        # from a line on the page rather than from a red error after clicking.
        #
        # Resolved LIVE against the role rather than read off the token: the
        # link document snapshots who the manager was when it was minted, and
        # the commonest fix for "we have no booking link for you" is the
        # recruiter adding one afterwards. Reading the snapshot would leave the
        # page insisting they still cannot book until somebody re-sent the
        # whole shortlist. Same call the decision route gates on, so the notice
        # and the refusal can never disagree.
        "can_book": bool(candidate_mail.booking_link(
            role, "", link["manager"].get("name") or "",
            link["manager"].get("email") or "")[0]),
    })


@app.route("/api/review/<token>/decision", methods=["POST"])
def api_review_decision(token: str):
    """
    A manager marking one candidate hired or rejected.

    Body: {submission_id, stage, note?}.

    NOT INTERVIEW. That is an invitation with a subject line and a body the
    manager writes, and it goes through /api/review/<token>/invite, where there
    is a composer and a preview in front of it.

    NOBODY IS EMAILED FROM HERE while PIPELINE_AUTO_EMAIL is off. The manager's
    decision is recorded and comes back as `mail.queued`, and the page says "we
    will email them shortly" rather than claiming a send that has not happened.
    A recruiter then reads the message in the dashboard and clicks Send. This
    is the same pause the board itself now takes -- one system, one moment
    where a person looks at the mail before a candidate does.

    With the switch on, the board write happens first and the candidate's email
    second, and a failed email does NOT fail the request. The move is the
    durable thing; a manager who saw a red error would click again, moving the
    same person twice and possibly sending two mails. What the mail did is
    reported back in `mail` either way, so the page can say "marked for
    interview, but the invitation could not be sent" -- which is the honest
    sentence.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    stage = body.get("stage")

    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400
    if stage == "interview":
        return jsonify({
            "error": "Interview invitations are written and sent from the "
                     "invite composer, so the candidate gets a message you "
                     "have read.",
            "needs": "compose",
        }), 400
    if stage not in MANAGER_DECISION_STAGES:
        return jsonify({"error": f"Unknown stage: {stage}"}), 400
    # The token's own list is the authority, not the role. Without this a
    # manager could move any candidate whose id they guessed -- including one
    # on a role they have nothing to do with.
    if submission_id not in (link.get("submission_ids") or []):
        return jsonify({
            "error": "That candidate is not on the list you were sent."
        }), 403

    submission = store.get_submission(submission_id)
    if submission is None:
        return jsonify({"error": f"No submission {submission_id}."}), 404

    def field(name: str) -> str | None:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    manager = link["manager"]
    note = field("note")
    role = store.get_role(link["job_id"]) or {}

    # The booking-link refusal lives on the invite route now, with the only
    # stage that ever needed one.

    store.set_pipeline_stage(
        submission_id, stage,
        # The manager IS the interviewer here, which is what lets
        # candidate_mail.resolve_manager() find their calendar and sign a later
        # message with their name.
        interviewer=manager.get("name"),
        note=note,
        reason=note if stage == "rejected" else None,
        source="manager",
        by=f"{manager.get('name')} <{manager.get('email')}>",
    )
    store.record_review_action(token, submission_id, stage, note)

    # The same function the dashboard sends through, so a candidate gets the
    # same message whichever surface moved them -- including the duplicate
    # suppression, which is what stops a manager clicking twice from sending
    # two rejections. Best-effort: the move has already happened and is not
    # rolled back because mail failed.
    mail: dict = {"sent": False, "reason": "No email is sent for this stage."}
    if candidate_mail.stage_is_mailed(stage) and not candidate_mail.PIPELINE_AUTO_EMAIL:
        # Manual mode: the decision is the manager's, the email is ours to
        # send once a recruiter has read it. Reported as `queued` rather than
        # as a failure -- nothing went wrong here, and a manager should not be
        # shown a warning about a system that is working as configured.
        mail = {"sent": False, "queued": True,
                "reason": "the recruiting team will send their email."}
    elif candidate_mail.stage_is_mailed(stage):
        moved = store.get_submission(submission_id) or submission
        try:
            mail = candidate_mail.send_stage_email(
                moved, role, stage,
                interviewer=manager.get("name") or "",
                manager_email=manager.get("email") or "",
                note=note or "",
            )
        except candidate_mail.CandidateMailError as exc:
            mail = {"sent": False, "reason": str(exc)}
        except Exception as exc:
            log.exception("review-page stage mail failed")
            mail = {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}

    # A transport failure carries our provider's own words -- an API endpoint,
    # a key problem, an IP allowlist URL. Useful to a recruiter reading the
    # dashboard, wrong in front of a hiring manager: it is infrastructure
    # detail about us, shown to someone outside the company, and there is
    # nothing they could do with it anyway. `error` is only set on a transport
    # failure, so the outcomes a manager CAN act on -- already emailed, no
    # address on record, emails switched off -- still come through verbatim.
    if mail.get("error"):
        log.error("Review-page mail to candidate %s failed: %s",
                  submission_id, mail["error"])
        mail = {"sent": False,
                "reason": "we could not send their email just now. The "
                          "recruiting team has been alerted and will follow "
                          "up — your decision is saved."}

    name = submission.get("candidate_name") or f"submission {submission_id}"
    said = {"hired": "marked hired", "rejected": "marked rejected"}[stage]
    message = f"{name} {said}."
    if candidate_mail.stage_is_mailed(stage):
        if mail.get("sent"):
            message += " They have been emailed."
        elif mail.get("queued"):
            message += " We will email them shortly."
        else:
            message += f" Not emailed: {mail.get('reason', 'no reason given')}"

    return jsonify({
        "message": message,
        "submission_id": submission_id,
        "stage": stage,
        "mail": mail,
    })


# ---------------------------------------------------------------------------
# The invitation the manager writes
# ---------------------------------------------------------------------------
#
# The one door into the interview stage -- see INTERVIEW_IS_THE_MANAGERS. A
# manager ticks the people they want to meet, edits the message, reads it
# rendered, and sends. Two routes, because reading it and sending it are two
# decisions and only one of them is irreversible.


def _invite_picks(link: dict, body: dict) -> tuple[list[int], object]:
    """
    The candidates this request may invite, in the order the manager reads them.

    Returns (ids, None) or ([], error_response). The token's own list is the
    authority, exactly as it is for a single decision: without that check a
    manager could invite any candidate whose id they guessed, including one on
    a role they have nothing to do with.
    """
    raw = body.get("submission_ids")
    if not isinstance(raw, list) or not raw:
        return [], (jsonify({"error": "Pick at least one candidate first."}), 400)
    if not all(isinstance(i, int) for i in raw):
        return [], (jsonify({"error": "submission_ids must be numbers."}), 400)

    allowed = link.get("submission_ids") or []
    if any(i not in allowed for i in raw):
        return [], (jsonify({
            "error": "Some of those candidates are not on the list you were sent."
        }), 403)

    # De-duplicated, and put back into the ranked order the manager is looking
    # at rather than the order the checkboxes happened to be ticked in.
    picked = set(raw)
    return [i for i in allowed if i in picked], None


def _invite_context(link: dict, body: dict) -> tuple[dict | None, object]:
    """
    Everything both invite routes need, resolved once: the role, the manager,
    their booking link, the submissions picked, and the words they typed.

    Returns (context, None) or (None, error_response). Shared so the preview
    and the send cannot disagree about who is being written to, with what, or
    over whose calendar.
    """
    ids, dead = _invite_picks(link, body)
    if dead:
        return None, dead

    role = store.get_role(link["job_id"]) or {}
    manager = link["manager"]

    # Resolved live against the role rather than read off the token: the link
    # snapshots who the manager was when it was minted, and the commonest fix
    # for "we have no booking link for you" is the recruiter adding one
    # afterwards.
    booking, _resolved = candidate_mail.booking_link(
        role, "", manager.get("name") or "", manager.get("email") or "")

    def text(name: str) -> str:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    when = text("interview_at")
    # One pencilled-in time cannot be right for twelve people. Refused for a
    # batch rather than quietly written onto everybody, which is how twelve
    # candidates all get told to come at two o'clock on Thursday.
    if when and len(ids) > 1:
        return None, (jsonify({
            "error": "A suggested time only fits one candidate at a time. "
                     "Clear it, or invite them one by one.",
        }), 400)

    found = {sub["_id"]: sub for sub in store.submissions_for_review(link)}
    subs = [found[i] for i in ids if i in found]
    if not subs:
        return None, (jsonify({
            "error": "Those candidates are no longer on this list."
        }), 404)

    return {
        "role": role,
        "manager": manager,
        "booking": booking,
        "subs": subs,
        # Blank means "the default", resolved by candidate_mail rather than
        # here, so there is one copy of our copy.
        "subject": text("subject"),
        "message": text("message"),
        "interview_at": when,
        # Internal. Deliberately NOT passed to the mail: the manager already
        # wrote what the candidate reads, in the box above it, and quietly
        # appending a second note would put a line in the email they did not
        # see in the preview.
        "note": text("note"),
    }, None


def _no_booking_link() -> tuple:
    """The one refusal a manager can act on themselves, worded for them."""
    return jsonify({
        "error": "We do not have your booking link yet, so the candidate would "
                 "have no way to pick a time. Reply to the email that brought "
                 "you here with your cal.com link and we will add it.",
        "needs": "cal_link",
    }), 409


def _invite_preview(link: dict):
    """
    The shared half of the invite flow, credential already resolved.

    Takes a link DOCUMENT rather than a token, so one body answers both a
    manager holding a mailed token and a manager signed in to the
    dashboard. The two differ only in how they prove who they are; what
    they may do once proved is identical, and a second copy of this would
    become a second set of rules the moment either was edited.
    """

    context, dead = _invite_context(link, request.get_json(silent=True) or {})
    if dead:
        return dead
    if not context["booking"]:
        return _no_booking_link()

    title = context["role"].get("title") or ""
    defaults = {
        "subject": candidate_mail.default_interview_subject(title),
        "message": candidate_mail.default_interview_message(context["interview_at"]),
    }
    subject = context["subject"] or defaults["subject"]
    message = context["message"] or defaults["message"]

    # Who it is about to go to, and who has already had one. An invitation sent
    # twice is a candidate with two booking links wondering which is real, and
    # this is the line that lets a manager notice before it happens rather than
    # after.
    recipients = [{
        "submission_id": sub["_id"],
        "name": sub.get("candidate_name") or "(no name)",
        "email": sub.get("candidate_email") or "",
        "invited_at": _invited_at(sub),
    } for sub in context["subs"]]

    first = context["subs"][0]
    try:
        email = candidate_mail.build_stage_email(
            first, context["role"], "interview",
            interviewer=context["manager"].get("name") or "",
            manager_email=context["manager"].get("email") or "",
            message=message, subject=subject,
            interview_at=context["interview_at"],
        )
    except candidate_mail.CandidateMailError as exc:
        return jsonify({"error": str(exc), "needs": "cal_link"}), 409

    return jsonify({
        "subject": subject,
        "message": message,
        "defaults": defaults,
        "placeholders": list(candidate_mail.PLACEHOLDERS),
        "cal_link": context["booking"],
        "manager": context["manager"],
        "recipients": recipients,
        "count": len(recipients),
        "preview": {
            "submission_id": first["_id"],
            "name": first.get("candidate_name") or "",
            "to": email["to"],
            "subject": email["subject"],
            "html": email["html"],
        },
    })


@app.route("/api/review/<token>/invite/preview", methods=["POST"])
def api_review_invite_preview(token: str):
    """
    The invitation exactly as it currently reads.

    Body: {submission_ids: [...], subject?, message?, interview_at?}.

    Rendered against the FIRST candidate picked, through the same builder the
    send uses -- a preview from a second template is a preview of nothing. The
    manager's placeholders come back resolved for that one person while the box
    they are still typing in keeps "{first_name}", which is the only honest way
    to show what a batch of twelve is going to say.

    A blank subject or message means the default, and the defaults come back
    alongside, so the composer fills its boxes from this one request instead of
    keeping a second copy of our copy in the frontend.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead
    return _invite_preview(link)

def _invite_send(link: dict, token: str = ""):
    """
    The shared half of the invite flow, credential already resolved.

    Takes a link DOCUMENT rather than a token, so one body answers both a
    manager holding a mailed token and a manager signed in to the
    dashboard. The two differ only in how they prove who they are; what
    they may do once proved is identical, and a second copy of this would
    become a second set of rules the moment either was edited.
    """

    body = request.get_json(silent=True) or {}
    context, dead = _invite_context(link, body)
    if dead:
        return dead
    # Checked once for the batch, before a single move is written: it is the
    # same manager and the same calendar for all of them, and writing the
    # stages first would leave the board saying "booked" for a dozen people who
    # were never given a way to book.
    if not context["booking"]:
        return _no_booking_link()

    manager = context["manager"]
    role = context["role"]
    note = context["note"]
    resend = bool(body.get("resend"))
    signed = f"{manager.get('name')} <{manager.get('email')}>"

    results, sent = [], 0
    for sub in context["subs"]:
        submission_id = sub["_id"]
        name = sub.get("candidate_name") or f"submission {submission_id}"

        store.set_pipeline_stage(
            submission_id, "interview",
            interview_at=context["interview_at"] or None,
            # The manager IS the interviewer, which is what lets
            # candidate_mail.resolve_manager() find their calendar and sign the
            # invitation with their name.
            interviewer=manager.get("name"),
            note=note or None,
            source="manager",
            by=signed,
        )
        # Only when there IS a token. This trail answers "what did this link
        # do while it was live", which is a question about a link -- asked when
        # one is suspected of having been forwarded. A signed-in manager has no
        # link to suspect, and their move is already on the candidate's own
        # pipeline history, stamped with their account and source="manager".
        if token:
            store.record_review_action(token, submission_id, "interview",
                                       note or None)

        # Re-read, so the invitation quotes back the time that was just written
        # rather than the one it replaced.
        moved = store.get_submission(submission_id) or sub
        try:
            mail = candidate_mail.send_stage_email(
                moved, role, "interview",
                interviewer=manager.get("name") or "",
                manager_email=manager.get("email") or "",
                message=context["message"], subject=context["subject"],
                force=resend,
            )
        except candidate_mail.CandidateMailError as exc:
            mail = {"sent": False, "reason": str(exc)}
        except Exception as exc:
            log.exception("invite mail failed for %s", submission_id)
            mail = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}

        # A transport failure carries our provider's own words -- an API
        # endpoint, a key problem, an IP allowlist URL. Useful to a recruiter
        # reading the dashboard, wrong in front of a hiring manager: it is
        # infrastructure detail about us, shown to somebody outside the
        # company, and there is nothing they could do with it anyway. The
        # outcomes a manager CAN act on -- already invited, no address on
        # record, mail switched off -- still come through verbatim.
        if mail.get("error"):
            log.error("Invitation to candidate %s failed: %s",
                      submission_id, mail["error"])
            mail = {"sent": False,
                    "reason": "we could not send their invitation just now. "
                              "The recruiting team has been alerted and will "
                              "follow up \u2014 they are still marked for interview."}

        if mail.get("sent"):
            sent += 1
        results.append({
            "submission_id": submission_id,
            "name": name,
            "sent": bool(mail.get("sent")),
            "already": bool(mail.get("already")),
            "reason": mail.get("reason", ""),
            "invited_at": _invited_at(store.get_submission(submission_id) or moved),
        })

    total = len(results)
    if sent == total:
        message = (f"{results[0]['name']} has been invited." if total == 1
                   else f"All {total} invitations are on their way.")
    elif sent:
        message = (f"Invited {sent} of {total}. "
                   + "; ".join(f"{r['name']}: {r['reason']}"
                               for r in results if not r["sent"])[:400])
    else:
        message = ("Marked for interview, but nothing was emailed. "
                   + "; ".join(f"{r['name']}: {r['reason']}"
                               for r in results)[:400])

    return jsonify({
        "message": message,
        "stage": "interview",
        "sent": sent,
        "total": total,
        "results": results,
    })


@app.route("/api/review/<token>/invite", methods=["POST"])
def api_review_invite(token: str):
    """
    Move the manager's picks to interview and send each of them the invitation
    the manager just wrote.

    Body: {submission_ids: [...], subject?, message?, interview_at?, note?,
           resend?}.

    THIS IS THE ONLY WAY INTO THE INTERVIEW STAGE. Both dashboard routes refuse
    it -- see INTERVIEW_IS_THE_MANAGERS.

    It sends while PIPELINE_AUTO_EMAIL is off, and that is not a hole in the
    manual mode; it is the manual mode arriving at its point. The pause exists
    so a person reads the message before a candidate does, and here that person
    is the one who wrote it, in the composer this request came from, one click
    earlier. What the switch still governs is everything that is not that: a
    board move on the dashboard, and this page's own hired and rejected
    buttons. PIPELINE_EMAILS_ENABLED is absolute either way -- with mail off
    the candidates are still moved and the reply says plainly that nobody was
    written to, rather than reporting a send that never happened.

    Per candidate, not per batch. One missing address or one refused send must
    not cost the other eleven their invitation, so each row carries its own
    outcome and the summary counts them. The move is committed before the send
    and is never rolled back because the send failed: where somebody stands is
    the durable fact, and a manager shown a red error would click again and
    invite the same person twice.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead
    return _invite_send(link, token)

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


def main() -> None:
    """
    Two ways to run this, and they are not interchangeable.

        python server.py
            The dashboard. Every page needs an account: an admin sees every
            role and the machinery, a hiring manager sees only the roles their
            address is on. Still bound to 127.0.0.1 by default -- a login is
            the right lock on who reads what, and it is not a reason to put a
            box that mails hundreds of candidates on a public port without TLS
            and a proxy in front of it. PORT is overridable so a second
            instance can run alongside one you already have up.

        python server.py --review-only --host 0.0.0.0 --port 5051
            Just the manager review surface: /review/<token>, its API, and its
            three static files. Everything else 404s. THIS is the process that
            may face the internet -- put it behind TLS, because the token in
            the URL is the credential and plain HTTP hands it to the network.

    Run both. Managers reach the second one; PUBLIC_BASE_URL must be the
    address they reach it at, or the links in their email are dead.
    """
    parser = argparse.ArgumentParser(description="Assessment dashboard server.")
    parser.add_argument("--review-only", action="store_true",
                        help="serve only the manager review pages -- the mode "
                             "that is safe to expose")
    parser.add_argument("--host", default=None,
                        help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "5000")))
    args = parser.parse_args()

    setup_logging()
    app.config["REVIEW_ONLY"] = args.review_only

    # Accounts live in Mongo, and review-only mode has none -- its credential
    # is the token in the URL. Failure here is logged rather than fatal: a
    # database that is down should keep the server up long enough to say so on
    # a page, and _guard_auth answers 503 with the reason until it is back.
    if not args.review_only and AUTH_ENABLED:
        try:
            auth.ensure_indexes()
            auth.seed_admins()
            if not auth.admin_count():
                log.warning(
                    "NO ADMIN ACCOUNT EXISTS -- nobody can sign in. Create one "
                    "with:  python manage_users.py add --admin <email>")
        except store.MongoUnavailable as exc:
            log.error("Cannot reach MongoDB, so nobody can sign in yet: %s", exc)
        except Exception:
            log.exception("Could not prepare the accounts collection")
    elif not args.review_only:
        # An off switch on the access rules has to be impossible to leave on by
        # accident, and the only way to make that true is to say it every time.
        log.warning(
            "AUTH_ENABLED=0: THE DASHBOARD HAS NO LOGIN. Every visitor sees "
            "every role, every candidate's address, and every send button. "
            "This is a local-development setting -- unset it before this "
            "server is reachable by anybody else.")

    # The default stays loopback in BOTH modes. Exposing the review surface is
    # a thing you type, not a thing that happens because you passed a flag that
    # sounded safe.
    host = args.host or "127.0.0.1"

    if args.review_only:
        log.info("Review-only mode on %s:%s -- links are built from %s",
                 host, args.port, PUBLIC_BASE_URL)
        if shortlist.is_loopback(PUBLIC_BASE_URL):
            log.warning(
                "PUBLIC_BASE_URL is %s, which only resolves on this machine. "
                "Review links mailed to managers will not open for them. Set "
                "PUBLIC_BASE_URL in .env to the address they can reach.",
                PUBLIC_BASE_URL)
    elif host != "127.0.0.1":
        # Loud, because this is the mistake that puts the send buttons on the
        # internet, and it is one flag away from the mode that is meant to.
        # A login narrows who gets in; it does not make plain HTTP on a public
        # interface safe, and a session cookie sent in the clear is a session
        # anyone on the path can take.
        log.warning(
            "THE FULL DASHBOARD IS BOUND TO %s. Sign-in is %s. Put TLS in "
            "front of it and set SESSION_COOKIE_SECURE=1, or bind it back to "
            "loopback -- and if you only meant to let hiring managers reach "
            "their review links, run a separate process with --review-only.",
            host, "on" if AUTH_ENABLED else "OFF, so ANYONE CAN OPEN IT")

    app.run(host=host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
