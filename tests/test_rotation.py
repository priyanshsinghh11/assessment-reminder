"""
Which role an hourly grading run spends its budget on.

`manage.py grade --next` grades ONE role per run, and _next_role picks it. The
rule has no state of its own -- it reads the grading timestamps that grading
itself writes -- so the only thing standing between it and a run that grades
the same role twenty-four times a day is this file.

No database: the queries live in store, the rule takes their output as plain
dicts, and that split is what makes these tests possible.
"""

from datetime import datetime

from backend.pipeline.grade import _next_role


def role(job_id: int, title: str = "") -> dict:
    return {"_id": job_id, "title": title or f"Role {job_id}", "published": True}


def at(day: int, hour: int = 0) -> datetime:
    # Naive UTC, which is what Mongo hands back -- see last_graded_by_role.
    return datetime(2026, 9, day, hour)


ROLES = [role(1, "AI Delivery"), role(2, "AI Solution Architect"), role(3, "Data")]


class TestItTakesThemInTurn:
    def test_the_least_recently_graded_goes_first(self):
        pending = {1: 50, 2: 50, 3: 50}
        last = {1: at(24, 9), 2: at(24, 7), 3: at(24, 8)}
        assert _next_role(ROLES, pending, last)["_id"] == 2

    def test_grading_a_role_sends_it_to_the_back(self):
        """
        The rotation in one test. Grading role 2 stamps it `now`, which is the
        newest timestamp, so the next run must not pick it again.
        """
        pending = {1: 50, 2: 50, 3: 50}
        last = {1: at(24, 9), 2: at(24, 7), 3: at(24, 8)}

        first = _next_role(ROLES, pending, last)
        assert first["_id"] == 2

        last[first["_id"]] = at(24, 10)          # it has just been graded
        second = _next_role(ROLES, pending, last)
        assert second["_id"] == 3                 # not 2 again

        last[second["_id"]] = at(24, 11)
        assert _next_role(ROLES, pending, last)["_id"] == 1

    def test_a_full_cycle_visits_every_role_once(self):
        pending = {1: 500, 2: 500, 3: 500}
        last = {1: at(24, 9), 2: at(24, 7), 3: at(24, 8)}
        seen = []
        for hour in range(len(ROLES)):
            chosen = _next_role(ROLES, pending, last)
            seen.append(chosen["_id"])
            last[chosen["_id"]] = at(25, hour)
        assert sorted(seen) == [1, 2, 3], f"a role was skipped or repeated: {seen}"


class TestAnUngradedRoleGoesFirst:
    def test_never_graded_beats_any_timestamp(self):
        # A newly published role must not wait a full cycle to be looked at.
        pending = {1: 5, 2: 5, 3: 5}
        last = {1: at(20), 3: at(21)}             # role 2 has never been graded
        assert _next_role(ROLES, pending, last)["_id"] == 2

    def test_several_never_graded_are_ordered_by_job_id(self):
        # Deterministic, so two runs cannot disagree about whose turn it is.
        pending = {1: 5, 2: 5, 3: 5}
        assert _next_role(ROLES, pending, {})["_id"] == 1

    def test_no_timestamp_is_never_compared_to_one(self):
        # The bug this guards: a sort key mixing None and datetime raises
        # TypeError, and it would only do so once a role had been graded --
        # never on a fresh database, so never where it would be noticed.
        pending = {1: 1, 2: 1, 3: 1}
        _next_role(ROLES, pending, {2: at(24)})   # must not raise


class TestItSkipsEmptyQueues:
    def test_a_role_with_nothing_waiting_is_not_chosen(self):
        # THE ONE THAT COSTS REAL MONEY IF IT REGRESSES: picking an empty role
        # burns the hour and grades nobody, while another role has a backlog.
        pending = {2: 12}
        last = {1: at(1), 2: at(24), 3: at(2)}    # role 1 is much staler
        assert _next_role(ROLES, pending, last)["_id"] == 2

    def test_zero_counts_as_empty(self):
        pending = {1: 0, 2: 3}
        last = {1: at(1), 2: at(24)}
        assert _next_role(ROLES, pending, last)["_id"] == 2

    def test_nothing_waiting_anywhere_returns_none(self):
        # The caller prints "nothing pending" and exits 0. A run with an empty
        # queue is a success, not a failure to escalate.
        assert _next_role(ROLES, {}, {1: at(24)}) is None

    def test_no_roles_at_all_returns_none(self):
        assert _next_role([], {}, {}) is None

    def test_an_unpublished_role_is_the_callers_problem(self):
        # _next_role does not filter on `published` -- main() passes only
        # published roles. Pinned so that contract is not quietly moved here.
        only = [role(9)]
        assert _next_role(only, {9: 1}, {})["_id"] == 9
