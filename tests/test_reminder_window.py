"""
The date arithmetic that decides who gets chased, and when.

Every one of these is a decision to email a real person. business_days_since()
picks who is inside the window; should_send_reminder() decides whether they
have already had enough. An off-by-one in either is not a rounding error -- it
is somebody mailed a day early, or a second time, or not at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.utils import (
    business_days_ago,
    business_days_between,
    business_days_since,
    claim_reminder,
    release_reminder,
    should_send_reminder,
    state_key,
)


# 2026-08-10 is a Monday. Every date below is anchored to it so the weekday
# maths is readable rather than something you have to work out.
MON = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
TUE = MON + timedelta(days=1)
FRI = MON + timedelta(days=4)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)
NEXT_MON = MON + timedelta(days=7)


class TestBusinessDaysBetween:
    def test_same_day_is_zero(self):
        assert business_days_between(MON, MON) == 0

    def test_consecutive_weekdays(self):
        assert business_days_between(MON, TUE) == 1
        assert business_days_between(MON, FRI) == 4

    def test_the_weekend_does_not_count(self):
        # Friday to the following Monday is one business day, not three. This
        # is the whole reason the function exists: a candidate who applied on
        # Friday has not had three days to respond by Monday morning.
        assert business_days_between(FRI, NEXT_MON) == 1

    def test_saturday_and_sunday_contribute_nothing(self):
        assert business_days_between(FRI, SAT) == 0
        assert business_days_between(FRI, SUN) == 0

    def test_start_day_is_excluded_end_day_included(self):
        # Applying on Monday and asking on Tuesday is one day elapsed, not two.
        # Counting the application day itself would chase everyone a day early.
        assert business_days_between(MON, TUE) == 1

    def test_a_full_week_is_five(self):
        assert business_days_between(MON, NEXT_MON) == 5

    def test_backwards_range_is_zero_not_negative(self):
        assert business_days_between(TUE, MON) == 0

    def test_accepts_dates_as_well_as_datetimes(self):
        assert business_days_between(MON.date(), FRI.date()) == 4


class TestBusinessDaysAgo:
    def test_lands_on_a_weekday(self):
        for n in range(1, 12):
            assert business_days_ago(n).weekday() < 5

    def test_is_consistent_with_business_days_since(self):
        # The two are used together -- one bounds the Workable query, the other
        # makes the real decision -- so they must not disagree about what "3
        # business days ago" means. A day of slack is documented and expected;
        # more than that is a window that quietly changes size.
        for n in (1, 3, 5, 7, 10):
            elapsed = business_days_since(business_days_ago(n))
            assert abs(elapsed - n) <= 1, f"{n} business days ago -> {elapsed}"


class TestStateKey:
    def test_scoped_to_the_assignment_not_the_posting(self):
        # Fifteen marketing shortcodes can point at one portal assignment. The
        # key has to be the assignment, or a candidate who applied to five of
        # them gets five identical emails carrying the same link.
        assert state_key("a@x.com", "30") == state_key("a@x.com", "30")

    def test_different_assignments_are_different_keys(self):
        assert state_key("a@x.com", "30") != state_key("a@x.com", "31")

    def test_email_is_normalised(self):
        assert state_key("  Ada@X.COM ", "30") == state_key("ada@x.com", "30")

    def test_integer_and_string_group_do_not_collide_silently(self):
        # Documents today's behaviour rather than blessing it: the group is
        # interpolated, so 30 and "30" produce the SAME key. That is the
        # forgiving direction (no duplicate mail), and it is only safe because
        # config now refuses a non-string portal_job_id outright.
        assert state_key("a@x.com", 30) == state_key("a@x.com", "30")


class TestShouldSendReminder:
    """
    The advisory read. It answers for the dashboard, the dry run and the log
    line -- it is NOT what protects against a double send, because anything
    that checks and then acts has a gap in the middle. claim_reminder() is the
    gate; these pin what the page is told.
    """

    def test_unknown_candidate_is_eligible(self, reminder_store):
        assert should_send_reminder("new@x.com", "30") is True

    def test_not_again_immediately_after_a_send(self, reminder_store):
        claim_reminder("a@x.com", "30", "c1", "A", "SHORT1",
                       days_between_reminders=0)
        assert should_send_reminder("a@x.com", "30", days_between_reminders=2) is False

    def test_eligible_again_once_the_gap_has_passed(self, reminder_store, monkeypatch):
        claim_reminder("a@x.com", "30", "c1", "A", "SHORT1",
                       days_between_reminders=0)
        from backend.core import utils
        monkeypatch.setattr(utils, "business_days_since", lambda dt: 2)
        assert should_send_reminder("a@x.com", "30", days_between_reminders=2) is True

    def test_stops_at_the_maximum(self, reminder_store):
        claim_reminder("a@x.com", "30", "c1", "A", "SHORT1", days_between_reminders=0)
        claim_reminder("a@x.com", "30", "c1", "A", "SHORT1", days_between_reminders=0)
        # Two is MAX_REMINDERS_PER_CANDIDATE. However long anyone waits, there
        # is no third email.
        assert should_send_reminder("a@x.com", "30") is False

    def test_an_explicitly_suppressed_record_is_never_eligible(self, reminder_store):
        # The 192 records from the 2026-08-10 incident. They used to rely
        # entirely on a hand-edited counter sitting at the cap; the migration
        # promoted that to a real flag, so the block no longer depends on a
        # number that looks like corruption to whoever reads it next.
        reminder_store.documents[state_key("blocked@x.com", "33")] = {
            "_id": state_key("blocked@x.com", "33"),
            "reminders_sent": 0,
            "suppressed": True,
            "blocked_reason": "2026-08-10 incident",
            "last_reminder_at": None,
        }
        assert should_send_reminder("blocked@x.com", "33") is False

    def test_one_assignment_does_not_suppress_another(self, reminder_store):
        claim_reminder("a@x.com", "30", "c1", "A", "SHORT1", days_between_reminders=0)
        assert should_send_reminder("a@x.com", "31") is True


class TestClaiming:
    """The gate. One operation, so a race cannot produce two emails."""

    def test_returns_the_reminder_number(self, reminder_store):
        assert claim_reminder("a@x.com", "30", "c1", "A", "S",
                              days_between_reminders=0) == 1
        assert claim_reminder("a@x.com", "30", "c1", "A", "S",
                              days_between_reminders=0) == 2

    def test_refuses_past_the_cap(self, reminder_store):
        for _ in range(2):
            claim_reminder("a@x.com", "30", "c1", "A", "S", days_between_reminders=0)
        assert claim_reminder("a@x.com", "30", "c1", "A", "S",
                              days_between_reminders=0) is None

    def test_records_the_first_send_in_full(self, reminder_store):
        claim_reminder("a@x.com", "30", "c1", "Ada", "SHORT1",
                       days_between_reminders=0)
        record = reminder_store.documents[state_key("a@x.com", "30")]
        assert record["reminders_sent"] == 1
        assert record["candidate_name"] == "Ada"
        assert record["first_reminder_at"] == record["last_reminder_at"]
        assert record["suppressed"] is False

    def test_the_second_claim_moves_only_the_last_stamp(self, reminder_store):
        claim_reminder("a@x.com", "30", "c1", "Ada", "S", days_between_reminders=0)
        key = state_key("a@x.com", "30")
        first = dict(reminder_store.documents[key])
        claim_reminder("a@x.com", "30", "c1", "Ada", "S", days_between_reminders=0)
        second = reminder_store.documents[key]

        assert second["reminders_sent"] == 2
        # first_reminder_at is history and must never move...
        assert second["first_reminder_at"] == first["first_reminder_at"]
        # ...and last_reminder_at must, which is what tells a real second send
        # apart from a hand-edited suppression. The 192 incident records sit at
        # reminders_sent=2 with the two stamps still equal; that is how you can
        # tell nobody was actually mailed twice.
        assert second["last_reminder_at"] >= first["last_reminder_at"]

    def test_release_gives_a_failed_send_back(self, reminder_store):
        number = claim_reminder("a@x.com", "30", "c1", "A", "S",
                                days_between_reminders=0)
        assert release_reminder("a@x.com", "30", number) is True
        assert reminder_store.documents[state_key("a@x.com", "30")]["reminders_sent"] == 0
