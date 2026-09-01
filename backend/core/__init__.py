"""
Configuration and the small shared helpers.

    config          the single source of truth for env vars, job definitions
                    and timing rules. Owns PROJECT_ROOT, the anchor every path
                    in the project resolves from.
    utils           pure functions: business-day arithmetic, the reminder
                    dedupe key, and the tz-aware fix-up every Mongo timestamp
                    goes through.
    logging_setup   the one place logging is configured, for the CLIs, the
                    dashboard and both deployment entry points.

NOTHING IN THIS PACKAGE IMPORTS FROM THE REST OF THE PROJECT, and that is what
keeps the dependency graph acyclic -- everything else imports from here.

It has been broken once and it was not obvious: utils.py held the reminder
state functions, which imported backend.database.reminder_log, so the bottom of
the stack reached up into storage. Those functions now live in
backend/database/reminder_state.py, next to the collection they read. If
something here needs a name from another package, that is the sign it is not a
core helper -- move it to the layer that owns the thing it depends on.
"""
