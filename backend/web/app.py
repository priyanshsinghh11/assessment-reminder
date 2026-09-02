#!/usr/bin/env python3
"""
The Flask app itself, and everything more than one group of routes needs.

WHY THIS IS SEPARATE FROM server.py. server.py was 4,673 lines and 51 routes in
one module. The routes now live in nine backend/web/views_*.py files, and every
one of them needs the same few things: the `app` to hang a decorator on, the
guards that decide who may see a role, and the helpers that turn a Mongo
document into JSON. Those are here, and the views import only from here -- a
star, not a graph, so there is no order to get right and no cycle to create.

WHAT IS HERE AND NOT IN A VIEW:

  * `app`, its config, and the four request hooks. The hooks are registered in
    this file in their original order, which is the order Flask runs them in,
    and that is why they were not distributed: two of them decide whether a
    request is answered at all.
  * The review-only mode, and the two endpoints that must answer inside it --
    the unsubscribe page and /healthz.
  * Sessions, sign-in state, and the access rule: _current_user, _require_admin,
    _scope, _role_guard, _submission_guard.
  * The shared shape helpers: _json_safe, _mongo_guard, _project, _tier_arg.

WHAT IS DELIBERATELY NOT HERE. `_last_state` reads like a core concern and is
not: api_state(), api_run() and _remember() all rebind it with `global`, and a
`global` statement binds in the module the function is written in. Split across
files it would have become three separate caches, each certain it was the only
one, and nothing would have raised -- the dashboard would simply have started
showing an empty or stale window at random. It lives in views_dashboard.py with
the three functions that write it.
"""

import hmac
import logging
import threading
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, redirect, request
from markupsafe import escape

from backend.config import (AUTH_ENABLED, MANAGER_DASHBOARD_SCORES,
    CSRF_COOKIE, SESSION_COOKIE, SESSION_COOKIE_SECURE, SESSION_TTL_HOURS,
    MAX_REQUEST_BYTES, TRUSTED_PROXY_HOPS, UNSUBSCRIBE_MAILTO,
    PROJECT_ROOT)

from backend import auth
from backend.db import store
from backend.grading import rubric_pack
from backend.mail import unsubscribe as unsubscribe_mod

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
# Shared by more than one view module
#
# These sat among the evaluations, pipeline and dashboard routes when all
# 51 lived in one file. They are here now because four, eight and two other
# modules respectively call them, and a view importing from another view is
# how the import graph stops being a star.
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


# Finished jobs are kept so a page that polls late still learns the outcome,
# and swept so a long-lived process does not accumulate them for ever.
RUN_JOB_RETENTION = timedelta(hours=6)
RUN_JOB_MAX = 50
