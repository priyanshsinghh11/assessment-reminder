"""
Who may sign in, and which roles they see.

`auth` owns password hashing, sessions and `visible_job_ids()` -- the whole
access rule, in one place, because an access rule spread across a codebase is
one that nobody can check. `manage_users` is the terminal way in: the first
admin, before there is an account to sign in with, and the way back when
nobody can.

The regression test for all of it is `tests/test_access.py`. Run it after
adding any route that names a role.
"""
