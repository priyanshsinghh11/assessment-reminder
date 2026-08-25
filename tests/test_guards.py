"""
The shape of the two exposure modes, and the client address the throttle keys on.

These do NOT replace tests/test_access.py, which needs a real database to check
who may see which role. What they pin is the part that is decided before any
account is looked up: which paths exist at all in review-only mode, which are
reachable without a session, and how the caller's address is derived -- because
a throttle keyed on a value the caller can choose is not a throttle.
"""

from pathlib import Path

import pytest

from backend.web import server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def review_only(client):
    server.app.config["REVIEW_ONLY"] = True
    yield client
    server.app.config["REVIEW_ONLY"] = False


class TestHealth:
    def test_answers_without_a_session(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json() == {"status": "ok"}

    def test_answers_in_review_only_mode(self, review_only):
        # The review container needs a liveness probe as much as the dashboard
        # one does, and both guards fail closed, so it has to be named twice.
        assert review_only.get("/healthz").status_code == 200

    def test_reveals_nothing_but_liveness(self, client):
        # It is reachable by anyone, so what it says is what an unauthenticated
        # caller learns. No version, no config, no database state.
        assert set(client.get("/healthz").get_json()) == {"status"}


class TestReviewOnlyMode:
    @pytest.mark.parametrize("path", [
        "/evaluations.html",     # the dashboard page itself
        "/app.js",               # names every endpoint it calls
        "/evaluations.js",
        "/api/state",
        "/api/logs",
    ])
    def test_the_dashboard_surface_does_not_exist(self, review_only, path):
        # 404, not 401 or 403: a scan must not be able to tell that a dashboard
        # exists on the other side of this process.
        assert review_only.get(path).status_code == 404

    @pytest.mark.parametrize("path", ["/review.js", "/review.css", "/styles.css"])
    def test_the_review_pages_own_files_are_served(self, review_only, path):
        # These sit at the root, not under /review/, so the prefix check walks
        # straight past them. Without the by-name allowlist the page loads and
        # its script 401s, which a manager reads as a page stuck on "Loading".
        assert review_only.get(path).status_code != 404


class TestUnauthenticatedDashboard:
    @pytest.mark.parametrize("path", ["/api/state", "/api/logs"])
    def test_demands_a_session(self, client, path):
        assert client.get(path).status_code in (302, 401)

    def test_the_unsubscribe_path_is_public(self, client, fixed_secret):
        from backend.notifications import unsubscribe
        token = unsubscribe.token_for("ada@example.com")
        # No session, no CSRF token, and it must still answer -- the candidate
        # holding this link has no account and never will.
        assert client.get(f"/unsubscribe/{token}").status_code == 200

    def test_the_unsubscribe_path_works_in_review_only_mode(self, review_only,
                                                            fixed_secret):
        # PUBLIC_BASE_URL points at the review process, so this is the host the
        # link in every candidate email resolves to. Left off that allowlist,
        # every unsubscribe we send would 404.
        from backend.notifications import unsubscribe
        token = unsubscribe.token_for("ada@example.com")
        assert review_only.get(f"/unsubscribe/{token}").status_code == 200

    def test_a_get_does_not_unsubscribe_anyone(self, client, fixed_secret,
                                               monkeypatch):
        # Mail clients and scanners follow links to see where they go. An
        # opt-out caused by a prefetch is a decision the candidate never made,
        # which is why RFC 8058 one-click is a POST.
        from backend.notifications import unsubscribe
        called = []
        monkeypatch.setattr(unsubscribe, "suppress",
                            lambda *a, **k: called.append(a) or True)
        client.get(f"/unsubscribe/{unsubscribe.token_for('ada@example.com')}")
        assert called == []

    def test_an_unrecognised_token_is_404_not_a_traceback(self, client, fixed_secret):
        assert client.get("/unsubscribe/not-a-real-token").status_code == 404


class TestClientIp:
    def _ip(self, monkeypatch, hops, headers, peer="10.0.0.1"):
        monkeypatch.setattr(server, "TRUSTED_PROXY_HOPS", hops)
        with server.app.test_request_context("/", headers=headers,
                                             environ_base={"REMOTE_ADDR": peer}):
            return server._client_ip()

    def test_no_proxies_ignores_the_header_entirely(self, monkeypatch):
        # THE IMPORTANT ONE. X-Forwarded-For is written by the client, so with
        # nothing in front of this process it is pure fiction -- and a throttle
        # keyed on it gives an attacker a fresh bucket per request.
        got = self._ip(monkeypatch, 0,
                       {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        assert got == "10.0.0.1"

    def test_one_hop_takes_the_rightmost_entry(self, monkeypatch):
        got = self._ip(monkeypatch, 1,
                       {"X-Forwarded-For": "9.9.9.9, 203.0.113.5"})
        assert got == "203.0.113.5"

    def test_two_hops_reaches_one_further_left(self, monkeypatch):
        got = self._ip(monkeypatch, 2,
                       {"X-Forwarded-For": "9.9.9.9, 203.0.113.5, 198.51.100.7"})
        assert got == "203.0.113.5"

    def test_a_forged_prefix_cannot_reach_past_the_configured_hops(self, monkeypatch):
        # The client writes "1.1.1.1" hoping to be counted as that address; one
        # real proxy appends what it actually saw. With hops=1 we take the
        # proxy's word and discard theirs.
        got = self._ip(monkeypatch, 1,
                       {"X-Forwarded-For": "1.1.1.1, 203.0.113.5"})
        assert got == "203.0.113.5"

    def test_a_missing_header_falls_back_to_the_peer(self, monkeypatch):
        assert self._ip(monkeypatch, 1, {}) == "10.0.0.1"

    def test_a_shorter_chain_than_configured_does_not_crash(self, monkeypatch):
        got = self._ip(monkeypatch, 3, {"X-Forwarded-For": "203.0.113.5"})
        assert got == "203.0.113.5"


class TestThrottleConfiguration:
    def test_the_ip_limit_sits_below_the_account_lockout(self):
        # The whole mechanism. If a single source can make more failed attempts
        # than it takes to lock an account, the throttle never fires in time
        # and anyone who knows an admin's address can keep them signed out.
        from backend.core.config import LOGIN_IP_MAX_ATTEMPTS, LOGIN_MAX_ATTEMPTS
        assert LOGIN_IP_MAX_ATTEMPTS < LOGIN_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Booting where there is no writable disk
# ---------------------------------------------------------------------------
#
# THE BUG THIS PINS took a production deployment down completely and gave no
# clue why. setup_logging() runs at IMPORT from wsgi.py, and it opened a file
# under logs/. On a serverless platform the deployment is mounted read-only, so
# the mkdir raised OSError before Flask existed -- every request 500'd with a
# platform error page that says nothing about a log directory.
#
# It is invisible from a laptop, where logs/ is always writable, which is
# exactly why it belongs in the suite rather than in a runbook.

class TestLoggingWithoutAWritableDisk:
    @staticmethod
    def _reset():
        """setup_logging() is idempotent by design, so undo it between cases."""
        import logging
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        if hasattr(root, "_ajaia_configured"):
            del root._ajaia_configured

    @pytest.fixture
    def readonly_disk(self, monkeypatch):
        from pathlib import Path

        real = Path.mkdir

        def refuse(self, *args, **kwargs):
            if "logs" in str(self):
                raise OSError(30, "Read-only file system")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", refuse)
        self._reset()
        yield
        self._reset()

    def test_it_configures_rather_than_raising(self, readonly_disk):
        import logging
        from backend.notifications import reminder

        reminder.setup_logging()          # must not raise -- that is the bug
        assert getattr(logging.getLogger(), "_ajaia_configured", False)

    def test_stdout_still_gets_the_log(self, readonly_disk):
        import logging
        from backend.notifications import reminder

        reminder.setup_logging()
        kinds = [type(h).__name__ for h in logging.getLogger().handlers]
        # StreamHandler and nothing else: the file handler is the half that
        # needed a disk, and serverless platforms collect stdout anyway.
        assert "StreamHandler" in kinds
        assert "RotatingFileHandler" not in kinds

    def test_a_writable_disk_still_gets_a_file(self, monkeypatch, tmp_path):
        # The fix must not quietly cost everyone else their log file.
        import logging
        from backend.notifications import reminder

        self._reset()
        monkeypatch.setattr(reminder, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(reminder, "LOG_FILE", tmp_path / "logs" / "r.log")
        try:
            reminder.setup_logging()
            kinds = [type(h).__name__ for h in logging.getLogger().handlers]
            assert "RotatingFileHandler" in kinds
            assert "StreamHandler" in kinds
        finally:
            self._reset()


class TestTheServerlessEntryPoint:
    """
    api/index.py must go through wsgi.py, not around it.

    `from backend.web.server import app` is the tempting shortcut: same object,
    boots faster, skips the call that used to crash. It also skips
    `app.config["REVIEW_ONLY"] = ...`, and _review_only() reads that key with a
    falsy default -- so the deployment would come up serving the full dashboard
    on a public URL. It fails OPEN and looks entirely normal.
    """

    def test_it_imports_the_app_from_wsgi(self):
        # Parsed, not grepped. The docstring in that file quotes the shortcut
        # in order to warn against it, and a substring check reads the warning
        # as the offence.
        import ast

        tree = ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))
        sources = {node.module for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom)
                   and any(alias.name == "app" for alias in node.names)}
        assert sources == {"wsgi"}, f"app is imported from {sources}"

    def test_review_only_is_the_deployed_default(self):
        import json
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        # Not a precaution -- the dashboard's sends run on background threads
        # that a frozen function kills mid-batch. See api/index.py.
        assert config["env"]["REVIEW_ONLY"] == "1"

    def test_every_path_reaches_the_function(self):
        import json
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        # Including /unsubscribe/<token>, which every candidate email links to.
        assert any(r["src"] == "/(.*)" for r in config["routes"])

    def test_the_local_venv_is_not_deployed(self):
        ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8")
        for path in ("venv/", "logs/", "state/", ".env", "*.csv"):
            assert path in ignored, f"{path} would be uploaded"
