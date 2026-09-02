"""
A live send is started, not awaited.

It used to run inside the HTTP request: N Brevo calls, minutes long, with the
recruiter's browser waiting. Any proxy in between gave up first and showed a
failure -- while the loop carried on, because a dropped client connection stops
nothing. "Failed" on screen and hundreds of emails delivered is the worst
pairing available, and the obvious reaction to it is to click send again.

These tests pin the three properties that fix depends on: the request returns
straight away, exactly one batch runs, and the run lock is held for the whole
job and released however it ends.
"""

import time

import pytest

from backend.web import app as web_app, server, views_dashboard


@pytest.fixture
def dashboard(monkeypatch):
    """The app with the session guard and the admin check stood down."""
    monkeypatch.setattr(web_app, "AUTH_ENABLED", False)
    monkeypatch.setattr(views_dashboard, "AUTH_ENABLED", False)
    monkeypatch.setattr(views_dashboard, "_require_admin", lambda: None)
    server.app.config["REVIEW_ONLY"] = False

    monkeypatch.setattr(views_dashboard, "_last_state", {
        "candidates": [
            {"email": "a@example.com", "portal_status": None, "name": "A"},
            {"email": "b@example.com", "portal_status": None, "name": "B"},
        ],
        "generated_at": "2026-08-25T00:00:00+00:00",
    })
    # The staleness rule is tested by its own path; here it must simply not
    # reject the run before it starts.
    monkeypatch.setattr(views_dashboard, "_state_age",
                        lambda state: views_dashboard.timedelta(0))
    return server.app.test_client()


def settle(client, job_id, timeout=10.0):
    """Poll until the job leaves 'running'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = client.get(f"/api/run/status/{job_id}").get_json()
        if snapshot["state"] != "running":
            return snapshot
        time.sleep(0.05)
    raise AssertionError("job never finished")


def lock_is_free() -> bool:
    if web_app._run_lock.acquire(blocking=False):
        web_app._run_lock.release()
        return True
    return False


class TestLiveSendIsAsynchronous:
    def test_returns_202_without_waiting_for_the_batch(self, dashboard, monkeypatch):
        started = []

        def slow_batch(candidates, **kwargs):
            started.append(True)
            time.sleep(1.0)
            return {"reminders_sent": 2, "errors": 0, "unsubscribed": 0}, []

        monkeypatch.setattr(views_dashboard, "send_batch", slow_batch)

        began = time.time()
        response = dashboard.post("/api/run", json={"mode": "live"})
        elapsed = time.time() - began

        assert response.status_code == 202
        assert elapsed < 0.5, "the request waited for the send -- that is the bug"
        body = response.get_json()
        assert body["state"] == "running"
        assert body["poll"].endswith(body["job"])

        settle(dashboard, body["job"])

    def test_the_batch_runs_exactly_once(self, dashboard, monkeypatch):
        runs = []
        monkeypatch.setattr(views_dashboard, "send_batch", lambda c, **k: (
            runs.append(True),
            ({"reminders_sent": 1, "errors": 0, "unsubscribed": 0}, []))[1])

        job = dashboard.post("/api/run", json={"mode": "live"}).get_json()["job"]
        settle(dashboard, job)
        assert len(runs) == 1

    def test_the_result_is_readable_afterwards(self, dashboard, monkeypatch):
        recorded = [{"email": "a@example.com", "portal_job_id": "31"}]
        monkeypatch.setattr(views_dashboard, "send_batch", lambda c, **k: (
            {"reminders_sent": 1, "errors": 0, "unsubscribed": 2}, recorded))

        job = dashboard.post("/api/run", json={"mode": "live"}).get_json()["job"]
        snapshot = settle(dashboard, job)

        assert snapshot["state"] == "done"
        assert snapshot["totals"]["reminders_sent"] == 1
        assert snapshot["recorded"] == recorded
        # The opt-out count has to reach the recruiter, or a batch that skipped
        # people looks identical to one that had nobody to skip.
        assert "unsubscribed" in snapshot["message"]

    def test_an_unknown_job_is_404(self, dashboard):
        assert dashboard.get("/api/run/status/nope").status_code == 404


class TestTheRunLock:
    def test_a_second_click_is_refused_while_a_send_is_in_flight(
            self, dashboard, monkeypatch):
        monkeypatch.setattr(views_dashboard, "send_batch", lambda c, **k: (
            time.sleep(0.6),
            ({"reminders_sent": 1, "errors": 0, "unsubscribed": 0}, []))[1])

        first = dashboard.post("/api/run", json={"mode": "live"})
        assert first.status_code == 202

        second = dashboard.post("/api/run", json={"mode": "live"})
        assert second.status_code == 409, "two concurrent sends were allowed"

        settle(dashboard, first.get_json()["job"])

    def test_released_when_the_job_succeeds(self, dashboard, monkeypatch):
        monkeypatch.setattr(views_dashboard, "send_batch", lambda c, **k: (
            {"reminders_sent": 1, "errors": 0, "unsubscribed": 0}, []))
        job = dashboard.post("/api/run", json={"mode": "live"}).get_json()["job"]
        settle(dashboard, job)
        assert lock_is_free(), "the worker kept the lock after finishing"

    def test_released_when_the_job_fails(self, dashboard, monkeypatch):
        def boom(candidates, **kwargs):
            raise RuntimeError("Brevo exploded")

        monkeypatch.setattr(views_dashboard, "send_batch", boom)
        job = dashboard.post("/api/run", json={"mode": "live"}).get_json()["job"]
        snapshot = settle(dashboard, job)

        assert snapshot["state"] == "failed"
        assert "Brevo exploded" in snapshot["error"]
        # A leaked lock here would wedge every future run behind a 409 with no
        # way back short of a restart.
        assert lock_is_free(), "the lock leaked after a failed run"


class TestOtherModesStaySynchronous:
    @pytest.mark.parametrize("mode", ["dry-run", "preview"])
    def test_answer_in_one_round_trip(self, dashboard, monkeypatch, mode):
        # Neither touches the network, so both finish in the time it takes to
        # format the text. Making the page poll for them would be ceremony.
        monkeypatch.setattr(views_dashboard, "send_batch", lambda c, **k: (
            {"reminders_sent": 0, "previewed": 2, "errors": 0,
             "unsubscribed": 0}, []))
        response = dashboard.post("/api/run", json={"mode": mode})
        assert response.status_code == 200
        assert "job" not in response.get_json()
        assert lock_is_free()

    def test_an_unknown_mode_is_rejected(self, dashboard):
        response = dashboard.post("/api/run", json={"mode": "delete-everything"})
        assert response.status_code == 400
        assert lock_is_free()
