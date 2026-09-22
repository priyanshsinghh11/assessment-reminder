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


# ---------------------------------------------------------------------------
# Submission 9692's folder, exactly as Drive serves it
#
# Two .mp4 files of 12.1 MB and 13.8 MB, a Google Doc and a .pptx. Everything
# below is a bug that folder exposed.
# ---------------------------------------------------------------------------

VIDEO_A = "1xJQmeFZ_380sagKXPb4DPh1Ea9OgVdJP"
VIDEO_B = "1bgl5MgclIrkmHtWqxTJwfahL6A4zYV0m"
DOC = "1Ro7NNGMXwfNAcc-H8rOuzICo0JQ8ucBm3BEmQHEAE0k"
DECK = "1RMBx6dTXo8Io17i10hEdszsKeEAjbrLv"

REAL_FOLDER_HTML = (
    "<html><body>"
    + _row(VIDEO_A, "ajaia.ai Candidate responses.mp4")
    + _row(VIDEO_B, "ajaia.ai Strategy Presentation.mp4")
    + _row(DOC, "Markdown Google Docs")
    + _row(DECK, "Westbrook_90_Day_AI_Strategy.pptx")
    # Drive's script blobs repeat every id as a bare download URL, with no
    # filename attached -- this is the path that re-queued the videos.
    + "<script>"
    + " ".join(f"https://drive.google.com/uc?export=download&id={i}"
               for i in (VIDEO_A, VIDEO_B, DOC, DECK))
    + ' https://drive.google.com/?tab=oo'
    + "</script></body></html>"
).encode()


def test_videos_are_not_queued_even_though_drive_repeats_their_ids():
    """
    The 12 MB and 13.8 MB downloads that produced two fetch_timeouts.

    Skipping them in the item-row loop was never enough: the regex sweep read
    the same ids back out of Drive's script blobs, where no filename is
    attached to tell a deck from a recording.
    """
    links = list(submission_reader._html_links(REAL_FOLDER_HTML, FOLDER))
    ids = {submission_reader.file_id_of(u) for u in links}

    assert VIDEO_A not in ids
    assert VIDEO_B not in ids
    assert DECK in ids and DOC in ids


def test_drive_navigation_is_not_queued():
    links = list(submission_reader._html_links(REAL_FOLDER_HTML, FOLDER))
    assert not any("tab=oo" in u for u in links)


def test_one_document_reached_by_two_urls_is_fetched_once():
    """
    The doc arrived as /edit and as uc?export=download.

    The second attempt returned HTTP 500 and was recorded as a failure to read
    a document already sitting in `parts` -- which then counted toward the
    "some of their links could not be read" note the grader is shown.
    """
    edit = f"https://docs.google.com/document/d/{DOC}/edit"
    download = f"https://drive.google.com/uc?export=download&id={DOC}"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "uc?export=download" in url:
            return b"", "", "http_500"
        return b"%PDF-doc", "application/pdf", ""

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="The current-state map " * 50):
        result = submission_reader.read_submission(f"{edit} and {download}")

    assert len(calls) == 1, f"fetched the same document twice: {calls}"
    assert result["errors"] == []
    assert "current-state map" in result["text"]


def test_an_avatar_png_is_never_stored_as_the_candidates_writing():
    """440 characters beginning \x89PNG sat in 9692's submission text."""
    png = (b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4)
    avatar = "https://lh3.googleusercontent.com/ogw/default-user=s83"

    with patch.object(submission_reader, "_fetch",
                      return_value=(png, "image/png", "")):
        result = submission_reader.read_submission(avatar)

    assert result["text"] == ""
    assert result["sources"] == []
    assert any("not_a_document" in e for e in result["errors"])


def test_the_whole_folder_reads_as_deck_plus_document_and_two_videos():
    """End to end on 9692's real folder shape."""
    def fake_fetch(url):
        if "/folders/" in url:
            return REAL_FOLDER_HTML, "text/html", ""
        if DECK in url:
            return b"%PDF-deck", "application/pdf", ""
        if DOC in url:
            return b"%PDF-doc", "application/pdf", ""
        raise AssertionError(f"should not have fetched {url}")

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="Authorization-ready intake " * 50):
        result = submission_reader.read_submission(FOLDER)

    assert result["media"] == ["ajaia.ai Candidate responses.mp4",
                               "ajaia.ai Strategy Presentation.mp4"]
    assert result["errors"] == []
    assert len(result["sources"]) == 2      # the doc and the deck, not the folder
    assert "Authorization-ready intake" in result["text"]
    assert "Skip to main content" not in result["text"]


# ---------------------------------------------------------------------------
# Recognising a recording from the response, not from Drive's HTML
#
# A signed-out folder does not serve the data-id/aria-label rows
# `_folder_entries` reads; the ids come out of script blobs with no filename.
# On the live folder that left the filename check finding nothing and both
# videos queued anyway.
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_media_is_named_from_content_disposition():
    response = FakeResponse({
        "Content-Type": "video/mp4",
        "Content-Disposition":
            'attachment; filename="ajaia.ai Strategy Presentation.mp4"'})
    assert (submission_reader._media_name(response)
            == "ajaia.ai Strategy Presentation.mp4")


def test_media_falls_back_to_the_content_type():
    response = FakeResponse({"Content-Type": "video/mp4"})
    assert submission_reader._media_name(response) == "video/mp4"


def test_a_document_is_not_mistaken_for_media():
    response = FakeResponse({
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="deck.pdf"'})
    assert submission_reader._media_name(response) == ""


def test_a_recording_is_recorded_not_downloaded_or_reported_as_an_error():
    """The two fetch_timeouts that cost 24 seconds of a grading call."""
    def fake_fetch(url):
        return b"", "video/mp4", submission_reader.MEDIA_MARKER + "demo.mp4"

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch):
        result = submission_reader.read_submission(
            f"https://drive.google.com/uc?export=download&id={VIDEO_A}")

    assert result["media"] == ["demo.mp4"]
    assert result["errors"] == []
    assert result["text"] == ""


# ---------------------------------------------------------------------------
# Furniture: an allowlist, because the denylist kept being wrong
# ---------------------------------------------------------------------------

def test_drive_urls_with_no_file_behind_them_are_never_fetched():
    """Each of these cost a fetch whose failure reached the grader."""
    for url in ("https://drive.google.com/?tab=oo",
                "https://drive.google.com/viewer/main",
                "https://drive.google.com/video/captions/edit",
                "https://drive.google.com/drive?authuser",
                "https://drive.google.com/picker",
                "https://lh3.googleusercontent.com",
                "https://lh3.googleusercontent.com/ogw/default-user=s83",
                "https://drive.usercontent.google.com"):
        assert submission_reader._is_drive_furniture(url), url


def test_real_documents_and_folders_are_not_furniture():
    for url in (f"https://docs.google.com/document/d/{DOC}/edit",
                f"https://drive.google.com/uc?export=download&id={DECK}",
                FOLDER):
        assert not submission_reader._is_drive_furniture(url), url


def test_a_file_read_under_one_url_is_not_reported_failed_under_another():
    """
    9692's doc was fetched as a download (HTTP 500) and as /edit (fine).

    The 500 was reported, so the grader was told some of the candidate's links
    could not be read about a document it had in full.
    """
    download = f"https://drive.google.com/uc?export=download&id={DOC}"
    edit = f"https://docs.google.com/document/d/{DOC}/edit"

    def fake_fetch(url):
        if "uc?export=download" in url:
            return b"", "", "http_500"
        return b"%PDF-doc", "application/pdf", ""

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="Ranking basis " * 50):
        result = submission_reader.read_submission(f"{download} then {edit}")

    assert result["errors"] == []
    assert "Ranking basis" in result["text"]


def test_a_file_that_never_succeeded_is_still_reported():
    doc = f"https://docs.google.com/document/d/{DOC}/edit"
    with patch.object(submission_reader, "_fetch",
                      return_value=(b"", "", "http_403")):
        result = submission_reader.read_submission(doc)
    assert len(result["errors"]) == 1 and "http_403" in result["errors"][0]
