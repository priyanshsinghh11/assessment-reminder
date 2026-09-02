"""
Who pays when there is no CV to mark.

`_blend` has four ways of reaching a score without a CV mark, and three of them
must cost the candidate nothing. Only one -- a CV we fetched and genuinely
could not read, under CV_MISSING_POLICY=forfeit -- is allowed to move the
number, and even that is off by default now.

These exist because the distinction was got wrong in production. 365 graded
candidates carried a forfeited score, and 79 of them had never had their resume
fetched at all: no `resume_error`, nothing known to be wrong with their link,
just a backfill that had not reached them. Nikash Kathuria (submission 10851,
Chief of Staff, w=0.55) was marked 73.6 on the rubric and recorded as 33.1 for
a CV that downloads in a second. The rule that was missing is the last class
below.
"""

import pytest

from backend.grading import evaluator


def cv(reason=None, score=None):
    """A `_cv_assessment` return, in the shape `_blend` reads."""
    return {"scored": score is not None, "score": score,
            "criteria": [], "marked": 0, "reason": reason}


class TestCvAssessmentReasons:
    def test_fetched_and_empty_is_no_cv(self):
        verdict = evaluator._cv_assessment({}, has_cv=False, attempted=True)
        assert verdict["reason"] == "no_cv"

    def test_never_fetched_is_its_own_reason(self):
        # The distinction the whole fix turns on: nothing is known to be wrong
        # with this candidate's link, because nobody ever pulled it.
        verdict = evaluator._cv_assessment({}, has_cv=False, attempted=False)
        assert verdict["reason"] == "not_fetched"

    def test_a_marked_cv_is_scored_either_way(self):
        raw = {c["key"]: {"score": 4} for c in evaluator.CV_CRITERIA}
        verdict = evaluator._cv_assessment(raw, has_cv=True, attempted=True)
        assert verdict["scored"] and verdict["score"] == 80.0

    def test_a_present_but_unmarked_cv_says_so(self):
        verdict = evaluator._cv_assessment({}, has_cv=True, attempted=True)
        assert verdict["reason"] == "unmarked"


class TestBlendChargesNobodyForOurFailures:
    @pytest.mark.parametrize("reason", ["not_fetched", "unmarked"])
    @pytest.mark.parametrize("policy", ["forfeit", "rescale"])
    def test_our_failures_never_move_the_score(self, reason, policy, monkeypatch):
        # Whatever the policy is set to. CV_MISSING_POLICY decides what to do
        # about a CV we could not read, and in neither of these did we ever
        # establish that we could not.
        monkeypatch.setattr(evaluator, "CV_MISSING_POLICY", policy)
        score, applied = evaluator._blend(73.6, cv(reason=reason), 0.55)
        assert score == 73.6
        assert applied is False

    def test_the_chief_of_staff_case(self, monkeypatch):
        # Submission 10851 exactly: the seat's weight, the rubric total it was
        # marked at, and the number that was stored instead.
        monkeypatch.setattr(evaluator, "CV_MISSING_POLICY", "forfeit")
        assert evaluator._blend(73.6, cv(reason="no_cv"), 0.55)[0] == 33.1
        assert evaluator._blend(73.6, cv(reason="not_fetched"), 0.55)[0] == 73.6

    def test_rescale_lifts_an_unreadable_cv_too(self, monkeypatch):
        monkeypatch.setattr(evaluator, "CV_MISSING_POLICY", "rescale")
        score, applied = evaluator._blend(73.6, cv(reason="no_cv"), 0.55)
        assert score == 73.6 and applied is False

    def test_forfeit_still_bites_an_unreadable_cv(self, monkeypatch):
        # The one case that is still allowed to move the number, kept under
        # test so flipping the env var back is a decision rather than a
        # surprise.
        monkeypatch.setattr(evaluator, "CV_MISSING_POLICY", "forfeit")
        score, applied = evaluator._blend(80.0, cv(reason="no_cv"), 0.60)
        assert score == 32.0 and applied is False

    def test_a_scored_cv_is_folded_in(self):
        score, applied = evaluator._blend(80.0, cv(score=60.0), 0.25)
        assert score == 75.0 and applied is True

    def test_zero_weight_is_a_no_op(self):
        # The seats that score the record inside the grid. Nothing to blend.
        score, applied = evaluator._blend(73.6, cv(reason="not_fetched"), 0.0)
        assert score == 73.6 and applied is False


class TestFetchAttemptedIsReadFromTheRecord:
    """
    `cv_fetch_attempted` is decided from stored fields, never from the model.

    `resume_fetched_at` is written by set_resume() on both outcomes, so a null
    means the fetch never ran rather than that it ran and failed.
    """

    def attempted(self, submission):
        return (submission.get("resume_fetched_at") is not None
                or not (submission.get("resume_link") or "").strip())

    def test_a_link_nobody_pulled_is_not_attempted(self):
        assert self.attempted({"resume_link": "https://drive.google.com/x"}) is False

    def test_a_fetched_link_is_attempted_even_when_it_failed(self):
        assert self.attempted({"resume_link": "https://drive.google.com/x",
                               "resume_fetched_at": "2026-09-01",
                               "resume_error": "not_a_document:text/html"}) is True

    def test_no_link_at_all_counts_as_attempted(self):
        # There was nothing to fetch. The artefact is genuinely the
        # candidate's to supply, so CV_MISSING_POLICY should price it the way
        # it always has rather than this rule shielding them.
        assert self.attempted({"resume_link": ""}) is True
