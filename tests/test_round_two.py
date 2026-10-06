"""
Round 2: the optional second interview between the first one and the outcome.

THE RULE THIS SUITE HOLDS is that round 2 follows a round 1. A candidate who
was never invited to a first interview has no manager on record and no
invitation in their inbox, and a board that put them in "Round 2" would be
describing a meeting nobody arranged.

Pure: the one database write is made against a fake collection.
"""

import pytest

from backend.db import store


class FakeSubmissions:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))


class FakeDb:
    def __init__(self):
        self.submissions = FakeSubmissions()


class TestWhoHasInterviewed:
    def test_nobody_outside_the_pipeline(self):
        assert not store.has_interviewed({})
        assert not store.has_interviewed({"pipeline": {"stage": None}})

    def test_someone_at_their_first_interview(self):
        assert store.has_interviewed({"pipeline": {"stage": "interview"}})

    def test_someone_already_in_round_two(self):
        assert store.has_interviewed({"pipeline": {"stage": "interview_2"}})

    def test_someone_rejected_after_their_interview(self):
        # The correction case: rejected by mistake, and put through instead.
        assert store.has_interviewed({"pipeline": {
            "stage": "rejected",
            "history": [{"stage": "interview"}, {"stage": "rejected"}],
        }})

    def test_someone_rejected_without_ever_being_seen(self):
        assert not store.has_interviewed({"pipeline": {
            "stage": "rejected", "history": [{"stage": "rejected"}],
        }})


class TestTheStage:
    def test_round_two_sits_between_the_interview_and_the_outcomes(self):
        assert store.PIPELINE_STAGES == ("interview", "interview_2",
                                         "hired", "rejected")

    def test_the_move_is_written_with_its_history(self, monkeypatch):
        db = FakeDb()
        monkeypatch.setattr(store, "get_db", lambda: db)
        store.set_pipeline_stage(7, "interview_2", note="Strong on delivery")

        query, update = db.submissions.updates[0]
        assert query == {"_id": 7}
        assert update["$set"]["pipeline.stage"] == "interview_2"
        assert update["$push"]["pipeline.history"]["stage"] == "interview_2"
        # The first interview's time is left alone, not blanked.
        assert "pipeline.interview_at" not in update["$set"]

    def test_an_unknown_stage_is_still_refused(self):
        with pytest.raises(ValueError):
            store.set_pipeline_stage(7, "interview_3")


# ---------------------------------------------------------------------------
# The round 2 invitation
# ---------------------------------------------------------------------------

from backend.mail import candidate_mail

ANITA = {"name": "Anita Desai", "email": "anita@example.com",
         "cal_link": "https://cal.com/anita"}
RAVI = {"name": "Ravi Rao", "title": "CTO", "email": "ravi@example.com",
        "cal_link": "https://cal.com/ravi"}
ROLE = {"title": "Developer", "hiring_managers": [ANITA]}
CANDIDATE = {"_id": 7, "candidate_name": "Asha Menon",
             "candidate_email": "asha@example.com",
             "pipeline": {"stage": "interview_2", "interviewer": "Anita Desai"}}


@pytest.fixture
def directory(monkeypatch):
    """Ravi is a manager on another role, which is where round 2 often goes."""
    monkeypatch.setattr(
        store, "find_manager",
        lambda email: RAVI if email == RAVI["email"] else None)


class TestTheRoundTwoInvitation:
    def test_it_carries_the_second_interviewers_link(self, directory):
        email = candidate_mail.build_stage_email(
            CANDIDATE, ROLE, "interview_2", manager_email=RAVI["email"])
        assert email["cal_link"] == "https://cal.com/ravi"
        assert "Ravi Rao" in email["text"]
        assert "cal.com/anita" not in email["text"]
        assert "Second interview" in email["subject"]

    def test_the_first_interviewer_is_never_assumed(self, directory):
        # One manager on the role and their name on the first interview: both
        # are fallbacks round 1 uses, and both would be the wrong calendar.
        with pytest.raises(candidate_mail.CandidateMailError):
            candidate_mail.build_stage_email(CANDIDATE, ROLE, "interview_2")

    def test_someone_on_no_role_needs_only_a_name_and_a_link(self, directory):
        email = candidate_mail.build_stage_email(
            CANDIDATE, ROLE, "interview_2",
            interviewer="Sam Lee", cal_link="cal.com/sam")
        assert email["cal_link"] == "https://cal.com/sam"
        assert "Sam Lee" in email["text"]

    def test_a_pasted_link_wins_over_the_stored_one(self, directory):
        email = candidate_mail.build_stage_email(
            CANDIDATE, ROLE, "interview_2", manager_email=RAVI["email"],
            cal_link="cal.com/ravi/second")
        assert email["cal_link"] == "https://cal.com/ravi/second"

    def test_a_new_link_is_not_a_duplicate_send(self):
        sent = {**CANDIDATE, "pipeline": {"stage": "interview_2", "emails": [
            {"stage": "interview_2", "ok": True,
             "cal_link": "https://cal.com/ravi"}]}}
        assert candidate_mail.already_sent(
            sent, "interview_2", "https://cal.com/ravi") is not None
        assert candidate_mail.already_sent(
            sent, "interview_2", "https://cal.com/sam") is None
