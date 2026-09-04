#!/usr/bin/env python3
"""
Start the dashboard.

    python server.py                                   # http://127.0.0.1:5000
    python server.py --port 5001
    python server.py --review-only --host 0.0.0.0 --port 5051

THE LOG IS THE TERMINAL YOU RAN THIS IN. setup_logging() puts every INFO line
on stdout as well as into logs/reminder.log, so the window stays the live view
-- each request, each scan, each send, as it happens -- and the file is the
copy you go back to afterwards. Nothing is redirected here on purpose: send
this to a file and the terminal goes quiet, which is the thing this file exists
to stop.

WHY THERE IS A LAUNCHER AT THE ROOT AGAIN. manage.py's docstring is about
deleting twelve of these, and that stands -- grade, ingest, regrade, calibrate
and the rest are typed occasionally, from notes, and belong behind one name.
This one is different in the way that mattered for those twelve: it is typed
every day, it is the first thing run in a fresh terminal, and it is the only
command whose output you sit and watch. `python server.py` is what that is
worth being.

`python manage.py serve` runs this same main() and keeps working -- nothing was
moved and no flag changed. So does `python -m backend.web.server`. Three ways
to say one thing, and the logic is in none of them: it is in
backend/web/server.py, whose docstring covers what the server actually does and
which view module owns which URL.

NOT WHAT A REAL SERVER IMPORTS. This ends in app.run(), Werkzeug's development
server -- one worker, no request queue, no timeouts, and it says so in the log.
Deployments import wsgi.py under gunicorn instead; see the note there about why
that file cannot just be `from backend.web.server import app`.

argv passes through untouched. argparse lives in backend/web/server.py, so
`python server.py --help` is that parser's help, and this module knows nothing
about any of the flags it forwards.
"""

from backend.web.server import main

if __name__ == "__main__":
    main()
