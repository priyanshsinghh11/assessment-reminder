"""
The Flask dashboard.

`server` serves the static `frontend/` directory and the reminder, evaluation,
shortlist and account endpoints on top of the same functions the CLIs call --
and enforces, on every route that names a role, who is allowed to see it.
"""
