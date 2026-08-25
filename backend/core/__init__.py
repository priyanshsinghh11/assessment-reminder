"""
Configuration and shared helpers.

`config` is the single source of truth for env vars, job definitions and timing
rules, and it also owns PROJECT_ROOT -- the anchor every path in the project is
resolved from. `utils` is business-day math and reminder state tracking.

Everything else in the project imports from here; this package imports from
nothing else in the project, which is what keeps the dependency graph acyclic.
"""
