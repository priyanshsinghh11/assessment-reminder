"""
The spotlight route: the payload it builds, and the two rules that are easy to
break without anything on screen looking wrong.

WHY THE RULES ARE CHECKED HERE. `_spotlight_row` builds its rows by hand and
they never pass through `_project()`, so the manager score rule -- the one
`MANAGER_SUBMISSION_FIELDS` and `MANAGER_DASHBOARD_SCORES` own everywhere else
on this page -- is restated in this one function. A restated rule drifts. The
same goes for the school panel being narrowed to the New York seats: on a
dashboard with no New York role, the correct output and the broken output both
look like an empty panel.

No database. Every store call the route makes is replaced with a list, which
is also a compact statement of exactly which ones it makes.
"""

import pytest

from backend.grading import pedigree
from backend.web import app as web_app
from backend.web import views_evaluations as views


# The portal slug the pack's two AI Strategist grids are filed under, both of
# which read "New York, on-site". Named here rather than inlined so the day
# that posting closes, this file fails with a clear reason instead of two
# mysteriously empty panels.
NY_SLUG = "ai-strategist"
REMOTE_SLUG = "zz-not-in-the-pack"


def submission(sub_id, job_id, name, employers=(), schools=(), score=None,
               stage=None):
    return {
        "_id": sub_id,
        "job_id": job_id,
        "job_title": "Stored portal title",
        "candidate_name": name,
        "candidate_email": f"{name.lower()}@example.com",
        "candidate_headline": "Head of Things",
        "candidate_location": "Brooklyn, NY",
        "resume_link": "https://example.com/cv",
        "submitted_at": "2026-09-01",
        "decision": {"status": "scored"},
        "pipeline": {"stage": stage} if stage else {},
        "evaluation": {"score": score} if score is not None else None,
        "pedigree": {
            "version": pedigree.VERSION,
            "employers": [{"name": e, "category": "Banking", "source": "record"}
                          for e in employers],
            "schools": [{"name": s, "category": "Ivy+", "source": "record"}
                        for s in schools],
        },
    }


@pytest.fixture
def spotlight(monkeypatch, client):
    """
    Two roles -- one a New York seat, one not -- and four candidates on them.

    Auth is forced off so the route answers as an admin; the manager side of it
    is `_scope()`, which is the same function every other route on this page
    goes through and is covered against a real database by test_access.py.
    """
    roles = [
        {"_id": 1, "title": "AI Strategist", "slug": NY_SLUG},
        {"_id": 2, "title": "Remote Engineer", "slug": REMOTE_SLUG},
    ]
    rows = [
        submission(10, 1, "Ada", employers=["Goldman Sachs"],
                   schools=["Princeton"], score=82),
        submission(11, 1, "Bo", schools=["Yale"], score=71),
        submission(12, 2, "Cy", employers=["McKinsey"], schools=["Harvard"],
                   score=64),
        submission(13, 1, "Di", employers=["Meta"], score=90, stage="interview"),
    ]

    monkeypatch.setattr(web_app, "AUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(views.store, "ping", lambda: None)
    # role_index, not get_roles: the route takes the light read, which returns
    # the id, title and slug and leaves the assessment markdown in the database.
    monkeypatch.setattr(views.store, "role_index", lambda job_ids=None: roles)
    monkeypatch.setattr(views.store, "pedigree_unread",
                        lambda version, job_ids=None, limit=0: [])
    monkeypatch.setattr(views.store, "count_pedigree_unread",
                        lambda version, job_ids=None: 0)
    monkeypatch.setattr(views.store, "pedigree_pool",
                        lambda version, job_ids=None: rows)
    return client


def get(client):
    response = client.get("/api/evaluations/spotlight")
    assert response.status_code == 200
    return response.get_json()


def test_the_employer_panel_spans_every_role(spotlight):
    data = get(spotlight)
    assert {row["name"] for row in data["employers"]} == {"Ada", "Cy", "Di"}


def test_the_school_panel_is_the_new_york_seats_only(spotlight):
    """
    Cy went to Harvard but applied to a role with no New York grid behind it.
    Where somebody studied is only in front of a reader here because the seat
    needs them in the building; on a remote role it would be prestige for its
    own sake.
    """
    data = get(spotlight)
    assert {row["name"] for row in data["schools"]} == {"Ada", "Bo"}
    assert all(row["seat"] == "onsite" for row in data["schools"])


def test_the_new_york_seats_are_named(spotlight):
    data = get(spotlight)
    assert data["new_york_roles"] == [
        {"id": 1, "title": "AI Strategist", "seat": "onsite"}]


def test_rows_carry_the_display_title_not_the_portal_one(spotlight):
    """
    ROLE_TITLES renames some roles for the screen. A spotlight row filed under
    a name that appears nowhere else on the dashboard is a row nobody can match
    to the card it came from.
    """
    data = get(spotlight)
    assert {row["job_title"] for row in data["employers"]} \
        == {"AI Strategist", "Remote Engineer"}


def test_the_waiting_count_leaves_out_anyone_already_moved(spotlight):
    """Di is booked for an interview, so she is listed but is not waiting."""
    data = get(spotlight)
    assert len(data["employers"]) == 3
    assert data["waiting"]["employers"] == 2


def test_the_score_travels_when_the_setting_allows_it(spotlight):
    data = get(spotlight)
    assert data["scores_visible"] is True
    assert {row["name"]: (row["evaluation"] or {}).get("score")
            for row in data["employers"]} == {"Ada": 82, "Cy": 64, "Di": 90}


def test_a_manager_who_may_not_read_scores_gets_no_evaluation(spotlight,
                                                              monkeypatch):
    """
    MANAGER_DASHBOARD_SCORES=0 with a non-admin caller. The number must be
    absent from the PAYLOAD, not merely undrawn -- a manager reading the JSON
    directly sees exactly what their screen does.
    """
    monkeypatch.setattr(views, "MANAGER_DASHBOARD_SCORES", False)
    monkeypatch.setattr(views, "_is_admin", lambda: False)
    data = get(spotlight)
    assert data["scores_visible"] is False
    assert all(row["evaluation"] is None for row in data["employers"])


def test_a_candidate_with_neither_is_not_in_either_panel(spotlight,
                                                         monkeypatch):
    monkeypatch.setattr(views.store, "pedigree_pool",
                        lambda version, job_ids=None: [submission(20, 1, "Ed")])
    data = get(spotlight)
    assert data["employers"] == [] and data["schools"] == []


def test_an_unread_batch_is_scanned_and_reported(spotlight, monkeypatch):
    """
    The scan runs inside the request, bounded, and says what it has left. A
    silent partial list is the one outcome this must not produce.
    """
    # A whole little CV rather than one line: employers are read from the
    # work-experience section and from nowhere else, so a bare line is
    # correctly worth nothing. See tests/test_pedigree.py.
    unread = [{"_id": 99, "resume_text": "WORK EXPERIENCE\n"
                                         "Goldman Sachs | Analyst | 2019 - 2023\n"
                                         "SKILLS\nPython"}]
    written = {}
    monkeypatch.setattr(views.store, "pedigree_unread",
                        lambda version, job_ids=None, limit=0: unread)
    monkeypatch.setattr(views.store, "set_pedigree_many", written.update)
    data = get(spotlight)
    assert written[99]["employers"][0]["name"] == "Goldman Sachs"
    assert data["scanned"] == 1
    # Short of the batch size, so nothing is left and no second query was made.
    assert data["pending_scan"] == 0
