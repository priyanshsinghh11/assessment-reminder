"""
Where a derived grid lives.

THE FAILURE THIS EXISTS FOR. derive_grid() used to write the grid it derived
to assessments/grid-<slug>.json. On Vercel that directory is read-only, so a
Grade click on any role without a grid paid for a derivation and then died
with a 500 writing it; on a GitHub runner the file was thrown away with the
runner, so every hourly run derived the role's bar again. A derived grid now
goes to Mongo, and a committed file is only ever read.
"""

import json
from pathlib import Path

import pytest

from backend.grading import evaluator

ROOT = Path(__file__).resolve().parents[1]
VALID_GRID = json.loads(
    (ROOT / "assessments" / "grid-ai-delivery-lead.json").read_text(encoding="utf-8"))
ROLE = {"_id": 99, "slug": "not-in-the-pack", "title": "Test Role",
        "assessment": {"name": "Test", "markdown": "Do the task."}}


@pytest.fixture
def stored(monkeypatch, tmp_path):
    """An empty grids store, and an assessments dir nothing is committed to."""
    grids = {}
    monkeypatch.setattr(evaluator, "ASSESSMENT_DIR", tmp_path)
    monkeypatch.setattr(evaluator.store, "get_derived_grid",
                        lambda slug: json.loads(json.dumps(grids[slug]))
                        if slug in grids else None)
    monkeypatch.setattr(evaluator.store, "save_derived_grid",
                        lambda slug, grid: grids.__setitem__(slug, grid))
    return grids


def test_nothing_derived_and_nothing_committed_is_none(stored):
    assert evaluator.load_derived_grid(ROLE) is None


def test_a_stored_grid_is_read_back(stored):
    stored[ROLE["slug"]] = VALID_GRID
    grid = evaluator.load_derived_grid(ROLE)
    assert grid["criteria"] == VALID_GRID["criteria"]
    assert grid["source"] == "derived"


def test_a_committed_file_wins_over_the_stored_grid(stored, tmp_path):
    stored[ROLE["slug"]] = {"not": "a grid"}
    evaluator.grid_path(ROLE["slug"]).write_text(json.dumps(VALID_GRID),
                                                 encoding="utf-8")
    assert evaluator.load_derived_grid(ROLE)["criteria"] == VALID_GRID["criteria"]


def test_an_unusable_stored_grid_is_an_evaluation_failure(stored):
    stored[ROLE["slug"]] = {"not": "a grid"}
    with pytest.raises(evaluator.EvaluationFailed, match="stored grid"):
        evaluator.load_derived_grid(ROLE)


def test_deriving_stores_the_grid_and_writes_no_file(stored, tmp_path,
                                                     monkeypatch):
    calls = []
    monkeypatch.setattr(evaluator, "_chat",
                        lambda *a, **k: calls.append(1) or "{}")
    monkeypatch.setattr(evaluator, "_clean_derived",
                        lambda raw, role, name: dict(VALID_GRID, repairs=[]))

    grid = evaluator.derive_grid(ROLE)

    assert ROLE["slug"] in stored
    assert stored[ROLE["slug"]]["derived_by"] == evaluator.LLM_MODEL
    assert grid["source"] == "derived"
    assert list(tmp_path.iterdir()) == []

    # Derived once: the next caller -- another process, another run -- reads
    # the stored grid instead of paying for a second one.
    evaluator.derive_grid(ROLE)
    assert len(calls) == 1


def test_a_derived_grid_survives_bson(monkeypatch):
    # The mock above round-trips through JSON, which turns the anchors' int
    # levels into strings without a word. BSON refuses them instead -- the
    # 500 a Grade click on Vercel died with -- so this goes through BSON.
    import bson
    from types import SimpleNamespace
    from backend.db import store

    docs = {}
    grids = SimpleNamespace(
        replace_one=lambda query, doc, upsert: docs.__setitem__(
            query["_id"], bson.encode(doc)),
        find_one=lambda query, projection: bson.decode(docs[query["_id"]])
        if query["_id"] in docs else None,
    )
    monkeypatch.setattr(store, "get_db", lambda: SimpleNamespace(grids=grids))

    grid = {"criteria": [{"key": "k", "anchors": {5: "five", 3: "three", 1: "one"}}],
            "derived_by": "model"}
    store.save_derived_grid("slug", grid)

    assert store.get_derived_grid("slug")["criteria"] == grid["criteria"]
    assert store.get_derived_grid("missing") is None
