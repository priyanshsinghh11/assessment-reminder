"""
The role analyst: what the Summary drawer says about a role.

THE RULE THIS SUITE HOLDS is that every finding is a count or a name off the
record, that nothing derived from a score is said to an account that may not
read scores, and that the written summary is only re-asked for when the facts
under it change.

Pure: the model is never called.
"""

from datetime import date

from backend.pipeline import role_analyst

ROLE = {"_id": 38, "title": "Developer", "published": True}
TODAY = date(2026, 10, 7)


def quality(key, graded=40, clear=0, top=None, repost=False, pending=0):
    detail = (f"{clear} of {graded} graded clear the bar" if clear
              else f"none of {graded} graded clear the bar")
    return {"key": key, "label": key.capitalize(), "detail": detail,
            "graded": graded, "clear": clear, "bands": {}, "top": top,
            "bar": 75, "repost": repost}


def read(q, applicants=120, last="2026-10-06T09:00:00.000Z", counts=None,
         pipeline=None, **kwargs):
    recruitment = {"applicants": applicants, "last_application": last,
                   "quality": q}
    return role_analyst.read(ROLE, counts or {"total": applicants},
                             pipeline or {}, recruitment, today=TODAY, **kwargs)


def texts(summary):
    return " | ".join(f["text"] for f in summary["findings"])


class TestIsThereAnybodyGood:
    def test_no_good_candidates_is_said_in_as_many_words(self):
        summary = read(quality("weak", top=58.2))
        assert summary["headline"] == "No good candidates so far"
        assert summary["tone"] == "warn"
        assert "none of the 40 graded clear the bar" in texts(summary)
        assert "best score is 58.2 against a bar of 75" in texts(summary)

    def test_a_repost_is_called_for_when_nothing_is_left_to_wait_for(self):
        summary = read(quality("weak", top=58.2, repost=True))
        assert "repost needed" in summary["headline"]
        assert "needs reposting" in texts(summary)

    def test_a_thin_pipeline_counts_who_is_left(self):
        summary = read(quality("thin", clear=1))
        assert summary["headline"] == "Thin pipeline"
        assert "Only 1 candidate of 40 graded clears the bar." in texts(summary)

    def test_a_strong_one_is_good_news(self):
        summary = read(quality("strong", clear=7))
        assert (summary["headline"], summary["tone"]) == ("Strong pipeline", "good")

    def test_ungraded_work_is_a_caveat_on_the_read(self):
        summary = read(quality("weak"), counts={"total": 120, "pending": 30})
        assert "30 submissions still waiting on grading" in texts(summary)


class TestWhoStandsOut:
    CLEARING = {"count": 2, "top": [
        {"id": 1, "name": "Asha Menon", "score": 91.24, "stage": None},
        {"id": 2, "name": "Ravi Rao", "score": 80.0, "stage": "interview"}]}
    PEOPLE = [
        {"id": 1, "name": "Asha Menon", "employers": ["Google", "McKinsey"],
         "schools": ["MIT"], "stage": None},
        {"id": 3, "name": "Sam Lee", "employers": [], "schools": ["Stanford"],
         "stage": None}]

    def test_the_top_of_the_queue_is_named_with_scores(self):
        summary = read(quality("thin", clear=2), clearing=self.CLEARING)
        assert "Asha Menon (91.2) and Ravi Rao (80.0)" in texts(summary)
        assert summary["top"][0] == {"id": 1, "name": "Asha Menon",
                                     "score": 91.2, "stage": None}

    def test_where_somebody_worked_and_studied_is_said(self):
        summary = read(quality("thin", clear=2), people=self.PEOPLE)
        assert "Asha Menon worked at Google and McKinsey" in texts(summary)
        assert "Sam Lee studied at Stanford" in texts(summary)

    def test_a_long_list_is_cut_off_and_counted(self):
        people = [{"id": n, "name": f"Person {n}", "employers": ["Google"],
                   "schools": [], "stage": None} for n in range(7)]
        assert "and 3 more with a notable employer" in texts(
            read(quality("strong", clear=7), people=people))

    def test_somebody_turned_down_after_interview_is_not_notable(self):
        rows = [{"_id": 1, "candidate_name": "Asha Menon",
                 "pipeline": {"stage": "rejected"},
                 "pedigree": {"employers": [{"name": "Google"}]}},
                {"_id": 2, "candidate_name": "Ravi Rao",
                 "pedigree": {"employers": [{"name": "Stripe"}], "schools": []}}]
        assert [p["name"] for p in role_analyst.notable(rows)] == ["Ravi Rao"]


class TestWhatIsInTheWay:
    def test_good_people_and_nobody_invited(self):
        summary = read(quality("strong", clear=5),
                       clearing={"count": 5, "top": []})
        assert "5 candidates above the bar and nobody has been invited" in texts(summary)

    def test_not_said_once_interviews_are_under_way(self):
        summary = read(quality("strong", clear=5),
                       clearing={"count": 5, "top": []},
                       pipeline={"interview": 2, "interview_2": 1})
        assert "nobody has been invited" not in texts(summary)
        assert "2 at first interview and 1 in round 2" in texts(summary)

    def test_a_role_nobody_has_applied_to_lately(self):
        summary = read(quality("thin", clear=1), last="2026-08-01T10:00:00.000Z")
        assert "No new applicant in 67 days" in texts(summary)
        assert "01 Aug 2026" in texts(summary)

    def test_a_role_with_no_manager(self):
        summary = read(quality("thin", clear=1), owners={"managers": []})
        assert "No hiring manager is assigned" in texts(summary)


class TestWithoutScores:
    def test_nothing_derived_from_a_score_is_said(self):
        summary = read(None, clearing=TestWhoStandsOut.CLEARING,
                       people=TestWhoStandsOut.PEOPLE)
        said = texts(summary)
        assert summary["headline"] == "120 applicants"
        assert summary["top"] == []
        assert "bar" not in said and "91.2" not in said
        # Where somebody worked is not a score, and is still said.
        assert "worked at Google" in said


class TestTheWrittenSummaryIsCached:
    def test_the_same_facts_hash_the_same_on_another_day(self):
        one = role_analyst.read(ROLE, {"total": 9}, {}, {
            "applicants": 9, "last_application": "2026-10-06T09:00:00.000Z",
            "quality": quality("thin", clear=1)}, today=date(2026, 10, 7))
        two = role_analyst.read(ROLE, {"total": 9}, {}, {
            "applicants": 9, "last_application": "2026-10-06T09:00:00.000Z",
            "quality": quality("thin", clear=1)}, today=date(2026, 10, 9))
        assert role_analyst.facts_hash(one) == role_analyst.facts_hash(two)

    def test_a_new_applicant_changes_it(self):
        before = read(quality("thin", clear=1), applicants=9)
        after = read(quality("thin", clear=1), applicants=10)
        assert role_analyst.facts_hash(before) != role_analyst.facts_hash(after)

    def test_the_model_is_shown_the_findings_and_nothing_it_was_not_given(self):
        shown = role_analyst.facts(read(quality("weak", top=58.2)))
        assert shown["role"] == "Developer"
        assert shown["clear_the_bar"] == 0 and shown["bar"] == 75
        assert any("No good candidates" in line for line in shown["findings"])
