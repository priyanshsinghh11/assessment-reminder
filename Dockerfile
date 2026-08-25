# syntax=docker/dockerfile:1
#
# The dashboard and the manager review surface, served by gunicorn.
#
#   docker build -t assessment-reminder .
#   docker run --env-file .env -p 8080:8080 assessment-reminder
#
# ONE IMAGE, TWO PROCESSES. This image runs either mode; REVIEW_ONLY picks
# which (see wsgi.py). The dashboard shows every role and every send button and
# belongs somewhere private; the review surface is the one that may face the
# internet. Run two containers from this image rather than one serving both.

FROM python:3.11-slim AS base

# PYTHONUNBUFFERED so log lines reach the platform's collector as they happen
# rather than when a buffer fills -- a send that hangs should be visible while
# it is hanging.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first, so a code change does not re-resolve the dependency tree.
# Nothing here needs a compiler or a system library: the PDF and DOCX readers
# (pypdf, python-docx) and the spreadsheet writer (openpyxl) are pure Python,
# which is why requirements.txt says so out loud. If that stops being true,
# this is where build-essential goes -- in a builder stage, not in the image
# that ships.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Runs as a real user. Not ceremony: this process scrapes a portal with stored
# credentials and mails candidates, and the blast radius of a bug in it should
# not include the image it is running in.
RUN useradd --create-home --uid 10001 app

COPY --chown=app:app . .

# state/ holds the cached portal scan; logs/ holds the file the dashboard's Logs
# panel reads back. Created here so they exist and are writable even when
# nothing is mounted over them.
#
# THE REMINDER DEDUPE LOG IS NO LONGER HERE. It used to be
# state/reminder_log.json, which made this directory the one thing standing
# between a candidate and a second copy of the same email -- and made an
# ephemeral container filesystem a correctness problem rather than an
# inconvenience. It now lives in MongoDB behind an atomic claim, so losing this
# directory costs a cached scan and some log tail, not the dedupe.
#
# What is left here is genuinely disposable: last_scan.json is rebuilt by the
# next Sync portal click, and logs/ rotates anyway. A volume is still worth
# mounting so the Logs panel survives a restart, but nothing about correctness
# depends on it now.
RUN mkdir -p /app/state /app/logs && chown -R app:app /app/state /app/logs

USER app

ENV PORT=8080
EXPOSE 8080

# Liveness only -- "the process is answering HTTP". Deliberately does not check
# Mongo: the app is written to stay up and explain a database outage on the
# page (503 from _guard_auth) rather than fall over, and a health check that
# killed the container for it would fight that on purpose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz', timeout=4).status==200 else 1)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
