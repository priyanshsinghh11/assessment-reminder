"""
The stages that get run.

    ingest      portal -> MongoDB, plus the missing-artefact screening rule
    grade       grade a role, or the whole backlog
    regrade     re-score submissions that already have a verdict
    calibrate   is the grader using the scale, or just detecting missing
                sections? A grid is only worth its anchors if marks spread.

Each is a subcommand of manage.py at the repository root -- `python manage.py
grade --job 33` runs grade.main() here, and `python -m backend.pipeline.grade
--job 33` runs the same one directly.
"""
