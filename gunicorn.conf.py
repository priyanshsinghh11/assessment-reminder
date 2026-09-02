"""
Gunicorn configuration.

    gunicorn -c gunicorn.conf.py wsgi:app

Read the workers note before changing anything here. It is the one setting in
this file that is a correctness constraint rather than a tuning knob.
"""

import multiprocessing
import os

# Cloud Run, App Service and Heroku all hand the port in through $PORT and
# expect the process to honour it. 8080 is Cloud Run's default, so it is the
# right thing to fall back to.
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
#
# ONE WORKER. NOT A PERFORMANCE CHOICE -- A CORRECTNESS ONE.
#
# backend/web/app.py:64 guards every scan, send and grading run with
#
#     _run_lock = threading.Lock()
#
# and its own comment says what it is for: "Only one scan or send may run at a
# time. Without this, two clicks could double-send: both would pass the dedupe
# check before either recorded it."
#
# A threading.Lock is a lock inside ONE process. Start a second worker and
# there are two locks, each certain it holds the only one, and the exact race
# that comment describes is back -- except now it is two OS processes, so no
# lock in Python can see across it. Two "Sync portal" clicks landing on
# different workers both scrape, both pass the dedupe check, and both mail the
# same candidates.
#
# THE REMINDER LOG NO LONGER ADDS TO THIS. That dedupe state used to be an
# unlocked JSON file, so two workers writing it lost each other's records
# silently. It now lives in MongoDB behind an atomic claim
# (backend/db/reminder_log.py) and settles its own races, so a second
# worker would no longer double-send a reminder.
#
# The run lock is the reason that remains, and it is enough on its own: two
# workers still means two concurrent portal scans, two concurrent grading
# sweeps, and two processes each believing they hold the only lock.
#
# So: one worker, and concurrency comes from threads INSIDE it, where the lock
# still means something. Raise `workers` only once the run lock is something
# both processes can see -- a Mongo lock document, or a lease. Until then this
# line is load bearing.
workers = 1
worker_class = "gthread"

# Threads, not processes. Dashboard traffic is a handful of recruiters loading
# pages that are mostly served from the last cached scan, so this is generous.
# The long operations hold _run_lock and reject a second one with 409 rather
# than queueing, so extra threads never pile up behind a grading run -- they
# get an immediate "A run is already in progress."
threads = int(os.environ.get("GUNICORN_THREADS", "8"))


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
#
# The default is 30 seconds, and several endpoints here are far slower than
# that BY DESIGN, synchronously, inside the request:
#
#   GET  /api/state?refresh=1   scrapes the portal and Workable  (~15s+)
#   POST /api/run               sends a batch of reminder emails
#   POST /api/grade*            calls the LLM per candidate, serially at
#                               LLM_CONCURRENCY=1
#
# A grading sweep over a full role is minutes, not seconds. At the default
# timeout gunicorn would kill the worker mid-run -- after the emails went out
# or after the LLM was billed, but before the result was written back.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "1800"))

# Long enough for an in-flight grading run to finish on a redeploy instead of
# being cut in half.
graceful_timeout = 120

# Idle keep-alive. Slightly above a typical 60s proxy idle timeout so the
# proxy is the one that closes the connection, not us mid-response.
keepalive = 65

# NO WORKER RECYCLING. max_requests would restart the worker after N requests,
# and with one worker that restart lands on whatever long run is in flight.
# There is nothing here that leaks per-request memory badly enough to trade a
# half-finished send for.
max_requests = 0


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
#
# Access and error logs to stdout/stderr, which is where every container
# platform collects them. This is separate from the application's own log:
# setup_logging() also writes logs/reminder.log, because the dashboard's Logs
# panel reads that file back (GET /api/logs). Both are wanted -- see the note
# about the logs volume in docker-compose.yml.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")

# The platform's proxy is the only thing that knows the client's real address.
# Trust it for X-Forwarded-For so the access log is not a column of proxy IPs.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")

# Not preloaded. wsgi.py touches Mongo at import (indexes, admin seeding), and
# with a single worker preloading would only move that work into the arbiter,
# where a failure is harder to see and a restart does not repeat it.
preload_app = False


def on_starting(server):
    server.log.info(
        "Starting %s worker, %s threads, %ss timeout -- REVIEW_ONLY=%s",
        workers, threads, timeout, os.environ.get("REVIEW_ONLY", "0"))
    if workers != 1:
        server.log.error(
            "workers=%s. _run_lock is a threading.Lock and cannot see across "
            "processes: concurrent scans and double-sends are now possible. "
            "See the comment in gunicorn.conf.py.", workers)


# Deliberately unused, and kept visible so the next person does not reach for
# it: sizing workers by CPU is the normal thing to do here and is wrong for
# this app until the run lock is shared.
_CPU_COUNT = multiprocessing.cpu_count()
