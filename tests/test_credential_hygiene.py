"""
A pasted credential carries whatever came with it.

THE RUN THIS COMES FROM. An hourly grading run reached the provider and failed
all 18 candidates in a role with:

    Gave up after 2 attempts (connection error: Invalid leading whitespace,
    reserved character(s), or return character(s) in header value: '***')

Nothing was wrong with the network or the key. LLM_API_KEY goes straight into

    headers = {"Authorization": f"Bearer {LLM_API_KEY}", ...}

and an HTTP header value may not contain a newline, so `requests` refused to
build the request at all. The evaluator reports that as a CONNECTION error,
so the log blamed the network for a paste.

A trailing newline is easy to include when pasting into a GitHub secret box,
and python-dotenv strips it on the way in -- so a laptop never reproduces it
and only the scheduled run fails.
"""

import importlib

import pytest
import requests

CREDENTIALS = ["LLM_API_KEY", "PORTAL_EMAIL", "PORTAL_PASSWORD", "MONGO_URI"]

# What a paste actually picks up.
DIRT = [
    ("trailing newline", "\n"),
    ("trailing CRLF", "\r\n"),
    ("trailing space", "   "),
    ("trailing tab", "\t"),
]


def config_with(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import backend.config
    return importlib.reload(backend.config)


@pytest.fixture(autouse=True)
def _restore():
    yield
    import backend.config
    importlib.reload(backend.config)


class TestTheApiKeyCanAlwaysBecomeAHeader:
    """The exact failure, asserted against the library that raised it."""

    @pytest.mark.parametrize("label,suffix", DIRT)
    def test_a_dirty_key_still_builds_a_header(self, monkeypatch, label, suffix):
        cfg = config_with(monkeypatch, LLM_API_KEY="nvapi-abc123" + suffix)
        # This is the call that raised InvalidHeader in production.
        requests.Request(
            "POST", "https://example.invalid",
            headers={"Authorization": f"Bearer {cfg.LLM_API_KEY}"}).prepare()

    def test_the_key_itself_is_clean(self, monkeypatch):
        cfg = config_with(monkeypatch, LLM_API_KEY="  nvapi-abc123\n")
        assert cfg.LLM_API_KEY == "nvapi-abc123"

    def test_an_embedded_newline_would_still_be_caught_loudly(self, monkeypatch):
        """
        Stripping fixes the ends, not the middle. A key with a newline INSIDE
        it is not a paste artefact -- it is the wrong string -- and must not be
        silently patched up into something that looks like a credential.
        """
        cfg = config_with(monkeypatch, LLM_API_KEY="nvapi-ab\ncd")
        assert "\n" in cfg.LLM_API_KEY
        with pytest.raises(Exception):
            requests.Request(
                "POST", "https://example.invalid",
                headers={"Authorization": f"Bearer {cfg.LLM_API_KEY}"}).prepare()


class TestEveryCredentialIsStripped:
    @pytest.mark.parametrize("name", CREDENTIALS)
    @pytest.mark.parametrize("label,suffix", DIRT)
    def test_surrounding_whitespace_is_removed(self, monkeypatch, name, suffix, label):
        value = {"MONGO_URI": "mongodb://127.0.0.1:27017"}.get(name, "some-value")
        cfg = config_with(monkeypatch, **{name: value + suffix})
        assert getattr(cfg, name) == value

    def test_the_portal_password_is_stripped_too(self, monkeypatch):
        # Sent in a form body rather than a header, so a stray newline fails as
        # a WRONG PASSWORD rather than an error -- the worse of the two to
        # diagnose, because it looks like rotated credentials.
        cfg = config_with(monkeypatch, PORTAL_PASSWORD="hunter2\n")
        assert cfg.PORTAL_PASSWORD == "hunter2"

    def test_a_dirty_mongo_uri_does_not_reach_the_driver(self, monkeypatch):
        cfg = config_with(monkeypatch, MONGO_URI="mongodb://127.0.0.1:27017\n")
        assert not cfg.MONGO_URI.endswith("\n")


class TestRealValuesAreUnchanged:
    def test_internal_characters_survive(self, monkeypatch):
        # Stripping must not touch what is legitimately inside a credential --
        # an Atlas URI is full of punctuation that matters.
        uri = "mongodb+srv://user:p%40ss-w0rd@cluster.izzotww.mongodb.net/?appName=x"
        cfg = config_with(monkeypatch, MONGO_URI=uri)
        assert cfg.MONGO_URI == uri

    def test_an_ordinary_key_is_untouched(self, monkeypatch):
        cfg = config_with(monkeypatch, LLM_API_KEY="nvapi-ABC_123-xyz")
        assert cfg.LLM_API_KEY == "nvapi-ABC_123-xyz"
