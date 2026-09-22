"""
The candidate who puts the work in a Drive folder instead of the answer box.

Every test here is one step of the same journey, because the failure was
spread across three stages and fixing any one of them alone still lost the
candidate. They paste a folder link and two lines of covering note, so: the
portal's `video_link` is empty and screening used to delete the record; the
crawler used to skip past the folder's subdirectories and ignore the .mp4 it
found; and the grader used to be handed an eight-word answer, told the video
was "genuinely absent", and asked to mark a rubric against it.

The through-line is that an empty artefact FIELD is not an absent artefact,
and a short answer BOX is not thin work. These pin both.
"""

from unittest.mock import patch

import pytest

from backend.grading import evaluator
from backend.pipeline import ingest
from backend.scraping import submission_reader


FOLDER = "https://drive.google.com/drive/folders/candidate-work"
DOC_ID = "12345678901234567890abc"
SUBFOLDER_ID = "09876543210987654321xyz"


def _row(file_id, label):
    """One Drive folder row, shaped the way Drive serialises them."""
    return f'<div aria-label="{label}"><div data-id="{file_id}"></div></div>'


# ---------------------------------------------------------------------------
# The crawl
# ---------------------------------------------------------------------------

def test_recording_in_the_folder_is_reported_not_silently_skipped():
    html = (f"<html>{_row(DOC_ID, 'Approach.pdf')}"
            f"{_row('aaaaaaaaaaaaaaaaaaaaaaa', 'walkthrough.mp4')}</html>"
            ).encode()

    def fake_fetch(url):
        if "folders" in url:
            return html, "text/html", ""
        return b"%PDF-child", "application/pdf", ""

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="A detailed candidate submission " * 30):
        result = submission_reader.read_submission(f"my work: {FOLDER}")

    # The video is named but never fetched: it is evidence that a recording
    # exists, not evidence of what is in it.
    assert result["media"] == ["walkthrough.mp4"]
    assert not any("walkthrough" in s for s in result["sources"])
    assert "A detailed candidate submission" in result["text"]


def test_subfolders_are_crawled_one_level_deeper():
    """The realistic shape: a share link over /docs, /code, /video."""
    root = f"<html>{_row(SUBFOLDER_ID, 'docs')}</html>".encode()
    sub = f"<html>{_row(DOC_ID, 'Approach.pdf')}</html>".encode()

    def fake_fetch(url):
        if SUBFOLDER_ID in url:
            return sub, "text/html", ""
        if "folders" in url:
            return root, "text/html", ""
        return b"%PDF-child", "application/pdf", ""

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="Nested submission body " * 40):
        result = submission_reader.read_submission(FOLDER)

    assert "Nested submission body" in result["text"], (
        "work one subfolder down must still reach the grader")


def test_media_names_are_deduplicated_and_case_insensitive():
    html = (f"<html>{_row('aaaaaaaaaaaaaaaaaaaaaaa', 'Demo.MP4')}"
            f"{_row('aaaaaaaaaaaaaaaaaaaaaaa', 'Demo.MP4')}</html>").encode()
    assert submission_reader.media_names(html) == ["Demo.MP4"]


# ---------------------------------------------------------------------------
# What the grader is told
# ---------------------------------------------------------------------------

def test_video_in_the_folder_does_not_count_as_a_missing_artefact():
    submission = {"video_link": "", "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": ["walkthrough.mp4"]}
    assert evaluator.missing_artefacts(submission) == ()


def test_video_is_still_missing_when_the_folder_held_no_recording():
    submission = {"video_link": "", "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": []}
    assert evaluator.missing_artefacts(submission) == ("video_link",)


def test_artefact_block_tells_the_model_the_video_was_submitted():
    grid = {"criteria": [
        {"key": "delivery", "label": "Delivery", "block": "work", "weight": 20,
         "anchors": {1: "No video walkthrough at all."}},
    ]}
    submission = {"video_link": "", "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": ["walkthrough.mp4"]}

    block = evaluator._artefact_block(submission, grid)

    assert "NOT SUBMITTED" not in block.split("Resume:")[0]
    assert "walkthrough.mp4" in block
    # The instruction that used to force the delivery row to 1 must be gone,
    # and the one that stops the model inventing the video must be present.
    assert "genuinely absent" not in block
    assert "do not describe what the recording shows" in block.lower()


def test_word_count_covers_the_linked_documents():
    phrase = evaluator.submission_word_count(
        "see the folder", "word " * 2000)
    assert "2,003 words in total" in phrase
    assert "3 in the portal answer box" in phrase


def test_word_count_stays_a_plain_number_when_nothing_was_linked():
    assert evaluator.submission_word_count("one two three") == "3 words"


# ---------------------------------------------------------------------------
# The three states of the linked-documents section
# ---------------------------------------------------------------------------

def test_linked_rule_says_nothing_was_linked():
    rule = evaluator._linked_rule([], "", [])
    assert "costs them nothing" in rule


def test_linked_rule_marks_fetched_work_as_the_submission():
    rule = evaluator._linked_rule([FOLDER], "the work", [])
    assert "This is their work: mark it." in rule


def test_unreadable_links_are_our_failure_and_never_the_candidates():
    """The regression that started this: a private folder scored as no work."""
    rule = evaluator._linked_rule([FOLDER], "", ["403"])
    lowered = rule.lower()
    assert "do not invent the contents" in lowered
    assert "do not mark a criterion low" in lowered
    assert "do not trip an" in lowered or "auto-fail" in lowered
    assert "our gap" in lowered


def test_partial_fetch_failure_does_not_taint_what_was_read():
    rule = evaluator._linked_rule([FOLDER, "b"], "the work", ["403"])
    assert "mark it" in rule
    assert "do not treat the unread ones as empty" in rule


# ---------------------------------------------------------------------------
# Screening -- the stage that used to delete the record
# ---------------------------------------------------------------------------

@pytest.fixture
def decisions(monkeypatch):
    """Capture set_decision calls instead of writing to Mongo."""
    written = []
    monkeypatch.setattr(ingest.store, "set_decision",
                        lambda sid, status, reason, source:
                        written.append((sid, status, reason)))
    monkeypatch.setattr(ingest.store, "purge_auto_rejected", lambda: 0)
    return written


def _submission(**over):
    base = {"_id": 1, "job_id": 7, "submission_status": "submitted",
            "decision": {}, "video_link": "", "resume_link": "",
            "submission_links": []}
    base.update(over)
    return base


def test_submission_with_linked_work_is_graded_not_auto_rejected(
        decisions, monkeypatch):
    monkeypatch.setattr(
        ingest.store, "list_submissions",
        lambda **kw: [_submission(submission_links=[FOLDER])])
    monkeypatch.setattr(ingest, "required_artefacts_for",
                        lambda job_id: ("video_link", "resume_link"))

    counts = ingest.apply_auto_rejections()

    assert counts["rejected"] == 0
    assert counts["pending"] == 1
    assert decisions == [(1, "pending", "awaiting_evaluation")]


def test_submission_with_no_links_and_no_artefacts_is_still_rejected(
        decisions, monkeypatch):
    monkeypatch.setattr(ingest.store, "list_submissions",
                        lambda **kw: [_submission()])
    monkeypatch.setattr(ingest, "required_artefacts_for",
                        lambda job_id: ("video_link", "resume_link"))

    counts = ingest.apply_auto_rejections()

    assert counts["rejected"] == 1
    assert decisions == [(1, "rejected", "missing_video_and_resume")]


def test_parse_row_records_the_links_screening_will_need():
    row = {"submission_id": "42", "job_id": "7",
           "candidate_email": "A@B.com",
           "submission_markdown": f"Everything is here: {FOLDER}"}
    rec = ingest._parse_row(row)
    assert rec["submission_links"] == [FOLDER]


# ---------------------------------------------------------------------------
# The recording rule must stay about the recording
# ---------------------------------------------------------------------------

VIDEO_GRID = {"criteria": [
    {"key": "delivery", "label": "Delivery", "block": "work", "weight": 40,
     "anchors": {1: "No video walkthrough at all.", 5: "Clear walkthrough."}},
    {"key": "analysis", "label": "Analysis", "block": "work", "weight": 60,
     "anchors": {1: "No analysis.", 5: "Rigorous analysis."}},
]}


def test_missing_resume_is_never_charged_to_a_recording_criterion():
    """
    A resume-only absence must not trip the video rule.

    `video_dependent_criteria` matches 1 anchors that name a recording, so
    applied to the resume it told the model to mark the delivery row 1 because
    a CV was missing -- a work-sample penalty for an artefact the grid does
    not score. Resolving the video from the linked folder makes resume-only
    the common case, so this is the shape most candidates now hit.
    """
    submission = {"video_link": "", "resume_link": "",
                  "linked_submission_media": ["walkthrough.mp4"]}

    block = evaluator._artefact_block(submission, VIDEO_GRID)

    assert "prices the resume in" not in block
    assert "The missing resume costs NOTHING in the grid above" in block
    # And the video rule is absent entirely, because the video is not missing.
    assert "prices the video in" not in block


def test_missing_video_still_prices_the_recording_row():
    """The original behaviour, unchanged for a candidate who really has none."""
    submission = {"video_link": "", "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": []}

    block = evaluator._artefact_block(submission, VIDEO_GRID)

    assert "prices the video in: [delivery] Delivery" in block
    assert "so mark it 1" in block


# ---------------------------------------------------------------------------
# Backfill: rows crawled before the crawler looked for recordings
# ---------------------------------------------------------------------------

def test_row_crawled_before_media_existed_is_recrawled_once(monkeypatch):
    """
    Otherwise every already-graded candidate keeps the penalty.

    These rows satisfy every other "already done" test in
    `ensure_linked_submission`, so nothing would ever fetch them again, and an
    absent media field would read as "no video in the folder" forever.
    """
    from backend.grading import grader

    calls = []
    monkeypatch.setattr(
        grader.submission_reader, "read_submission",
        lambda md: calls.append(md) or {
            "text": "work", "sources": [FOLDER], "errors": [],
            "links": [FOLDER], "media": ["walkthrough.mp4"]})

    stale = {"_id": 1, "submission_markdown": f"work: {FOLDER}",
             "linked_submission_links": [FOLDER],
             "linked_submission_text": "work",
             "linked_submission_errors": [],
             "linked_submission_fetched_at": "2026-01-01"}

    grader.ensure_linked_submission(stale, persist=False)

    assert calls, "a row with no media field must be crawled again"
    assert stale["linked_submission_media"] == ["walkthrough.mp4"]


def test_row_already_crawled_for_media_is_left_alone(monkeypatch):
    from backend.grading import grader

    calls = []
    monkeypatch.setattr(grader.submission_reader, "read_submission",
                        lambda md: calls.append(md) or {})

    fresh = {"_id": 1, "submission_markdown": f"work: {FOLDER}",
             "linked_submission_links": [FOLDER],
             "linked_submission_errors": [],
             "linked_submission_media": [],
             "linked_submission_fetched_at": "2026-01-01"}

    grader.ensure_linked_submission(fresh, persist=False)

    assert not calls, "an empty media list is an answer, not a gap"


# ---------------------------------------------------------------------------
# A folder page is navigation, not the candidate's writing
# ---------------------------------------------------------------------------

def test_drive_folder_chrome_is_never_stored_as_submission_text():
    """
    Submission 9692 stored 13k characters of Drive's sign-in page as work.

    A signed-out folder renders its banner, shortcut help and column headers
    as visible text, which clears the 200-character floor many times over and
    reaches the grader under a SOURCE header. The grader finds no assessment
    in it and marks every work-product row 1.
    """
    chrome = ("Skip to main content Keyboard shortcuts Accessibility feedback "
              "This browser version is no longer supported. Sign in Drive "
              "Name Date modified File size Sort by Folders On top " * 12)
    html = f"<html><body>{chrome}</body></html>".encode()

    with patch.object(submission_reader, "_fetch",
                      return_value=(html, "text/html", "")):
        result = submission_reader.read_submission(FOLDER)

    assert len(chrome) > 2000, "fixture must clear the old length floor"
    assert result["text"] == ""
    assert result["sources"] == []


def test_real_document_text_is_still_kept():
    """The guard is about folder URLs, not about HTML in general."""
    doc = "https://docs.google.com/document/d/12345678901234567890/edit"
    body = ("The current-state map runs from intake to attestation. " * 40)
    html = f"<html><body>{body}</body></html>".encode()

    with patch.object(submission_reader, "_fetch",
                      return_value=(html, "text/html", "")):
        result = submission_reader.read_submission(doc)

    assert "current-state map" in result["text"]
    assert result["sources"] == [doc]


def test_media_note_does_not_claim_an_empty_field_when_one_is_set():
    """
    9692 pasted the FOLDER into `video_link`, so the field is populated.

    The note must not tell the model the video field is empty about a record
    where it plainly is not -- this is the section the model is instructed to
    trust over its own reading.
    """
    submission = {"video_link": FOLDER, "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": ["responses.mp4"]}

    block = evaluator._artefact_block(submission, VIDEO_GRID)

    assert "video field is empty" not in block
    assert "points at the folder rather than at the file itself" in block
    assert "Treat the video as submitted" in block


def test_media_note_still_explains_a_genuinely_empty_field():
    submission = {"video_link": "", "resume_link": "https://x/cv.pdf",
                  "linked_submission_media": ["responses.mp4"]}

    block = evaluator._artefact_block(submission, VIDEO_GRID)

    assert "video field is empty" in block
