"""
One-off scripts, run by hand. Nothing in backend/ imports from here.

    migrate_db            copy an older database into the one MONGO_DB names
    migrate_reminder_log  move state/reminder_log.json into MongoDB
    migrate_to_atlas      the local-to-Atlas move. Already done -- see the
                          warning in that file before running it again
    llm_latency_bench     how long a grading call actually takes
    make_favicon          regenerates frontend/assets from the logo

The first two are reachable as `python manage.py migrate-db` and
`python manage.py migrate-reminder-log`; manage.py imports them lazily, so
nothing here is loaded unless one of those commands is the one being run. That
matters because .vercelignore excludes this directory from the deployed bundle.
"""
