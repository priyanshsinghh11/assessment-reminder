"""
The assessment pipeline, as a package.

Nothing here is a framework. It is the same modules the project has always had,
grouped by the job they do so that a new reader can find the one they want
without opening twenty files to see which is which:

    config          the single source of truth for env vars, job definitions
                    and timing rules. Owns PROJECT_ROOT, the anchor every path
                    in the project resolves from.
    utils           pure functions: business-day arithmetic, the reminder
                    dedupe key, and the tz-aware fix-up every Mongo timestamp
                    goes through.
    logging_setup   the one place logging is configured, for the CLI, the
                    dashboard and both deployment entry points.
    auth            who may sign in, and `visible_job_ids()` -- the whole
                    access rule, in one place, because an access rule spread
                    across a codebase is one nobody can check. Its regression
                    test is tests/test_access.py; run it after adding any route
                    that names a role.
    manage_users    the terminal way in: the first admin, before there is an
                    account to sign in with, and the way back when nobody can.

    db              MongoDB access
    scraping        the portal and Workable -- everything that reads the outside
    grading         the scoring engine: grids, anchors, tier resolution
    pipeline        the stages that are run: ingest, grade, regrade, calibrate
    mail            every outbound email
    web             the Flask dashboard

THE FIVE MODULES ABOVE THE PACKAGES USED TO BE TWO MORE PACKAGES -- `core`
(config, utils, logging_setup) and `accounts` (auth, manage_users). A directory
holding three files, whose name a reader has to learn before they can find
`config`, is a level of nesting that costs an import path and returns nothing.
They sit at the top of `backend` now, which is also where the dependency graph
says they belong: NOTHING HERE IMPORTS FROM THE PACKAGES BELOW IT, and that is
what keeps the graph acyclic -- everything else imports from these.

That rule has been broken once and it was not obvious: utils.py held the
reminder state functions, which imported the reminder log, so the bottom of the
stack reached up into storage. Those functions live in backend/db/
reminder_state.py now, next to the collection they read. If something at this
level needs a name from a package below, that is the sign it is not a shared
helper -- move it to the layer that owns the thing it depends on.

Every CLI is one command: `python manage.py <command>` at the repository root.
"""
