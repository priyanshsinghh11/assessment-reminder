"""
A hiring manager moving a candidate to interview by hand.

THE RULE THIS SUITE HOLDS is that the manual move is a record and nothing
more: the board says "interview", the candidate's inbox stays empty, and the
token's own list is still the only set of people it can touch. The written
invitation is untouched and still goes through the composer.

Pure: the store is held in memory and the mail transport is a list.
"""

import pytest

from backend.mail import candidate_mail

ROLE = {"_id": 38, "title": "Developer"}
MANAGER = {"name": "Ravi Rao", "email": "ravi@example.com"}
LINK = {"_id": "tok", "job_id": 38, "manager": MANAGER, "submission_ids": [7]}


@pytest.fixture
def review(monkeypatch):
    from backend.web import server, views_review as views

    calls = {"sent": [], "moved": [], "actions": []}
    current = {"pipeline": {}}

    monkeypatch.setattr(views, "_mongo_guard", lambda: None)
    monkeypatch.setattr(views, "_review_guard", lambda token: (LINK, None))
    monkeypatch.setattr(views.store, "get_submission", lambda sid: {
        "_id": sid, "job_id": 38, "candidate_name": "Asha Patel",
        "candidate_email": "asha@example.com", **current})
    monkeypatch.setattr(views.store, "get_role", lambda job_id: ROLE)
    monkeypatch.setattr(views.store, "set_pipeline_stage",
                        lambda sid, stage, **kw: calls["moved"].append((stage, kw)))
    monkeypatch.setattr(views.store, "record_review_action",
                        lambda *a: calls["actions"].append(a))
    monkeypatch.setattr(candidate_mail, "PIPELINE_EMAILS_ENABLED", True)
    monkeypatch.setattr(candidate_mail, "PIPELINE_AUTO_EMAIL", True)
    monkeypatch.setattr(candidate_mail.brevo_client, "send_email",
                        lambda **kwargs: calls["sent"].append(kwargs))
    server.app.config["TESTING"] = True
    server.app.config["REVIEW_ONLY"] = False
    return server.app.test_client(), calls, current


def decide(client, **body):
    return client.post("/api/review/tok/decision", json=body)


class TestTheManualMove:
    def test_it_moves_them_and_emails_nobody(self, review):
        client, calls, _ = review
        reply = decide(client, submission_id=7, stage="interview")
        assert reply.status_code == 200
        (stage, kw), = calls["moved"]
        assert stage == "interview"
        assert kw["source"] == "manager"
        assert kw["interviewer"] == "Ravi Rao"
        # Both switches on, and still nothing leaves.
        assert calls["sent"] == []
        assert reply.get_json()["mail"]["sent"] is False
        assert "No invitation was sent" in reply.get_json()["message"]

    def test_it_is_on_the_links_audit_trail(self, review):
        client, calls, _ = review
        decide(client, submission_id=7, stage="interview", note="Booked by phone")
        assert calls["actions"] == [("tok", 7, "interview", "Booked by phone")]

    def test_only_candidates_on_the_list_they_were_sent(self, review):
        client, calls, _ = review
        reply = decide(client, submission_id=99, stage="interview")
        assert reply.status_code == 403
        assert calls["moved"] == []

    @pytest.mark.parametrize("stage", ["interview_2", "hired", "rejected"])
    def test_nobody_is_rewound_to_a_first_interview(self, review, stage):
        client, calls, current = review
        current["pipeline"] = {"stage": stage}
        reply = decide(client, submission_id=7, stage="interview")
        assert reply.status_code == 409
        assert calls["moved"] == []

    def test_rejection_still_works_alongside_it(self, review):
        client, calls, _ = review
        reply = decide(client, submission_id=7, stage="rejected")
        assert reply.status_code == 200
        assert [stage for stage, _ in calls["moved"]] == ["rejected"]
