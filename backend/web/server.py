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

HOW THIS IS LAID OUT. This module was 4,673 lines and all 51 routes. It is now
the entry point and nothing else: it imports the app, imports the four view
modules so their @app.route decorators run, and holds main(). Everything the
views share lives in backend/web/app.py -- see that module's docstring for what
"shared" means here and for the one thing that looks shared and is not.

FOUR VIEW MODULES, ONE PER SURFACE, and the URL prefixes say which is which:

    views_dashboard     /, /<file>, /api/auth/*, /api/state, /api/logs,
                        /api/run*      -- sign-in and the reminder dashboard
    views_evaluations   /api/evaluations/*, /api/pipeline*
                        -- the evaluations page's own API
    views_shortlist     /api/shortlist/*, /api/roles/*/managers,
                        /api/managers/*, /api/rejections/*
                        -- the hand-off and everything it mails
    views_review        /review/*, /api/review/*   -- the manager surface

It was briefly nine. Nine files for 51 routes meant the answer to "where is
this endpoint" was a grep rather than a guess, so they were grouped by the
surface they serve instead. Each former module's notes survive as the section
banners inside these four.

IMPORTING A VIEW MODULE IS WHAT REGISTERS ITS ROUTES. The imports below have no
other purpose, and there is nothing to call afterwards, which is why they are
marked noqa rather than tidied away. Remove one and its endpoints stop existing
-- the app still starts, and the page that calls them gets a 404.

NOTHING IS RE-EXPORTED FROM HERE. `from backend.web import server` then reaching
for `server.send_batch` or `server._require_admin` used to work because
everything was in this file. It does not now, deliberately: a re-export would
let a test patch `server.send_batch` and change nothing, because the view module
holds its own reference, and the test would pass while testing nothing. Patch
the module that uses the name.

Run with:
    python manage.py serve            # http://127.0.0.1:5000
"""

import argparse
import os


from backend.config import AUTH_ENABLED, PUBLIC_BASE_URL
from backend.logging_setup import setup_logging

from backend import auth
from backend.db import store
from backend.mail import shortlist
from backend.web.app import app, log

# Registration by import. See the note above -- these lines ARE the routing
# table, in the order the file used to declare it.
from backend.web import views_dashboard  # noqa: F401  (registers its routes)
from backend.web import views_evaluations  # noqa: F401  (registers its routes)
from backend.web import views_shortlist  # noqa: F401  (registers its routes)
from backend.web import views_review  # noqa: F401  (registers its routes)



def main() -> None:
    """
    Two ways to run this, and they are not interchangeable.

        python manage.py serve
            The dashboard. Every page needs an account: an admin sees every
            role and the machinery, a hiring manager sees only the roles their
            address is on. Still bound to 127.0.0.1 by default -- a login is
            the right lock on who reads what, and it is not a reason to put a
            box that mails hundreds of candidates on a public port without TLS
            and a proxy in front of it. PORT is overridable so a second
            instance can run alongside one you already have up.

        python manage.py serve --review-only --host 0.0.0.0 --port 5051
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
                    "with:  python manage.py users add --admin <email>")
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
