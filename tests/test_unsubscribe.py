"""
The unsubscribe token and the headers built from it.

The token is the whole credential on a public endpoint that changes state, so
the forgery cases matter more than the happy path: a token that can be edited
into a neighbour's address is a way to unsubscribe somebody else.
"""

import base64

import pytest

from backend.mail import unsubscribe


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class TestTokenRoundTrip:
    def test_reads_back_the_address(self, fixed_secret):
        token = unsubscribe.token_for("ada@example.com")
        assert unsubscribe.email_for(token) == "ada@example.com"

    def test_normalises_case_and_whitespace(self, fixed_secret):
        token = unsubscribe.token_for("  Ada@Example.COM  ")
        assert unsubscribe.email_for(token) == "ada@example.com"

    def test_is_stable_across_calls(self, fixed_secret):
        # A link in an email sent last week has to still work today, so the
        # token must not carry a nonce or a timestamp.
        assert (unsubscribe.token_for("ada@example.com")
                == unsubscribe.token_for("ada@example.com"))

    def test_different_addresses_get_different_tokens(self, fixed_secret):
        assert (unsubscribe.token_for("a@example.com")
                != unsubscribe.token_for("b@example.com"))

    def test_plus_addressing_survives(self, fixed_secret):
        token = unsubscribe.token_for("ada+jobs@example.com")
        assert unsubscribe.email_for(token) == "ada+jobs@example.com"


class TestTokenForgery:
    def test_swapping_the_address_invalidates_it(self, fixed_secret):
        token = unsubscribe.token_for("victim@example.com")
        _body, signature = token.split(".")
        forged = f"{b64(b'someone.else@example.com')}.{signature}"
        assert unsubscribe.email_for(forged) is None

    def test_a_wrong_signature_is_refused(self, fixed_secret):
        body, _sig = unsubscribe.token_for("ada@example.com").split(".")
        assert unsubscribe.email_for(f"{body}.AAAAAAAAAAAAAAAAAAAAAA") is None

    def test_a_different_secret_invalidates_every_token(self, monkeypatch):
        from backend.db import store
        monkeypatch.setattr(store, "get_app_secret", lambda: "secret-one")
        token = unsubscribe.token_for("ada@example.com")
        monkeypatch.setattr(store, "get_app_secret", lambda: "secret-two")
        assert unsubscribe.email_for(token) is None

    @pytest.mark.parametrize("bad", [
        "", "garbage", "no-separator", "a.b.c", ".", "....",
        "!!!.!!!", "AAAA.", ".AAAA",
    ])
    def test_malformed_tokens_return_none_rather_than_raising(self, bad, fixed_secret):
        # The endpoint hands whatever is in the URL straight to this. An
        # exception here would be a 500 on a public path.
        assert unsubscribe.email_for(bad) is None

    def test_a_truncated_token_is_refused(self, fixed_secret):
        token = unsubscribe.token_for("ada@example.com")
        assert unsubscribe.email_for(token[:-4]) is None


class TestMailHeaders:
    def test_https_gets_one_click(self, fixed_secret, monkeypatch):
        monkeypatch.setattr(unsubscribe, "PUBLIC_BASE_URL", "https://hire.example.com")
        monkeypatch.setattr(unsubscribe, "UNSUBSCRIBE_MAILTO", "")
        headers = unsubscribe.mail_headers("ada@example.com")
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert headers["List-Unsubscribe"].startswith("<https://hire.example.com/unsubscribe/")

    def test_plain_http_never_offers_one_click(self, fixed_secret, monkeypatch):
        # A one-click endpoint without TLS is an unsubscribe anyone on the path
        # can forge, and the link would be built from a loopback address that
        # opens on nobody's machine but ours.
        monkeypatch.setattr(unsubscribe, "PUBLIC_BASE_URL", "http://127.0.0.1:5000")
        monkeypatch.setattr(unsubscribe, "UNSUBSCRIBE_MAILTO", "jobs@example.com")
        headers = unsubscribe.mail_headers("ada@example.com")
        assert "List-Unsubscribe-Post" not in headers
        assert headers["List-Unsubscribe"] == "<mailto:jobs@example.com?subject=unsubscribe>"

    def test_https_and_mailto_offers_both(self, fixed_secret, monkeypatch):
        monkeypatch.setattr(unsubscribe, "PUBLIC_BASE_URL", "https://hire.example.com")
        monkeypatch.setattr(unsubscribe, "UNSUBSCRIBE_MAILTO", "jobs@example.com")
        value = unsubscribe.mail_headers("ada@example.com")["List-Unsubscribe"]
        assert value.count("<") == 2 and "mailto:" in value

    def test_nothing_configured_yields_no_header_rather_than_a_broken_one(
            self, fixed_secret, monkeypatch):
        monkeypatch.setattr(unsubscribe, "PUBLIC_BASE_URL", "http://127.0.0.1:5000")
        monkeypatch.setattr(unsubscribe, "UNSUBSCRIBE_MAILTO", "")
        assert unsubscribe.mail_headers("ada@example.com") == {}


class TestSuppressionFailsClosed:
    def test_an_unreachable_database_treats_the_address_as_opted_out(self, monkeypatch):
        # The one read in this system that must fail CLOSED: mailing somebody
        # who asked us to stop cannot be undone, so a database we cannot reach
        # stops the send rather than waving it through.
        from backend.db import store

        def boom():
            raise RuntimeError("mongo is down")

        monkeypatch.setattr(store, "get_db", boom)
        assert unsubscribe.is_suppressed("ada@example.com") is True

    def test_bulk_read_fails_closed_for_every_address(self, monkeypatch):
        from backend.db import store

        def boom():
            raise RuntimeError("mongo is down")

        monkeypatch.setattr(store, "get_db", boom)
        addresses = ["a@x.com", "b@x.com"]
        assert unsubscribe.suppressed_among(addresses) == set(addresses)

    def test_empty_input_needs_no_database_at_all(self, monkeypatch):
        from backend.db import store

        def boom():
            raise AssertionError("should not have queried")

        monkeypatch.setattr(store, "get_db", boom)
        assert unsubscribe.suppressed_among([]) == set()
        assert unsubscribe.is_suppressed("") is False
