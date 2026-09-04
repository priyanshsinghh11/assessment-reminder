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

Fetching the CV DOES live here, for the same reason. Every brief is written
from two documents -- the assessment answer and the resume -- and the resume
half used to depend on somebody having run `manage.py ingest --resumes` first.
When they had not, the candidate still graded: `_resume_block` says "no CV text
available", the model obeys it, and the brief comes back with a resume section
that reads "not available" for a candidate whose link was sitting in the row
the whole time. Nothing errors, so nothing shows it. `ensure_resume` closes
that by pulling the link at grade time, at the one place every grading path
passes through.

Grid resolution deliberately does NOT live here. `evaluator.derive_grid`
already returns the pack grid for any slug the pack covers, without reaching
for a model, and a CV-only slug is covered by definition: it cannot derive a
grid from an assessment it does not have. So every existing caller resolves the
right grid already and only the marking had to be routed.
"""

import logging

from backend.db import store
from backend.grading import cv_evaluator, evaluator
from backend.grading import rubric_pack as pack
from backend.scraping import resume_reader

log = logging.getLogger(__name__)


def ensure_resume(submission: dict, persist: bool = True) -> dict:
    """
    Make sure this candidate's CV text is in hand before they are graded.

    Fetches only what has never been fetched. The conditions mirror
    `store.needs_resume` so the on-demand path and the `--resumes` backfill
    agree about what counts as done: a row is work if it has a link and either
    nobody has attempted it (`resume_fetched_at` is null) or the candidate has
    since re-uploaded, which shows up as `resume_source_link` no longer
    matching `resume_link`.

    A stored failure is NOT retried here. Roughly 40% of these links are
    private Drive files, LinkedIn profile pages or scans with no text layer,
    and they will be exactly as private on the second attempt -- retrying them
    would put a fetch timeout in front of every grading call for a result the
    backfill already has. `--resumes --retry-transient` is still the place to
    take another pass at the ones that were about the moment.

    Mutates and returns `submission`, because that dict is what `evaluate`
    reads `resume_text`, `resume_error` and `resume_fetched_at` out of.
    `persist` writes the same four fields back so the next run does not pay for
    this fetch again; the dry-run path passes False and keeps it in memory.
    """
    link = (submission.get("resume_link") or "").strip()
    if not link:
        return submission
    attempted = submission.get("resume_fetched_at") is not None
    # `resume_link` may be a portal/profile URL while the actual fetched file
    # is recorded in `resume_source_link`. Comparing those URLs makes every
    # Re-evaluate download the same CV again, which can take minutes.
    if attempted:
        return submission

    text, error = resume_reader.read_resume(link)
    if error:
        log.info("  resume not readable for submission %s [%s]",
                 submission.get("_id"), error)
    submission["resume_text"] = text
    submission["resume_error"] = error
    submission["resume_source_link"] = link
    # Set whether or not the write below happens, and that is the point.
    # `evaluate` reads this field to decide who pays for a missing CV -- an
    # unfetched link is our gap, a fetched-and-failed one is priced by
    # CV_MISSING_POLICY -- and by here the fetch has genuinely been attempted.
    # Setting it only on a successful store would make a dry run describe the
    # candidate differently from the real run a moment later, which is the one
    # thing a dry run must not do.
    submission["resume_fetched_at"] = store.now()
    if not persist:
        return submission
    try:
        store.set_resume(submission["_id"], text, error, link)
    except Exception as exc:                        # noqa: BLE001
        # A CV that will not store must not cost the candidate their grade.
        # The text is already in the dict, so this run marks them with it
        # either way and only the saving is lost -- the next run pays for the
        # same fetch again. The backfill hit this on a resume holding half a
        # surrogate pair that BSON would not encode; see ingest_resumes for
        # the same guard.
        log.warning("Could not store resume for submission %s: %s",
                    submission.get("_id"), exc)
    return submission


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
    ensure_resume(submission)
    if is_cv_only(grid):
        return cv_evaluator.evaluate_and_store_cv(submission, role, grid)
    return evaluator.evaluate_and_store(submission, role, grid)


def grade(submission: dict, role: dict, grid: dict) -> dict:
    """The same choice, without writing to Mongo."""
    ensure_resume(submission, persist=False)
    if is_cv_only(grid):
        return cv_evaluator.evaluate_cv(submission, role, grid)
    return evaluator.evaluate(submission, role, grid)
