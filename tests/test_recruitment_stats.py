"""
The recruitment line on a role card: applicants, the last application, and the
read on the pipeline.

THE RULE THIS SUITE HOLDS is that the quality read is a count of who clears the
bar and nothing more, and that "repost" is only said once there is nothing left
to wait for -- an ungraded submission may be the strong candidate.

Pure: the aggregation's rows are written out by hand.
"""

from backend.web import views_evaluations as views


def part(graded=0, best=0, better=0, good=0, top=None, last_at=""):
    """One store.role_recruitment_stats() entry. `at_least` overlaps."""
    return {"last_at": last_at, "graded": graded, "top": top,
            "at_least": {"best": best, "better": best + better,
                         "good": best + better + good, "okay": graded}}


def read(parts, pending=0, total=None, pipeline=None, scores=True):
    graded = sum(p["graded"] for p in parts)
    counts = {"total": graded + pending if total is None else total,
              "pending": pending}
    return views._recruitment(parts, counts, pipeline or {}, scores)


class TestTheFacts:
    def test_applicants_are_the_cards_own_total(self):
        assert read([part(graded=4)], total=31)["applicants"] == 31

    def test_the_last_application_is_the_newest_across_the_parts(self):
        stats = read([part(last_at="2026-09-28T02:31:44.420Z"),
                      part(last_at="2026-10-07T09:37:44.542Z")])
        assert stats["last_application"] == "2026-10-07T09:37:44.542Z"

    def test_a_role_nobody_applied_to_has_no_date(self):
        assert read([])["last_application"] is None


class TestTheQualityRead:
    def test_three_clearing_the_bar_is_strong(self):
        quality = read([part(graded=40, best=1, better=2, top=91.2)])["quality"]
        assert quality["key"] == "strong"
        assert quality["clear"] == 3
        assert quality["detail"] == "3 of 40 graded clear the bar"

    def test_one_or_two_is_thin(self):
        assert read([part(graded=40, better=2)])["quality"]["key"] == "thin"

    def test_none_is_weak(self):
        quality = read([part(graded=40, good=6, top=71.44)])["quality"]
        assert quality["key"] == "weak"
        assert quality["top"] == 71.4
        assert quality["detail"] == "none of 40 graded clear the bar"

    def test_the_bands_do_not_double_count(self):
        bands = read([part(graded=10, best=1, better=2, good=3)])["quality"]["bands"]
        assert bands == {"best": 1, "better": 2, "good": 3, "okay": 4}

    def test_nothing_graded_is_not_a_verdict(self):
        quality = read([part()], pending=12)["quality"]
        assert quality["key"] == "none"
        assert quality["detail"] == "12 waiting on grading"
        assert quality["repost"] is False

    def test_a_tier_and_the_unresolved_are_read_together(self):
        quality = read([part(graded=5, better=2), part(graded=5, best=1)])["quality"]
        assert quality["graded"] == 10 and quality["key"] == "strong"

    def test_it_is_left_out_where_scores_are_hidden(self):
        stats = read([part(graded=40, best=3)], scores=False)
        assert stats["quality"] is None
        assert stats["applicants"] == 40


class TestRepost:
    def test_all_graded_and_nobody_clears_the_bar(self):
        assert read([part(graded=40, good=6)])["quality"]["repost"] is True

    def test_not_while_submissions_are_still_ungraded(self):
        quality = read([part(graded=40)], pending=3)["quality"]
        assert quality["repost"] is False
        assert quality["label"] == "Weak so far"
        assert quality["detail"].endswith("3 not graded yet")

    def test_not_while_somebody_is_being_interviewed(self):
        quality = read([part(graded=40)], pipeline={"interview": 1})["quality"]
        assert quality["repost"] is False

    def test_not_when_somebody_clears_the_bar(self):
        assert read([part(graded=40, better=1)])["quality"]["repost"] is False

    def test_a_rejection_after_interview_does_not_hold_it_back(self):
        quality = read([part(graded=40)], pipeline={"rejected": 4})["quality"]
        assert quality["repost"] is True
