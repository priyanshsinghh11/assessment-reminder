"""
Refusing a portal export that arrived but is not the export.

H4. A 200 is not evidence the fetch worked. Two bodies parse cleanly and mean
disaster downstream:

  An expired session answers 200 with an HTML login page. csv.DictReader parses
  it into rows keyed by "<!DOCTYPE html>", none of which carry a
  candidate_email, so all of them are dropped later -- leaving a short or empty
  record set and no error anywhere.

  A review_status the portal has stopped recognising answers 200 with a
  well-formed CSV containing zero rows.

Both read downstream as "these candidates never started", and that is acted on
by sending them mail.
"""

import pytest

from backend.scraping import portal_scraper
from backend.scraping.portal_scraper import (
    PortalExportSuspect, _bucket_shrank, _parse_rows,
)


HEADER = "candidate_email,job_id,submission_status,submission_id"
GOOD = f"{HEADER}\na@x.com,31,in_progress,1\nb@x.com,31,submitted,2\n"

LOGIN_PAGE = (
    "<!DOCTYPE html>\n<html><head><title>Sign in</title></head>\n"
    "<body><form action='/admin/login' method='post'>"
    "<input name='email'><input name='password'></form></body></html>\n"
)


class TestExportShape:
    def test_a_real_export_parses(self):
        assert len(_parse_rows(GOOD, "test")) == 2

    def test_an_html_login_page_is_refused(self):
        # THE ONE THAT MATTERS. Without the header check this parses into rows
        # that are silently dropped, and the run continues on a short set.
        with pytest.raises(PortalExportSuspect):
            _parse_rows(LOGIN_PAGE, "review_status=new")

    def test_the_refusal_says_what_arrived(self):
        with pytest.raises(PortalExportSuspect) as caught:
            _parse_rows(LOGIN_PAGE, "review_status=new")
        message = str(caught.value)
        assert "review_status=new" in message
        assert "candidate_email" in message
        # A sample of the body, so the log says what came back rather than only
        # that something did.
        assert "DOCTYPE" in message

    def test_a_schema_that_moved_is_refused(self):
        with pytest.raises(PortalExportSuspect):
            _parse_rows("id,name,email\n1,Ada,a@x.com\n", "full export")

    def test_an_empty_body_is_refused(self):
        with pytest.raises(PortalExportSuspect):
            _parse_rows("", "review_status=new")

    def test_a_header_only_export_parses_but_is_empty(self):
        # Structurally valid, so the parser passes it. Catching this one is the
        # shrink check's job, not the parser's -- they are different failures
        # and conflating them would refuse a genuinely empty bucket for ever.
        assert _parse_rows(HEADER + "\n", "review_status=interview") == []

    def test_extra_columns_are_fine(self):
        rows = _parse_rows(f"{HEADER},screener_rating\na@x.com,31,submitted,1,4\n", "t")
        assert rows[0]["screener_rating"] == "4"


class TestBucketShrink:
    def test_a_bucket_that_emptied_is_broken(self):
        # The exact failure: "interview" held 83 rows and now returns none.
        assert _bucket_shrank("interview", 0, 83) is True

    def test_an_absolute_row_floor_would_not_have_caught_that(self):
        # 83 rows disappearing does not move a total-row threshold when the
        # export is ~8,600 rows. This is why the check is relative.
        from backend.core.config import PORTAL_MIN_TOTAL_ROWS
        assert 8600 - 83 > PORTAL_MIN_TOTAL_ROWS

    def test_normal_movement_between_buckets_is_fine(self):
        assert _bucket_shrank("interview", 80, 83) is False
        assert _bucket_shrank("new", 4400, 4464) is False

    def test_losing_more_than_the_tolerance_is_broken(self):
        assert _bucket_shrank("interview", 20, 83) is True

    def test_a_bucket_that_never_had_rows_is_fine(self):
        assert _bucket_shrank("interview", 0, 0) is False

    def test_the_first_ever_run_is_fine(self):
        # Nothing remembered yet, so there is nothing to have shrunk from.
        assert _bucket_shrank("new", 4464, 0) is False

    def test_growth_is_never_suspicious(self):
        assert _bucket_shrank("new", 9000, 4464) is False


class TestRememberedCounts:
    def test_an_unreadable_memory_skips_the_check_rather_than_failing(self, monkeypatch):
        # The shrink check is a safety net over the real fetch. Losing the
        # memory should cost the check, not the run -- refusing every fetch
        # because a settings document could not be read would be its own
        # outage.
        from backend.database import mongo_store as store

        def boom():
            raise RuntimeError("mongo down")

        monkeypatch.setattr(store, "get_db", boom)
        assert portal_scraper._remembered_bucket_counts() == {}

    def test_recording_counts_never_raises(self, monkeypatch):
        from backend.database import mongo_store as store

        def boom():
            raise RuntimeError("mongo down")

        monkeypatch.setattr(store, "get_db", boom)
        portal_scraper._remember_bucket_counts({"new": 10})   # must not raise
