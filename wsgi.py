#!/usr/bin/env python3
"""
WSGI entry point. This is what a real server imports; `python server.py` is not.

    gunicorn -c gunicorn.conf.py wsgi:app

WHY THIS FILE EXISTS AT ALL. `server.py` ends in `app.run()`, which is
Werkzeug's development server -- single-threaded by default, no request queue,
no timeouts, and it says so in the log every time it starts. But the startup
work that makes the app usable does not live in the `app` object; it lives in
`main()`, alongside the argparse. A WSGI server imports `app` and never calls
`main()`, so importing `backend.web.server:app` directly gets you a dashboard
with no logging configured, no account indexes, no seeded admin, and
REVIEW_ONLY unset. This module is `main()` minus the argparse and minus
`app.run()`, so the two ways of starting the process agree on everything except
who is listening on the socket.

CONFIGURATION IS ENVIRONMENT, NOT FLAGS. There is nowhere to type `--review-only`
at a container, so the mode is read from the environment instead:

    REVIEW_ONLY=1   serve ONLY the manager review surface -- /review/<token>,
                    its API, and the handful of files those pages are built
                    from. Everything else 404s. THIS is the mode that may face
                    the internet.

    REVIEW_ONLY=0   the full dashboard: every role, every candidate's address,
                    and every send button, behind a sign-in. (default)

Those are the same two modes `server.py --review-only` selects, and they are
still two processes. Running one container in each is the deployment shape;
one container serving both puts the send buttons on whatever hostname the
review links are mailed from.

THE BIND ADDRESS IS NOT DEFENDED HERE. `server.py` defaults to 127.0.0.1
because a flag is a thing a person types. A container has no loopback worth
binding to -- nothing outside it could reach the app -- so gunicorn binds
0.0.0.0 and the boundary moves outward, to the platform: put TLS in front of
this, and set SESSION_COOKIE_SECURE=1 when you do, or the session cookie is
sent in the clear to anyone on the path.
"""

import logging
import os

from backend.core.config import AUTH_ENABLED
from backend.core.logging_setup import setup_logging

from backend.accounts import auth
from backend.database import mongo_store as store
from backend.web.server import app

log = logging.getLogger("wsgi")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


REVIEW_ONLY = _env_flag("REVIEW_ONLY")

setup_logging()
app.config["REVIEW_ONLY"] = REVIEW_ONLY

# Mirrors main(): accounts live in Mongo, and review-only mode has none -- its
# credential is the token in the URL. Failure is logged rather than fatal, and
# that is deliberate here too. A container that exits because the database was
# slow to accept connections is a crash-loop; one that stays up answers 503
# with the reason from _guard_auth until Mongo is back, which is the thing
# somebody reading the logs can act on.
if not REVIEW_ONLY and AUTH_ENABLED:
    try:
        auth.ensure_indexes()
        auth.seed_admins()
        if not auth.admin_count():
            log.warning(
                "NO ADMIN ACCOUNT EXISTS -- nobody can sign in. Create one "
                "with:  python manage_users.py add --admin <email>  (inside "
                "the container, or against the same MONGO_URI from anywhere)")
    except store.MongoUnavailable as exc:
        log.error("Cannot reach MongoDB, so nobody can sign in yet: %s", exc)
    except Exception:
        log.exception("Could not prepare the accounts collection")
elif not REVIEW_ONLY:
    # Said on every start, exactly as main() says it. A container makes this
    # worse, not better: the thing that made AUTH_ENABLED=0 survivable was a
    # server bound to loopback on somebody's laptop, and neither half of that
    # is true here.
    log.warning(
        "AUTH_ENABLED=0: THE DASHBOARD HAS NO LOGIN. Every visitor sees every "
        "role, every candidate's address, and every send button. This is a "
        "local-development setting and it is running in a container bound to "
        "0.0.0.0 -- unset it.")

if REVIEW_ONLY:
    log.info("Review-only mode: the dashboard surface is 404 in this process.")
