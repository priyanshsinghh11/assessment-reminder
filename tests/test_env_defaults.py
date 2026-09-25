"""
An empty environment variable is not a value.

THE RUN THIS COMES FROM. `sync.yml` passes `MONGO_DB: ${{ vars.MONGO_DB }}`.
With no repository variable of that name defined, GitHub does not leave
MONGO_DB unset -- it sets it to the empty string. `os.environ.get(name,
default)` returns its default only for an ABSENT name, so the runner got "",
and the sync failed with:

    Cannot reach MongoDB at ***...mongodb.net/?appName=...
    : database name cannot be the empty string

Atlas was reachable and the credentials were right. The database name was
blank. Grading carried a worse version of the same bug: int("") raises at
import, so `manage.py grade` would have died with a ValueError before
reaching any of its own code.

These pin the four values the workflows pass through `${{ vars.X }}`. An
`.env` line left as a bare `FOO=` produces the identical situation, which is
the more ordinary way to meet it.
"""

import importlib

import pytest

VARS = ["MONGO_DB", "LLM_BASE_URL", "LLM_MODEL", "LLM_CONCURRENCY"]


def config_with(monkeypatch, **env):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import backend.config
    return importlib.reload(backend.config)


@pytest.fixture(autouse=True)
def _restore():
    # Other modules hold references to these at import, so put the real
    # values back rather than leaving a reloaded module behind.
    yield
    import backend.config
    importlib.reload(backend.config)


class TestEmptyFallsBackToTheDefault:
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_the_database_name_is_never_empty(self, monkeypatch, blank):
        # The exact failure: a blank name reaches pymongo, which refuses it
        # AFTER a successful connection -- so the error reads like the cluster
        # is unreachable when it is perfectly fine.
        cfg = config_with(monkeypatch, MONGO_DB=blank)
        assert cfg.MONGO_DB == "assessment-evaluation"

    def test_concurrency_does_not_raise_on_empty(self, monkeypatch):
        # int("") is a ValueError at IMPORT, which means a traceback instead
        # of a log line, from a module nobody has called yet.
        cfg = config_with(monkeypatch, LLM_CONCURRENCY="")
        assert cfg.LLM_CONCURRENCY == 6

    def test_the_provider_falls_back(self, monkeypatch):
        cfg = config_with(monkeypatch, LLM_BASE_URL="", LLM_MODEL="")
        assert cfg.LLM_BASE_URL and cfg.LLM_MODEL

    def test_every_vars_passed_value_survives_an_empty_environment(self, monkeypatch):
        """All four at once -- a runner with no repository variables set."""
        cfg = config_with(monkeypatch, **{name: "" for name in VARS})
        assert cfg.MONGO_DB == "assessment-evaluation"
        assert cfg.LLM_CONCURRENCY == 6
        assert isinstance(cfg.LLM_CONCURRENCY, int)
        assert cfg.LLM_BASE_URL.startswith("http")
        assert "/" in cfg.LLM_MODEL


class TestARealValueStillWins:
    def test_an_explicit_database_name_is_used(self, monkeypatch):
        assert config_with(monkeypatch, MONGO_DB="staging-db").MONGO_DB == "staging-db"

    def test_an_explicit_concurrency_is_used(self, monkeypatch):
        cfg = config_with(monkeypatch, LLM_CONCURRENCY="3")
        assert cfg.LLM_CONCURRENCY == 3

    def test_an_explicit_model_is_used(self, monkeypatch):
        cfg = config_with(monkeypatch, LLM_MODEL="acme/model-x")
        assert cfg.LLM_MODEL == "acme/model-x"

    def test_surrounding_whitespace_is_trimmed_not_honoured(self, monkeypatch):
        # A trailing newline from a pasted secret must not become part of a
        # database name or a model id.
        cfg = config_with(monkeypatch, MONGO_DB="  staging-db  ")
        assert cfg.MONGO_DB == "staging-db"


class TestUnsetStillWorks:
    def test_absent_names_still_produce_usable_values(self, monkeypatch):
        """
        The original behaviour, which must not have been traded away.

        Asserted as invariants rather than against the literal defaults on
        purpose: removing a name from os.environ does not mean nothing
        supplies it. `load_dotenv` runs on reload, so on a developer's machine
        `.env` fills it back in -- LLM_CONCURRENCY is 4 there and 6 on a bare
        runner, and both are correct. Pinning 6 here would pass in CI and fail
        on every laptop that has a .env, which is the kind of test that gets
        deleted rather than believed.
        """
        cfg = config_with(monkeypatch)
        assert isinstance(cfg.MONGO_DB, str) and cfg.MONGO_DB.strip()
        assert isinstance(cfg.LLM_CONCURRENCY, int) and cfg.LLM_CONCURRENCY > 0

    def test_a_dotenv_value_is_not_overridden_by_the_fallback(self, monkeypatch):
        # The other half: _env must not shadow a real .env value with its own
        # default. Only an EMPTY value falls through.
        cfg = config_with(monkeypatch, MONGO_DB="from-env-file")
        assert cfg.MONGO_DB == "from-env-file"
