"""
Reconstructing a past mail-out from the per-candidate mail log.

WHY THIS IS RECONSTRUCTED RATHER THAN RECORDED. A recruiter mails a role's top
twenty their interview invitation. The send moves all twenty into `interview`,
which takes them out of `top_candidates` -- so the Shortlist tab's download now
builds a sheet of the NEXT twenty people, and the list that actually went out
exists nowhere as a list. It does exist as twenty `pipeline.emails` entries
written seconds apart, and that is what these pin: the clustering that turns
those entries back into "the twenty invited on the 7th", and the rule that a
second sitting at the dashboard is a second batch rather than the same one.

No database. `store.get_db` is the seam, as everywhere else in this suite.
"""

import io
from datetime import datetime, timedelta, timezone

import pytest

from backend.db import store
from backend.mail import shortlist


DAY = datetime(2026, 9, 7, 14, 25, tzinfo=timezone.utc)


def mail(at, stage="interview", ok=True):
    return {"stage": stage, "at": at, "to": "x@example.com",
            "subject": "s", "ok": ok}


def sub(sid, job_id=17, score=90.0, emails=(), name="Someone"):
    return {
        "_id": sid, "job_id": job_id, "candidate_name": name,
        "candidate_email": f"{sid}@example.com",
        "resume_link": f"https://cv.example/{sid}",
        "admin_url": f"https://portal.example/{sid}",
        "video_link": f"https://video.example/{sid}",
        "submitted_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
        "evaluation": {"score": score},
        "pipeline": {"stage": "interview", "emails": list(emails)},
    }


class FakeSubmissions:
    """Enough of a collection for these queries: job_id in or out, that is all."""

    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, projection=None):
        query = query or {}
        job = query.get("job_id")
        wanted = query.get("_id")
        ids = wanted.get("$in") if isinstance(wanted, dict) else None
        out = []
        for doc in self.docs:
            if isinstance(job, dict) and doc["job_id"] not in job["$in"]:
                continue
            if isinstance(job, int) and doc["job_id"] != job:
                continue
            if ids is not None and doc["_id"] not in ids:
                continue
            out.append(doc)
        return out


@pytest.fixture
def db(monkeypatch):
    def install(docs):
        fake = type("DB", (), {"submissions": FakeSubmissions(docs)})()
        monkeypatch.setattr(store, "get_db", lambda: fake)
        return fake
    return install


class TestBatching:
    def test_one_click_is_one_batch(self, db):
        # The send loop writes a row per candidate, seconds apart.
        db([sub(i, emails=[mail(DAY + timedelta(seconds=i * 3))])
            for i in range(1, 21)])
        batches = store.stage_send_batches(17, "interview")
        assert [b["count"] for b in batches] == [20]
        assert batches[0]["at"] == DAY + timedelta(seconds=3)

    def test_a_later_sitting_is_a_second_batch(self, db):
        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + timedelta(seconds=4))]),
            sub(3, emails=[mail(DAY + timedelta(days=13))])])
        batches = store.stage_send_batches(17, "interview")
        # Newest first, because that is the one being asked for.
        assert [b["count"] for b in batches] == [1, 2]
        assert batches[0]["at"] > batches[1]["at"]

    def test_the_gap_is_the_only_thing_that_splits_them(self, db):
        just_inside = store.STAGE_BATCH_GAP - timedelta(seconds=1)
        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + just_inside)])])
        assert [b["count"] for b in store.stage_send_batches(17, "interview")] == [2]

        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + store.STAGE_BATCH_GAP
                                + timedelta(seconds=1))])])
        assert [b["count"] for b in store.stage_send_batches(17, "interview")] == [1, 1]

    def test_a_failed_send_is_not_in_the_batch(self, db):
        # The candidate is still waiting to hear. They were not mailed, so they
        # are not on the sheet of who was.
        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + timedelta(seconds=2), ok=False)])])
        batch = store.stage_send_batches(17, "interview")[0]
        assert batch["submission_ids"] == [1]

    def test_another_stage_is_another_list(self, db):
        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY, stage="rejected")])])
        assert store.stage_send_batches(17, "interview")[0]["count"] == 1
        assert store.stage_send_batches(17, "rejected")[0]["count"] == 1

    def test_a_resend_puts_the_candidate_in_both_batches(self, db):
        # Rescheduling is a real second mail-out, and the sheet for each send
        # should say who that send went to.
        db([sub(1, emails=[mail(DAY), mail(DAY + timedelta(days=2))])])
        assert [b["submission_ids"] for b in
                store.stage_send_batches(17, "interview")] == [[1], [1]]

    def test_a_role_filter_is_a_role_filter(self, db):
        db([sub(1, job_id=17, emails=[mail(DAY)]),
            sub(2, job_id=28, emails=[mail(DAY + timedelta(seconds=2))])])
        assert store.stage_send_batches(17, "interview")[0]["submission_ids"] == [1]
        # A manager's scope narrows it the same way an explicit role does.
        scoped = store.stage_send_batches(None, "interview", job_ids={28})
        assert scoped[0]["submission_ids"] == [2]


class TestOneBatch:
    def test_the_key_survives_a_newer_send_landing_in_front(self, db):
        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + timedelta(days=1))])])
        key = store.stage_send_batches(17, "interview")[1]["key"]

        db([sub(1, emails=[mail(DAY)]),
            sub(2, emails=[mail(DAY + timedelta(days=1))]),
            sub(3, emails=[mail(DAY + timedelta(days=5))])])
        assert store.stage_send_batch(key, 17)["submission_ids"] == [1]

    def test_the_key_is_url_safe(self, db):
        db([sub(1, emails=[mail(DAY)])])
        key = store.stage_send_batches(17, "interview")[0]["key"]
        # An ISO timestamp's "+00:00" decodes as a space through any encoder
        # that treats + as a literal, which is every query string.
        assert key.isalnum() and key == "20260907T142500"

    def test_an_unknown_key_is_none_not_a_guess(self, db):
        db([sub(1, emails=[mail(DAY)])])
        assert store.stage_send_batch("20200101T000000", 17) is None

    def test_rows_come_back_by_score(self, db):
        db([sub(1, score=80.0, emails=[mail(DAY)]),
            sub(2, score=95.0, emails=[mail(DAY + timedelta(seconds=2))]),
            sub(3, score=90.0, emails=[mail(DAY + timedelta(seconds=4))])])
        key = store.stage_send_batches(17, "interview")[0]["key"]
        batch = store.stage_send_batch(key, 17)
        # The order the list was chosen in, not the order the mail loop wrote.
        assert [s["_id"] for s in batch["submissions"]] == [2, 3, 1]


class TestTheSheet:
    def _batch(self, db):
        db([sub(i, score=100.0 - i, emails=[mail(DAY + timedelta(seconds=i))])
            for i in range(1, 4)])
        key = store.stage_send_batches(17, "interview")[0]["key"]
        return store.stage_send_batch(key, 17)

    def test_it_carries_the_three_links_the_csv_never_had(self, db):
        batch = self._batch(db)
        rows = shortlist.pipeline_rows(batch["submissions"], include_scores=True,
                                       emailed=shortlist.batch_emailed(batch))
        assert len(rows) == 3
        for row in rows:
            assert row["resume_link"] and row["assessment_url"] and row["video_link"]
            assert row["email"] and row["name"]
            assert row["emailed_at"] == "07 Sep 2026"

    def test_the_score_is_the_callers_to_ask_for(self, db):
        subs = self._batch(db)["submissions"]
        assert "score" in shortlist.pipeline_rows(subs, include_scores=True)[0]
        # A hiring-manager account never gets it -- the same rule the shortlist
        # enforces, reached from the board instead.
        assert "score" not in shortlist.pipeline_rows(subs, include_scores=False)[0]

    def test_it_builds_and_says_what_it_is(self, db):
        openpyxl = pytest.importorskip("openpyxl")
        batch = self._batch(db)
        role = {"title": "AI-Native Full Stack Developer", "slug": "fsd"}
        rows = shortlist.pipeline_rows(batch["submissions"], include_scores=True,
                                       emailed=shortlist.batch_emailed(batch))
        blob = shortlist.build_xlsx(role, rows, heading="19 invited, 07 Sep 2026",
                                    caption="Who this send went to.")

        sheet = openpyxl.load_workbook(io.BytesIO(blob)).active
        # A file that mislabels itself is worse than no file when it is opened
        # again in three weeks.
        assert sheet["A1"].value == "19 invited, 07 Sep 2026"
        headers = [cell.value for cell in sheet[4]]
        assert headers[0] == "#"
        for label in ("Candidate", "Email", "AI score", "Resume", "Assessment",
                      "Video", "Emailed"):
            assert label in headers
        assert sheet.max_row == 4 + 3

    def test_the_filename_names_the_send(self, db):
        batch = self._batch(db)
        name = shortlist.batch_filename({"slug": "full-stack-developer"}, batch)
        assert name == "interview-full-stack-developer-2026-09-07.xlsx"
