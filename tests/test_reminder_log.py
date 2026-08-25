"""
The reminder dedupe log: atomic claims, and legacy records that used to abort runs.

Two findings meet here.

B2 was the unlocked JSON file -- read whole, mutated, written back over a
truncated handle. Sixty concurrent writes left one survivor, and every lost
record is a candidate the next run mails again. The claim below is what
replaced it; what these tests pin is that it decides and records in ONE
operation, so a race produces one email rather than two.

H5 was `record["last_reminder_at"]` raising KeyError on a record written before
that field existed -- aborting the entire run, for every other candidate in it,
because of one malformed row.

These use a fake collection rather than a live Mongo, so they run anywhere. The
compare-and-swap semantics are modelled faithfully enough to catch a filter
that stops being conditional; the real thing is exercised against Mongo by
hand, and the 60-way concurrency proof is in the commit message for B2.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.database import reminder_log


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=30)


@pytest.fixture
def collection(reminder_store):
    return reminder_store


def always_ok(_previous):
    return True


def never_ok(_previous):
    return False


def claim(**kwargs):
    defaults = dict(
        key="ada@x.com::31", email="ada@x.com", assessment_group="31",
        candidate_id="c1", candidate_name="Ada", job_shortcode="SHORT",
        max_reminders=2, gap_satisfied=always_ok,
    )
    defaults.update(kwargs)
    return reminder_log.claim(**defaults)


class TestClaiming:
    def test_first_claim_creates_the_record(self, collection):
        assert claim() == 1
        assert collection.documents["ada@x.com::31"]["reminders_sent"] == 1

    def test_second_claim_increments(self, collection):
        assert claim() == 1
        assert claim() == 2

    def test_stops_at_the_cap(self, collection):
        claim()
        claim()
        assert claim() is None

    def test_refuses_when_the_gap_has_not_passed(self, collection):
        claim()
        assert claim(gap_satisfied=never_ok) is None

    def test_a_suppressed_record_is_never_claimable(self, collection):
        collection.documents["blocked@x.com::33"] = {
            "_id": "blocked@x.com::33", "reminders_sent": 0,
            "suppressed": True, "blocked_reason": "2026-08-10 incident",
            "last_reminder_at": LONG_AGO,
        }
        # Count is zero and the gap is satisfied -- everything else says send.
        # The flag is the only thing refusing, which is the point of having it.
        assert claim(key="blocked@x.com::33") is None


class TestTheCompareAndSwap:
    def test_a_concurrent_claim_makes_this_one_fail(self, collection, monkeypatch):
        claim()

        original = collection.update_one

        def racing_update(query, update, upsert=False):
            # Stand in for another process claiming between our read and our
            # write: it moves last_reminder_at, so our filter matches nothing.
            document = collection.documents.get(query["_id"])
            if document and "$inc" in update:
                document["last_reminder_at"] = NOW + timedelta(seconds=1)
            return original(query, update, upsert)

        monkeypatch.setattr(collection, "update_one", racing_update)
        assert claim() is None

    def test_a_lost_insert_race_returns_none(self, collection, monkeypatch):
        from tests.conftest import FakeResult

        def already_there(query, update, upsert=False):
            return FakeResult()         # upserted_id None: somebody else won

        monkeypatch.setattr(collection, "update_one", already_there)
        assert claim() is None

    def test_two_sequential_claims_never_reuse_a_number(self, collection):
        assert [claim(), claim()] == [1, 2]


class TestLegacyRecords:
    """H5: a record written before last_reminder_at existed."""

    def test_a_missing_timestamp_does_not_raise(self, collection):
        collection.documents["old@x.com::31"] = {
            "_id": "old@x.com::31", "reminders_sent": 1,
            "first_reminder_at": LONG_AGO,
        }
        # Used to be a KeyError that aborted the run for everyone in it.
        assert claim(key="old@x.com::31") == 2

    def test_falls_back_to_first_reminder_at(self, collection):
        record = {"_id": "k", "reminders_sent": 1, "first_reminder_at": LONG_AGO}
        assert reminder_log.last_reminder_at(record) == LONG_AGO

    def test_no_timestamp_at_all_refuses_rather_than_raising(self, collection):
        collection.documents["bare@x.com::31"] = {
            "_id": "bare@x.com::31", "reminders_sent": 1,
        }
        # Unknown is not the same as eligible. Refusing is the safe direction,
        # and it is logged rather than silent.
        assert claim(key="bare@x.com::31") is None

    def test_a_record_with_no_count_field_is_treated_as_zero(self):
        assert reminder_log.reminders_sent({"_id": "k"}) == 0

    def test_a_non_numeric_count_does_not_crash_the_run(self):
        # Treated as over the cap -- refuse this one candidate rather than
        # raise and take the whole batch down.
        assert reminder_log.reminders_sent({"_id": "k", "reminders_sent": "two"}) > 2

    def test_an_iso_string_timestamp_is_understood(self):
        # Migrated records hold ISO strings, because that is what the JSON file
        # left behind. Rewriting them all would have been a second chance to
        # lose one.
        record = {"_id": "k", "last_reminder_at": "2026-08-10T13:47:27+00:00"}
        assert reminder_log.last_reminder_at(record).year == 2026

    def test_a_naive_timestamp_is_read_as_utc(self):
        record = {"_id": "k", "last_reminder_at": datetime(2026, 8, 10, 13, 47)}
        assert reminder_log.last_reminder_at(record).tzinfo is not None

    def test_an_unparseable_timestamp_is_none_not_an_exception(self):
        assert reminder_log.last_reminder_at({"_id": "k",
                                              "last_reminder_at": "yesterday"}) is None


class TestRelease:
    def test_gives_a_claim_back(self, collection):
        assert claim() == 1
        assert reminder_log.release("ada@x.com::31", 1) is True
        assert collection.documents["ada@x.com::31"]["reminders_sent"] == 0

    def test_will_not_undo_a_later_claim(self, collection):
        claim()
        claim()
        # A release for reminder 1 must not touch a record that has since
        # reached 2, or it would silently hand out a third send.
        assert reminder_log.release("ada@x.com::31", 1) is False
        assert collection.documents["ada@x.com::31"]["reminders_sent"] == 2


class TestOutagesFailClosed:
    def test_claim_raises_rather_than_returning_none(self, monkeypatch):
        # Patched at the store, so the real _collection() wrapper is the thing
        # under test -- patching _collection itself would skip the translation
        # from a driver error into ReminderLogUnavailable.
        from backend.database import mongo_store as store

        def boom():
            raise RuntimeError("mongo is down")

        monkeypatch.setattr(store, "get_db", boom)
        # None means "already reminded". An outage must not be reported as
        # that, or a caller treats an unreachable database as permission.
        with pytest.raises(reminder_log.ReminderLogUnavailable):
            claim()

    def test_should_send_reminder_reports_not_eligible(self, monkeypatch):
        from backend.core import utils

        def boom(_key):
            raise reminder_log.ReminderLogUnavailable("down")

        monkeypatch.setattr(reminder_log, "get", boom)
        assert utils.should_send_reminder("a@x.com", "31") is False
