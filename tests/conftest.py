"""
Shared fixtures.

WHAT THESE TESTS ARE FOR. Not the access rules -- test_access.py owns those and
needs a real database to say anything true about them. These cover the layer
underneath: the date arithmetic that decides who is in the reminder window, the
key that decides whether somebody has already been mailed, the comparison that
decides whether they have started, and the signed token that lets them opt out.

All of it is pure, so all of it runs with no MongoDB, no Brevo, no Workable and
no credentials -- which is the only reason it can run on every commit. Anything
here that reaches for the network or the database is a bug in the test.
"""

import sys
from pathlib import Path

import pytest

# The suite imports the app the same way the app imports itself -- from the
# repository root -- so a test run from anywhere finds the same modules the
# server would.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class FakeCollection:
    """
    Enough of a Mongo collection to test the conditional update honestly.

    Only what reminder_log uses: find, find_one, and update_one with
    $inc / $set / $setOnInsert against an equality filter (plus $ne, which the
    suppression guard needs).

    THE FILTER IS REALLY EVALUATED. That is the whole reason this is modelled
    rather than stubbed: the safety of the claim rests on an update that
    applies only while the document still matches what the decision was made
    from. A stub that always applied would let a compare-and-swap silently
    become an unconditional write with every test still green.
    """

    def __init__(self, documents=None):
        self.documents = {d["_id"]: dict(d) for d in (documents or [])}

    def find_one(self, query, projection=None):
        document = self.documents.get(query.get("_id"))
        return dict(document) if document else None

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.documents.values()]

    def _matches(self, document, query):
        for field, expected in query.items():
            if field == "_id":
                continue
            actual = document.get(field)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    def update_one(self, query, update, upsert=False):
        key = query["_id"]
        document = self.documents.get(key)

        if document is None:
            if not upsert:
                return FakeResult()
            new = dict(update.get("$setOnInsert") or {})
            new.update(update.get("$set") or {})
            new["_id"] = key
            for field, delta in (update.get("$inc") or {}).items():
                new[field] = new.get(field, 0) + delta
            self.documents[key] = new
            return FakeResult(upserted_id=key)

        if not self._matches(document, query):
            return FakeResult()

        for field, value in (update.get("$set") or {}).items():
            document[field] = value
        for field, delta in (update.get("$inc") or {}).items():
            document[field] = (document.get(field) or 0) + delta
        return FakeResult(modified_count=1)


@pytest.fixture
def reminder_store(monkeypatch):
    """
    An empty, in-memory reminder log.

    This used to be a `state_file` fixture pointing at a temporary JSON file.
    The store moved to MongoDB (see B2 -- the file could not be written safely
    by two processes), so the seam moved with it: patching
    `reminder_log._collection` keeps every test off both the real database and
    the real dedupe log.
    """
    from backend.db import reminder_log

    fake = FakeCollection()
    monkeypatch.setattr(reminder_log, "_collection", lambda: fake)
    return fake


@pytest.fixture
def fixed_secret(monkeypatch):
    """A stable signing key, so token tests never touch Mongo."""
    from backend.db import store

    monkeypatch.setattr(store, "get_app_secret",
                        lambda: "test-secret-not-a-real-key")
    return "test-secret-not-a-real-key"


@pytest.fixture
def client():
    """The Flask app with sessions switched off, for guard-shape tests."""
    from backend.web import server

    server.app.config["TESTING"] = True
    server.app.config["REVIEW_ONLY"] = False
    return server.app.test_client()
