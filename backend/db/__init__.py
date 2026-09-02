"""
MongoDB access.

`store` is the only module that talks to the submissions database. It keeps
portal-owned fields and our own fields apart, so a re-ingest overwrites what the
portal owns and leaves our grades, decisions and review tokens alone.

`reminder_log` is the collection behind the reminder dedupe, and the single
conditional update that settles a race between two runs. `reminder_state` is
the policy on top of it -- the maximum, the business-day gap, the suppression
flag, and the claim a send has to win before it may mail anybody. Kept apart
because one of them is a query and the other is a decision.

THE TWO MIGRATIONS MOVED OUT, to tools/migrate_db.py and
tools/migrate_reminder_log.py, next to tools/migrate_to_atlas.py. They are
one-off scripts a person runs by hand, not part of any code path in here, and
three of them in one place is easier to reason about than two of them hiding
among the modules the app imports on every request.
"""
