"""
Which grader marks a role.

There are two, and the difference is not a preference -- it is what the
candidate handed in. A portal role has an assessment answer and
`evaluator.evaluate` marks that, with the CV as background. A CV-only role has
no answer at all and `cv_evaluator.evaluate_cv` marks the record instead.

Every batch path in the system fans out through one call, and before this
module there were four of them: grade.py, regrade.py, and the dashboard's
single-candidate and whole-role buttons. Each would have had to know about the
split, and the failure when one did not was silent-ish and ugly: a CV-only
candidate sent to `evaluate` raises "Submission has no answer text", so the
dashboard's Grade button on the new role would fail for every candidate on it
with a message about a thing that role does not have.

One function instead, so a third kind of role -- if there is ever one -- is a
change here and nowhere else.

Grid resolution deliberately does NOT live here. `evaluator.derive_grid`
already returns the pack grid for any slug the pack covers, without reaching
for a model, and a CV-only slug is covered by definition: it cannot derive a
grid from an assessment it does not have. So every existing caller resolves the
right grid already and only the marking had to be routed.
"""

import logging

from backend.grading import cv_evaluator, evaluator
from backend.grading import rubric_pack as pack

log = logging.getLogger(__name__)


def is_cv_only(grid: dict) -> bool:
    """
    Whether this grid is marked from the record alone.

    Read off the grid rather than off the role, because the grid is what
    decides. A grid whose every criterion sits in the `background` block has
    nothing in it that could be marked from an assessment answer, which is the
    same statement as "this seat has no work sample" made in the one place the
    grader can check it.

    Not read from `role["cv_only"]`, which is a label somebody typed into
    config and could be wrong, or absent on a role written before the flag
    existed.
    """
    criteria = (grid or {}).get("criteria") or ()
    return bool(criteria) and all(c.get("block") == "background"
                                  for c in criteria)


def grade_and_store(submission: dict, role: dict, grid: dict) -> dict:
    """Mark one candidate with whichever grader their role uses, and store it."""
    if is_cv_only(grid):
        return cv_evaluator.evaluate_and_store_cv(submission, role, grid)
    return evaluator.evaluate_and_store(submission, role, grid)


def grade(submission: dict, role: dict, grid: dict) -> dict:
    """The same choice, without writing to Mongo."""
    if is_cv_only(grid):
        return cv_evaluator.evaluate_cv(submission, role, grid)
    return evaluator.evaluate(submission, role, grid)
