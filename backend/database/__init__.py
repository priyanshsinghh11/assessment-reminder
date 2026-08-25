"""
MongoDB access.

`mongo_store` is the only module that talks to the database. It keeps
portal-owned fields and our own fields apart, so a re-ingest overwrites what the
portal owns and leaves our grades, decisions and review tokens alone.

`migrate_db` copies an older database into the one MONGO_DB now points at.
"""
