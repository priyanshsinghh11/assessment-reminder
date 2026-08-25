"""
Vercel's entry point. Everything under / is routed here by vercel.json.

WHY THIS FILE IS THREE LINES OF REAL CODE. Vercel's Python runtime looks for a
module in api/ exposing a WSGI `app`, and that is all this provides -- the app
itself, and every piece of startup that has to happen before it serves, are
wsgi.py's job. Both container and serverless deployments therefore boot through
the same module, which is the point: a second startup path is a second place
for REVIEW_ONLY to be left unset.

DO NOT "SIMPLIFY" THIS TO `from backend.web.server import app`. It looks
identical and it boots faster, because it skips setup_logging() -- which is the
call that used to crash here. What it also skips is
`app.config["REVIEW_ONLY"] = ...`, and _review_only() reads that key with a
falsy default. The deployment would come up serving the FULL DASHBOARD --
every role, every candidate's address, every send button -- on a public URL,
behind nothing but the login form. It fails open, it looks completely normal,
and nothing on screen says so. See the docstring in wsgi.py.

WHAT WORKS HERE AND WHAT DOES NOT. This is a serverless function: it is frozen
the moment it returns a response, its memory is not shared with the next
invocation, and it is killed at the platform's timeout. So:

    works       /review/<token> and its API, /unsubscribe/<token>, /healthz,
                the static files those pages are built from -- all
                request-shaped, all backed by Mongo rather than by local state.

    does NOT    anything that starts a background thread and reports progress
                by polling: the reminder run (/api/run) and the bulk rejection
                send. The thread is frozen with the function, so the batch dies
                partway through having already mailed some people; the job id
                the page polls lives in a dict this instance owns and the next
                request may land on a different one; and _run_lock is a
                threading.Lock, which is per-instance, so the guard against two
                concurrent sends does not hold across instances.

                Grading and portal ingest are the other half: both are far
                longer than any function timeout.

REVIEW_ONLY IS SET IN vercel.json AND IS CURRENTLY 0 -- the whole dashboard is
served here, deliberately, so that recruiters and hiring managers can reach it
without a second deployment. That is a decision with two consequences worth
being able to find later:

  * The batch jobs in the "does NOT" list above are reachable and will fail.
    Sync portal and grading time out; the reminder run is the dangerous one --
    it returns 202 and then dies frozen, having already mailed some candidates.
    Run those from a machine with a real process, not from here.

  * Every candidate's name, address, CV and score is now behind nothing but the
    login form, on the open internet. AUTH_ENABLED must stay on, and the admin
    password is the whole boundary.

Set it back to "1" to serve only the review surface. What must NEVER happen is
the key going missing: _review_only() reads it with a falsy default, so an
absent REVIEW_ONLY deploys the full dashboard by accident and looks identical
to deploying it on purpose. tests/test_guards.py pins its presence.

The alternative, when the batch jobs need hosting too, is the Dockerfile in
this repo on Cloud Run, Render, Fly or App Service -- a real process with
threads and a writable disk, where nothing above is caveated.
"""

import sys
from pathlib import Path

# Vercel invokes this module from inside api/, and nothing guarantees the
# repository root is importable from there -- `import wsgi` and `import
# backend...` both fail without it. Prepended rather than appended so a
# same-named package in the runtime's own site-packages cannot win.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wsgi import app  # noqa: E402  (the path has to be set first)

# The name Vercel's Python runtime looks for. Re-exported explicitly rather
# than left as an incidental import, so nobody tidies it away as unused.
__all__ = ["app"]
