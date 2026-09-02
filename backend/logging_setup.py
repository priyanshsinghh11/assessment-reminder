"""
The one place logging is configured, for every entry point in the project.

WHY THIS IS IN core/ AND NOT WHERE IT STARTED. setup_logging() lived in
`notifications/reminder.py` because the reminder run was the first thing that
needed a log file. Five other modules then grew to need it -- both migrations,
all four pipeline CLIs, the dashboard -- and wsgi.py calls it at import, before
Flask exists, on every deployment.

That made `import reminder` the price of configuring a logger. reminder.py
pulls in the Workable scanner, the portal scraper, the Brevo client, the
unsubscribe tokens and the reminder log, so the dashboard was importing the
entire send path -- and reaching across a package boundary in the wrong
direction -- to decide where INFO lines go. A logging call landing in a leaf
module is also how an import cycle starts: anything reminder.py imports could
never call setup_logging() itself.

`core` imports nothing else in this project, which is what makes it safe for
everything to import from here.

reminder.py re-exports the name, so `reminder.setup_logging()` still works.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from backend.config import (
    LOG_DIR,
    LOG_FILE,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    LOG_FILE_MODE,
)


def setup_logging(level=logging.INFO):
    """
    One log to a rotating file, one to stdout.

    ROTATING, NOT PLAIN. This file names candidates and their addresses on
    every line that matters, so an unbounded FileHandler is a plaintext
    register of every applicant that grows for ever and is never pruned. The
    size and the number of kept files are the retention policy and live in
    config -- see the note on LOG_MAX_BYTES.

    Called by both CLI runs and the server, and more than once in a process
    that imports both. logging.basicConfig() does nothing if the root logger
    already has handlers, which quietly made the second call a no-op -- fine by
    accident, and only by accident. This configures the root logger directly
    and returns early if it has already been done, so the behaviour is the
    intent rather than a side effect of another function's guard clause.
    """
    root = logging.getLogger()
    if getattr(root, "_ajaia_configured", False):
        return

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # THE FILE HALF IS OPTIONAL. IT IS THE ONLY PART OF THIS THAT NEEDS A DISK.
    #
    # A serverless platform mounts the deployment read-only -- Vercel, Lambda
    # and Cloud Functions all do, with only /tmp writable. This mkdir then
    # raises OSError, and because setup_logging() runs at IMPORT from wsgi.py
    # it takes the process down before Flask exists. Every request 500s
    # identically, and nothing in the error names a log directory, so the
    # cause is invisible from the outside.
    #
    # Skipped rather than fatal, because those are exactly the platforms that
    # collect stdout instead: the handler below is the real log there. What is
    # lost is the rotation policy, and a filesystem that is discarded between
    # invocations had nothing to rotate. Set LOG_DIR somewhere writable (/tmp)
    # if you want the file back.
    #
    # Only OSError. A misconfigured LOG_MAX_BYTES should still fail loudly --
    # this is a narrow allowance for "there is no disk", not a blanket one.
    file_handler = None
    disk_error = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        disk_error = exc

    if file_handler is not None:
        # Tighten the mode on the live file and on every rotated one. chmod is
        # a no-op on Windows and the try/except covers a filesystem that does
        # not support it -- a log that cannot be locked down is still a log
        # worth writing, so this narrows permissions where it can and never
        # stops a run.
        def _protect(path) -> None:
            try:
                os.chmod(path, LOG_FILE_MODE)
            except (OSError, NotImplementedError):
                pass

        _protect(LOG_FILE)
        _rotator = file_handler.rotate

        def rotate(source, dest):
            _rotator(source, dest)
            _protect(dest)

        file_handler.rotate = rotate
        file_handler.setFormatter(formatter)

    # The console, reconfigured to UTF-8 where it will allow it.
    #
    # A Windows console defaults to cp1252, and a log line carrying a character
    # it cannot encode does not print a mangled line -- logging raises, prints
    # the whole UnicodeEncodeError with a call stack, and carries on. The run
    # survives, but a single quote from a candidate's CV turns one INFO line
    # into forty lines of traceback.
    #
    # It shows up wherever candidate text reaches the log, and CV grading is
    # the worst of it: resumes are full of non-breaking hyphens, curly quotes
    # and em dashes, none of which are in cp1252. errors="replace" is the
    # backstop for a stream that cannot be reconfigured at all -- a question
    # mark in place of a dash is a fair trade for a legible log.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root.setLevel(level)
    if file_handler is not None:
        root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root._ajaia_configured = True

    # Said once, on the handler that did survive, so "why is logs/ empty" has
    # an answer in the place somebody is already looking.
    if disk_error is not None:
        logging.getLogger(__name__).warning(
            "No log file: %s is not writable (%s). Logging to stdout only. "
            "This is normal on a read-only or serverless filesystem.",
            LOG_DIR, disk_error)
