"""
The assessment pipeline, as a package.

Nothing here is a framework. It is the same modules the project has always had,
grouped by the job they do so that a new reader can find the one they want
without opening twenty files to see which is which:

    core            configuration and the small shared helpers
    database        MongoDB access and schema migration
    scraping        the portal and Workable -- everything that reads the outside
    grading         the scoring engine: grids, anchors, tier resolution
    pipeline        the stages that are run: ingest, grade, regrade, calibrate
    notifications   every outbound email
    accounts        who may sign in, and which roles they see
    web             the Flask dashboard

The CLI entry points stay at the repository root as thin launchers, so
`python reminder.py --scan-only` and the crontab line still work unchanged.
"""
