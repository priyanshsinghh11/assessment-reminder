"""
Matching a candidate to their assignment -- the comparison that, when it goes
wrong, empties the started-set and mails an entire job.

The portal's job_id arrives as a CSV column, so it is always a string. The
value it is compared against comes from JOB_ASSESSMENTS. One unquoted number in
that 92-entry table makes every comparison False, which reads as "nobody has
started this assignment" and is acted on by sending mail to all of them.

These tests exist because that failure is completely silent: no error, a
successful scrape, and plausible-looking counts for a job nobody has begun.
"""

import pytest

from backend import config
from backend.scraping.portal_scraper import PortalRecord, get_portal_emails


def record(email, job_id, status="in_progress", **kw):
    return PortalRecord(email=email, name="Someone", job_id=job_id,
                        job_title="A role", status=status, **kw)


ROWS = [
    record("started@x.com", "31"),
    record("submitted@x.com", "31", status="submitted"),
    record("other-job@x.com", "33"),
]


class TestJobIdMatching:
    def test_filters_to_one_assignment(self):
        assert set(get_portal_emails(ROWS, "31")) == {
            "started@x.com", "submitted@x.com"}

    def test_no_filter_returns_everyone(self):
        assert len(get_portal_emails(ROWS, None)) == 3

    def test_an_integer_config_value_still_matches(self):
        # THE REGRESSION. Before str() normalisation this returned an empty
        # dict, and an empty started-set is what sends the mail.
        assert set(get_portal_emails(ROWS, 31)) == {
            "started@x.com", "submitted@x.com"}

    def test_an_integer_row_value_still_matches(self):
        # The other direction, for completeness: a portal that ever returned
        # parsed JSON instead of CSV would hand us ints.
        rows = [record("a@x.com", 31)]
        assert set(get_portal_emails(rows, "31")) == {"a@x.com"}

    def test_a_genuinely_different_job_is_still_excluded(self):
        # str() must not turn the comparison into something that matches
        # everything -- the filter still has to filter.
        assert "other-job@x.com" not in get_portal_emails(ROWS, "31")

    def test_unmatched_job_id_returns_empty(self):
        assert get_portal_emails(ROWS, "999") == {}


class TestStatusPrecedence:
    def test_submitted_beats_in_progress(self):
        rows = [record("a@x.com", "31", status="in_progress"),
                record("a@x.com", "31", status="submitted")]
        assert get_portal_emails(rows, "31")["a@x.com"] == "submitted"

    def test_order_does_not_change_the_answer(self):
        rows = [record("a@x.com", "31", status="submitted"),
                record("a@x.com", "31", status="in_progress")]
        assert get_portal_emails(rows, "31")["a@x.com"] == "submitted"

    def test_an_unknown_status_still_suppresses_the_reminder(self):
        # Presence in the export is what suppresses a reminder, whatever the
        # portal called the state. A status nobody has seen before must not
        # drop the candidate out of the set and get them mailed.
        rows = [record("a@x.com", "31", status="something_new")]
        assert "a@x.com" in get_portal_emails(rows, "31")


class TestConfigValidation:
    def test_the_real_table_passes(self):
        config._validate_job_assessments()

    def test_an_unquoted_job_id_is_refused_at_import(self, monkeypatch):
        table = dict(config.JOB_ASSESSMENTS)
        table["TESTCODE"] = ("A role", 31, "a-slug")
        monkeypatch.setattr(config, "JOB_ASSESSMENTS", table)
        with pytest.raises(TypeError) as caught:
            config._validate_job_assessments()
        # The message has to name the offender -- an assertion that says only
        # "wrong type" leaves someone grepping a 92-line table.
        assert "TESTCODE" in str(caught.value)

    def test_every_expanded_job_carries_a_string_id(self):
        assert all(isinstance(job["portal_job_id"], str)
                   for job in config.ASSESSMENT_JOBS.values())
