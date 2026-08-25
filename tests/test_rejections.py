"""
The bulk rejection: reading a pasted list, writing the message, and deciding
who is actually mailed.

THE FAILURE THIS SUITE EXISTS TO CATCH is sending somebody a second rejection.
It is unrecoverable and it is invisible from our side -- nothing bounces, no
count is wrong, the candidate simply reads "we have decided not to proceed" for
the second time about the same application. Every test below about `plan()` and
`send_bulk()` is really about that one thing.

The other half is the parser, which is tested hard because of what it is fed:
not a clean list of addresses but whatever came out of the last window the
recruiter had open -- a BCC field, a spreadsheet column, a Workable export,
usually all three stuck together. A parser that silently drops a line is a
candidate who never hears from us at all.

All of it pure: no MongoDB, no Brevo, no network. The two things that would
reach outside -- the opt-out list and the ledger -- are the seams every test
here patches, which is the same seam the real code reads them through.
"""

import pytest

from backend.notifications import rejections


# ---------------------------------------------------------------------------
# Reading a pasted list
# ---------------------------------------------------------------------------

class TestParsingWhatGetsPasted:
    def test_one_address_per_line(self):
        entries, bad = rejections.parse_recipients(
            "asha@example.com\nravi@example.io\n")
        assert [e["email"] for e in entries] == ["asha@example.com",
                                                 "ravi@example.io"]
        assert bad == []

    def test_a_bcc_field(self):
        # Semicolons, angle brackets and names -- what Outlook and Gmail both
        # put on the clipboard when you copy a BCC line.
        entries, _ = rejections.parse_recipients(
            "Asha Menon <asha@example.com>; ravi@example.io; "
            "Priya Nair <priya@example.co.in>")
        assert [(e["name"], e["email"]) for e in entries] == [
            ("Asha Menon", "asha@example.com"),
            ("", "ravi@example.io"),
            ("Priya Nair", "priya@example.co.in"),
        ]

    def test_a_spreadsheet_column_with_names(self):
        entries, _ = rejections.parse_recipients(
            "Asha Menon,asha@example.com\nRavi K,ravi@example.io")
        assert [(e["name"], e["email"]) for e in entries] == [
            ("Asha Menon", "asha@example.com"),
            ("Ravi K", "ravi@example.io"),
        ]

    def test_a_comma_inside_a_name_is_not_a_separator(self):
        # "Menon, Asha <asha@...>" is ONE person, not a stray word and a
        # nameless address. The line holds one address, so it is one record.
        entries, _ = rejections.parse_recipients("Menon, Asha <asha@example.com>")
        assert len(entries) == 1
        assert entries[0]["email"] == "asha@example.com"
        assert "Asha" in entries[0]["name"]

    def test_commas_do_separate_when_the_line_holds_several_addresses(self):
        entries, _ = rejections.parse_recipients("bob@z.dev, carol@z.dev")
        assert [e["email"] for e in entries] == ["bob@z.dev", "carol@z.dev"]

    def test_tabs_separate_too(self):
        entries, _ = rejections.parse_recipients("bob@z.dev\tcarol@z.dev")
        assert len(entries) == 2

    def test_addresses_are_lower_cased(self):
        entries, _ = rejections.parse_recipients("Asha.Menon@Example.COM")
        assert entries[0]["email"] == "asha.menon@example.com"

    def test_duplicates_collapse_to_one(self):
        # The whole point of the ledger is one rejection per person, and a
        # pasted list that names somebody twice must not mail them twice.
        entries, _ = rejections.parse_recipients(
            "asha@example.com\nASHA@example.com\nasha@example.com")
        assert len(entries) == 1

    def test_a_name_seen_later_is_kept_over_a_bare_repeat(self):
        entries, _ = rejections.parse_recipients(
            "asha@example.com\nAsha Menon <asha@example.com>")
        assert entries[0]["name"] == "Asha Menon"

    def test_lines_with_no_address_are_handed_back_not_dropped(self):
        # A mistyped address is a person who never hears from us, and that is
        # invisible unless the parser says so.
        entries, bad = rejections.parse_recipients(
            "asha@example.com\nravi.example.io\n")
        assert len(entries) == 1
        assert bad == ["ravi.example.io"]

    def test_blank_input_is_empty_not_an_error(self):
        assert rejections.parse_recipients("") == ([], [])
        assert rejections.parse_recipients("   \n\n  ") == ([], [])

    def test_plus_addressing_survives(self):
        entries, _ = rejections.parse_recipients("asha+jobs@example.com")
        assert entries[0]["email"] == "asha+jobs@example.com"

    def test_a_second_address_in_a_record_is_not_read_as_a_name(self):
        entries, _ = rejections.parse_recipients("asha@example.com reply@ajaia.ai")
        assert all(e["name"] == "" for e in entries)


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------

class TestTheMessage:
    def test_the_default_copy_is_used_when_nothing_is_written(self):
        email = rejections.build_email("Asha Menon")
        assert email["subject"] == rejections.DEFAULT_SUBJECT
        assert "Thank you for applying" in email["text"]

    def test_placeholders_are_filled(self):
        email = rejections.build_email(
            "Asha Menon", subject="About {role}",
            message="Hi {first_name}, about {name} and {role}.",
            role_title="AI Strategist")
        assert email["subject"] == "About AI Strategist"
        assert "Hi Asha, about Asha Menon and AI Strategist." in email["text"]

    def test_an_unknown_placeholder_is_left_as_typed(self):
        # It comes back to us as a visible mistake rather than going out as a
        # silently empty sentence.
        email = rejections.build_email("Asha", message="Your {salary} was fine.")
        assert "{salary}" in email["text"]

    def test_a_nameless_candidate_is_greeted_not_left_blank(self):
        email = rejections.build_email("", message="Hi {first_name},")
        assert "Hi there," in email["text"]

    def test_free_text_is_escaped_into_the_html(self):
        # The message is typed by a person outside the codebase. A stray angle
        # bracket must arrive as an angle bracket, not as broken layout.
        email = rejections.build_email("Asha", message="a < b & c > d")
        assert "&lt; b &amp; c &gt;" in email["html"]
        assert "a < b & c > d" in email["text"]

    def test_a_name_cannot_inject_markup(self):
        email = rejections.build_email(
            "<script>x</script>", message="Hi {name},")
        assert "<script>" not in email["html"]

    def test_the_signature_is_the_hiring_team_not_a_person(self, fixed_secret):
        # A rejection is the company's decision. Whoever clicked Send is not
        # who the candidate should be arguing with.
        email = rejections.build_email("Asha", to_email="asha@example.com")
        assert "Ajaia Hiring Team" in email["html"]

    def test_an_unsubscribe_link_is_added_for_a_real_recipient(self, fixed_secret):
        email = rejections.build_email("Asha", to_email="asha@example.com")
        assert "/unsubscribe/" in email["html"]
        assert "/unsubscribe/" in email["text"]

    def test_no_link_is_invented_for_a_preview_with_no_recipient(self):
        email = rejections.build_email("Asha")
        assert "/unsubscribe/" not in email["html"]

    def test_the_shell_is_appended_whatever_was_written(self, fixed_secret):
        # The recruiter's words are the top of the mail and nothing else --
        # they cannot delete the header, the sign-off or the way out.
        email = rejections.build_email("Asha", message="No.",
                                       to_email="asha@example.com")
        assert "Ajaia Hiring Team" in email["html"]
        assert "Unsubscribe" in email["html"]


# ---------------------------------------------------------------------------
# Who is actually mailed
# ---------------------------------------------------------------------------

@pytest.fixture
def seams(monkeypatch):
    """
    The two lookups that decide a send, under the test's control.

    Patched on `rejections` where they are read from rather than on their own
    modules, which is the same seam the real code goes through -- and means a
    test can never accidentally reach a real database.
    """
    box = {"opted_out": set(), "already": set(), "recorded": [], "sent": []}

    monkeypatch.setattr(rejections.unsubscribe, "suppressed_among",
                        lambda emails: set(box["opted_out"]))
    monkeypatch.setattr(rejections.unsubscribe, "mail_headers", lambda email: {})
    monkeypatch.setattr(rejections.unsubscribe, "unsubscribe_url",
                        lambda email: f"https://x/unsubscribe/{email}")
    monkeypatch.setattr(rejections.store, "already_rejected",
                        lambda emails: set(box["already"]))
    monkeypatch.setattr(rejections.store, "record_rejection",
                        lambda email, **kw: box["recorded"].append((email, kw)))
    monkeypatch.setattr(rejections.brevo_client, "send_email",
                        lambda **kw: box["sent"].append(kw))
    # Three minutes of real sleeping is not a unit test.
    monkeypatch.setattr(rejections, "REJECTION_SEND_DELAY", 0)
    return box


THREE = [{"email": "a@x.com", "name": "A"},
         {"email": "b@x.com", "name": "B"},
         {"email": "c@x.com", "name": "C"}]


class TestPlan:
    def test_everyone_new_is_mailable(self, seams):
        decided = rejections.plan(THREE)
        assert len(decided["mailable"]) == 3
        assert decided["already"] == 0
        assert decided["unsubscribed"] == 0

    def test_somebody_already_told_is_held_back(self, seams):
        seams["already"] = {"b@x.com"}
        decided = rejections.plan(THREE)
        assert [e["email"] for e in decided["mailable"]] == ["a@x.com", "c@x.com"]
        assert decided["already"] == 1

    def test_somebody_who_opted_out_is_held_back(self, seams):
        seams["opted_out"] = {"c@x.com"}
        decided = rejections.plan(THREE)
        assert [e["email"] for e in decided["mailable"]] == ["a@x.com", "b@x.com"]
        assert decided["unsubscribed"] == 1

    def test_opting_out_wins_over_having_been_told(self, seams):
        # The order matters for what the recruiter is shown: "asked not to be
        # emailed" is a different fact from "already heard from us", and the
        # first is the one that governs.
        seams["opted_out"] = {"a@x.com"}
        seams["already"] = {"a@x.com"}
        decided = rejections.plan(THREE)
        assert decided["unsubscribed"] == 1
        assert decided["already"] == 0

    def test_resend_reopens_the_ledger_but_never_the_opt_out(self, seams):
        seams["already"] = {"a@x.com"}
        seams["opted_out"] = {"b@x.com"}
        decided = rejections.plan(THREE, resend=True)
        assert [e["email"] for e in decided["mailable"]] == ["a@x.com", "c@x.com"]
        assert decided["unsubscribed"] == 1


class TestSendBulk:
    def test_mails_everyone_new_once(self, seams):
        totals = rejections.send_bulk(THREE)
        assert totals["sent"] == 3
        assert len(seams["sent"]) == 3
        assert [m["to"][0]["email"] for m in seams["sent"]] == [
            "a@x.com", "b@x.com", "c@x.com"]

    def test_one_message_per_person_never_a_bcc(self, seams):
        rejections.send_bulk(THREE)
        assert all(len(m["to"]) == 1 for m in seams["sent"])

    def test_nobody_already_told_is_mailed_again(self, seams):
        seams["already"] = {"b@x.com"}
        totals = rejections.send_bulk(THREE)
        assert "b@x.com" not in [m["to"][0]["email"] for m in seams["sent"]]
        assert totals["already"] == 1
        assert totals["sent"] == 2

    def test_nobody_who_opted_out_is_mailed(self, seams):
        seams["opted_out"] = {"a@x.com"}
        rejections.send_bulk(THREE)
        assert "a@x.com" not in [m["to"][0]["email"] for m in seams["sent"]]

    def test_each_send_is_recorded_as_it_goes(self, seams):
        # Not batched at the end: a process that dies at message two must
        # leave the first two people in the ledger, or the re-run mails them
        # a second time.
        rejections.send_bulk(THREE)
        assert [e[0] for e in seams["recorded"]] == ["a@x.com", "b@x.com", "c@x.com"]
        assert all(kw["status"] == "sent" for _, kw in seams["recorded"])

    def test_a_failure_is_recorded_and_the_batch_carries_on(self, seams, monkeypatch):
        def flaky(**kw):
            if kw["to"][0]["email"] == "b@x.com":
                raise rejections.brevo_client.BrevoError("bad address")
            seams["sent"].append(kw)

        monkeypatch.setattr(rejections.brevo_client, "send_email", flaky)
        totals = rejections.send_bulk(THREE)

        assert totals["sent"] == 2
        assert totals["failed"] == 1
        statuses = {email: kw["status"] for email, kw in seams["recorded"]}
        assert statuses["b@x.com"] == "failed"
        # And the last one still went -- one bad address does not end a batch.
        assert "c@x.com" in [m["to"][0]["email"] for m in seams["sent"]]

    def test_a_run_of_failures_stops_the_batch(self, seams, monkeypatch):
        monkeypatch.setattr(rejections, "REJECTION_ABORT_AFTER", 2)
        monkeypatch.setattr(
            rejections.brevo_client, "send_email",
            lambda **kw: (_ for _ in ()).throw(
                rejections.brevo_client.BrevoError("api key")))

        totals = rejections.send_bulk(THREE)
        assert totals["failed"] == 2
        assert "aborted" in totals
        # The third was never attempted, so it is not in the ledger and the
        # re-run will pick it up.
        assert [e[0] for e in seams["recorded"]] == ["a@x.com", "b@x.com"]

    def test_the_per_send_cap_is_refused_rather_than_truncated(self, seams, monkeypatch):
        monkeypatch.setattr(rejections, "REJECTION_MAX_PER_SEND", 2)
        with pytest.raises(rejections.RejectionError, match="cap"):
            rejections.send_bulk(THREE)
        assert seams["sent"] == []

    def test_candidate_mail_switched_off_sends_nothing(self, seams, monkeypatch):
        monkeypatch.setattr(rejections, "PIPELINE_EMAILS_ENABLED", False)
        with pytest.raises(rejections.RejectionError):
            rejections.send_bulk(THREE)
        assert seams["sent"] == []

    def test_progress_is_reported_per_candidate(self, seams):
        seen = []
        rejections.send_bulk(THREE, progress=lambda d, t, _: seen.append((d, t)))
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_the_message_written_is_the_message_sent(self, seams):
        rejections.send_bulk([THREE[0]], subject="Re: {role}",
                             message="Hi {first_name}, no.",
                             job_title="AI Strategist")
        sent = seams["sent"][0]
        assert sent["subject"] == "Re: AI Strategist"
        assert "Hi A, no." in sent["text"]

    def test_every_message_carries_the_way_out(self, seams, monkeypatch):
        headers = {"List-Unsubscribe": "<https://x/u>"}
        monkeypatch.setattr(rejections.unsubscribe, "mail_headers",
                            lambda email: headers)
        rejections.send_bulk(THREE)
        assert all(m["headers"] == headers for m in seams["sent"])


# ---------------------------------------------------------------------------
# The two buttons, at the route level
# ---------------------------------------------------------------------------
#
# The one thing that must be true of this surface however the rest of it
# changes: RECORDING EMAILS NOBODY. It is the button a recruiter presses after
# they have already mailed four hundred people by hand, and a version of it
# that sends would mail all four hundred a second time in one click.

@pytest.fixture
def dashboard(monkeypatch):
    """The app with the session guard and the admin check stood down."""
    from backend.web import server

    monkeypatch.setattr(server, "AUTH_ENABLED", False)
    monkeypatch.setattr(server, "_require_admin", lambda: None)
    monkeypatch.setattr(server, "_mongo_guard", lambda: None)
    server.app.config["TESTING"] = True
    server.app.config["REVIEW_ONLY"] = False
    return server.app.test_client()


class TestTheRoutes:
    def test_recording_sends_nothing(self, dashboard, seams, monkeypatch):
        from backend.web import server

        written = []
        monkeypatch.setattr(server.store, "record_rejections",
                            lambda entries, **kw: (
                                written.extend(entries),
                                {"added": len(entries), "updated": 0,
                                 "total": len(entries)})[1])
        monkeypatch.setattr(server.store, "rejection_stats", lambda: {})

        response = dashboard.post("/api/rejections/import", json={
            "text": "a@x.com\nb@x.com"})

        assert response.status_code == 200
        assert len(written) == 2
        assert seams["sent"] == [], "the record button sent email -- that is the bug"
        assert "Nothing was emailed" in response.get_json()["message"]

    def test_a_paste_with_no_addresses_in_it_is_refused(self, dashboard):
        response = dashboard.post("/api/rejections/import",
                                  json={"text": "no addresses here"})
        assert response.status_code == 400

    def test_checking_a_list_sends_nothing_and_records_nothing(
            self, dashboard, seams, monkeypatch):
        from backend.web import server
        monkeypatch.setattr(server.store, "clean_email",
                            lambda v: str(v or "").strip().lower())

        body = dashboard.post("/api/rejections/parse",
                              json={"text": "a@x.com\nb@x.com"}).get_json()

        assert body["total"] == 2 and body["mailable"] == 2
        assert seams["sent"] == [] and seams["recorded"] == []

    def test_a_batch_with_nobody_new_is_refused_before_the_lock(
            self, dashboard, seams):
        # 409 with a reason, not "a send is already in progress" -- telling the
        # recruiter the queue is busy would be a lie about why nothing happened.
        seams["already"] = {"a@x.com", "b@x.com"}
        response = dashboard.post("/api/rejections/send",
                                  json={"text": "a@x.com\nb@x.com"})
        assert response.status_code == 409
        assert "already been told" in response.get_json()["error"]
        assert seams["sent"] == []

    def test_a_send_returns_202_without_waiting_for_the_batch(
            self, dashboard, seams, monkeypatch):
        import time
        from backend.web import server

        def slow(**kw):
            time.sleep(0.4)
            seams["sent"].append(kw)

        monkeypatch.setattr(rejections.brevo_client, "send_email", slow)

        began = time.time()
        response = dashboard.post("/api/rejections/send",
                                  json={"text": "a@x.com\nb@x.com"})
        elapsed = time.time() - began

        assert response.status_code == 202
        assert elapsed < 0.3, "the request waited for the send -- that is the bug"
        body = response.get_json()
        assert body["queued"] == 2
        assert body["poll"].endswith(body["job"])

        deadline = time.time() + 10
        while time.time() < deadline:
            snapshot = dashboard.get(body["poll"]).get_json()
            if snapshot["state"] != "running":
                break
            time.sleep(0.05)
        assert snapshot["state"] == "done"
        assert snapshot["totals"]["sent"] == 2
        # And the lock is back, so the next click is not refused for ever.
        assert server._reject_lock.acquire(blocking=False)
        server._reject_lock.release()

    def test_an_unknown_batch_is_404(self, dashboard):
        assert dashboard.get("/api/rejections/send/nope").status_code == 404

    def test_the_preview_renders_without_sending(self, dashboard, seams):
        body = dashboard.post("/api/rejections/preview", json={
            "subject": "Hi {first_name}", "message": "No.",
            "name": "Asha", "email": "asha@x.com"}).get_json()
        assert body["email"]["subject"] == "Hi Asha"
        assert seams["sent"] == []


class TestReviewOnlyMode:
    @pytest.mark.parametrize("path", ["/api/rejections",
                                      "/api/rejections/send/abc"])
    def test_the_rejection_surface_does_not_exist(self, client, path):
        # 404, not 403. The process facing the internet must not admit that a
        # button which mails several hundred people exists behind it.
        from backend.web import server
        server.app.config["REVIEW_ONLY"] = True
        try:
            assert client.get(path).status_code == 404
        finally:
            server.app.config["REVIEW_ONLY"] = False


# ---------------------------------------------------------------------------
# The rejected queue knows who has already heard
# ---------------------------------------------------------------------------
#
# THE BUG THIS PREVENTS, IN FULL. The "Rejected — ready for rejection emails"
# list answers "who did the assessment reject", and it is read as though it
# answered "who is still owed an email". Those are the same list exactly once:
# the first time. The month after, twenty new people land in it beside the two
# hundred who were mailed last month, and ticking all of it sends two hundred
# people a second rejection out of a list that looked correct.
#
# So the queue carries the ledger's answer per row, and the page unticks on it.

class TestTheRejectedQueueIsAnnotated:
    @pytest.fixture
    def queue(self, dashboard, monkeypatch):
        from backend.web import server

        rows = [
            {"_id": 1, "candidate_name": "A", "candidate_email": "a@x.com",
             "job_id": 7, "decision": {"status": "rejected", "reason": "missing_video"}},
            {"_id": 2, "candidate_name": "B", "candidate_email": "b@x.com",
             "job_id": 7, "decision": {"status": "rejected", "reason": "missing_video"}},
            {"_id": 3, "candidate_name": "C", "candidate_email": "c@x.com",
             "job_id": 7, "decision": {"status": "rejected", "reason": "missing_video"}},
        ]
        monkeypatch.setattr(server.store, "list_rejected", lambda **kw: rows)
        return dashboard

    def _ledger(self, monkeypatch, mapping):
        from backend.web import server
        monkeypatch.setattr(server.store, "rejections_for", lambda emails: mapping)

    def test_nobody_told_yet_means_everybody_is_waiting(self, queue, monkeypatch):
        self._ledger(monkeypatch, {})
        body = queue.get("/api/evaluations/rejected").get_json()
        assert body["already_told"] == 0
        assert body["waiting"] == 3
        assert all(c["already_told"] is False for c in body["candidates"])

    def test_somebody_told_is_flagged_with_when_and_how(self, queue, monkeypatch):
        from datetime import datetime, timezone
        when = datetime(2026, 8, 25, tzinfo=timezone.utc)
        self._ledger(monkeypatch, {"b@x.com": {"_id": "b@x.com",
                                               "status": "recorded",
                                               "rejected_at": when}})
        body = queue.get("/api/evaluations/rejected").get_json()

        assert body["already_told"] == 1
        assert body["waiting"] == 2
        told = {c["candidate_email"]: c for c in body["candidates"]}
        assert told["b@x.com"]["already_told"] is True
        assert told["b@x.com"]["told_how"] == "recorded"
        assert told["b@x.com"]["told_at"].startswith("2026-08-25")
        assert told["a@x.com"]["already_told"] is False

    def test_it_does_not_matter_which_surface_told_them(self, queue, monkeypatch):
        # Mailed by the bulk send, by the board, or typed in by hand -- the
        # queue must hold all three back, or the route somebody was rejected
        # through decides whether they get a second one.
        self._ledger(monkeypatch, {
            "a@x.com": {"status": "sent", "rejected_at": None},
            "b@x.com": {"status": "recorded", "rejected_at": None},
        })
        body = queue.get("/api/evaluations/rejected").get_json()
        assert body["already_told"] == 2 and body["waiting"] == 1

    def test_a_failed_send_leaves_them_in_the_queue(self, queue, monkeypatch):
        # We tried and it bounced, so that candidate has heard nothing and is
        # still owed a reply. Treating a failure as "told" is how somebody is
        # silently dropped for ever -- which is the whole reason failures are
        # written to the ledger rather than discarded.
        self._ledger(monkeypatch, {"c@x.com": {"status": "failed",
                                               "rejected_at": None,
                                               "error": "bounced"}})
        body = queue.get("/api/evaluations/rejected").get_json()

        assert body["waiting"] == 3
        told = {c["candidate_email"]: c for c in body["candidates"]}
        assert told["c@x.com"]["already_told"] is False
        # ...but the page can still say what happened.
        assert told["c@x.com"]["told_how"] == "failed"

    def test_an_unreadable_ledger_does_not_take_the_list_down(self, monkeypatch):
        """
        rejections_for() fails OPEN, unlike already_rejected().

        Same collection, opposite default, because the consequence is
        opposite. already_rejected() gates a send: an unreadable ledger there
        must stop five hundred messages rather than risk a second rejection.
        This one only decorates a list on screen, and the cost of it failing
        closed would be a panel that says everybody has already been told --
        which is both wrong and the more dangerous of the two errors to show.
        """
        from pymongo.errors import PyMongoError
        from backend.database import mongo_store as store

        class Broken:
            def find(self, *a, **k):
                raise PyMongoError("mongo is down")

        monkeypatch.setattr(store, "get_db",
                            lambda: type("DB", (), {"rejections": Broken()})())
        assert store.rejections_for(["a@x.com"]) == {}
