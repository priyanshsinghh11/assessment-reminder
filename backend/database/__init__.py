"""
MongoDB access.

`mongo_store` is the only module that talks to the submissions database. It
keeps portal-owned fields and our own fields apart, so a re-ingest overwrites
what the portal owns and leaves our grades, decisions and review tokens alone.

`reminder_log` is the collection behind the reminder dedupe, and the single
conditional update that settles a race between two runs. `reminder_state` is
the policy on top of it -- the maximum, the business-day gap, the suppression
flag, and the claim a send has to win before it may mail anybody. Kept apart
because one of them is a query and the other is a decision.

`migrate_db` copies an older database into the one MONGO_DB now points at, and
`migrate_reminder_log` moves the old state/reminder_log.json into Mongo.
"""
