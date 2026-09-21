from unittest.mock import patch

from backend.scraping import submission_reader


def test_extract_links_only_allows_public_document_hosts():
    text = (
        "work [here](https://drive.google.com/drive/folders/abc) "
        "and https://docs.google.com/document/d/12345678901234567890/edit. "
        "Ignore https://example.com/private.pdf and http://drive.google.com/x"
    )
    links = submission_reader.extract_links(text)
    assert links == [
        "https://drive.google.com/drive/folders/abc",
        "https://docs.google.com/document/d/12345678901234567890/edit",
    ]


def test_folder_html_is_crawled_for_child_document(monkeypatch):
    folder = "https://drive.google.com/drive/folders/folder"
    doc = "https://docs.google.com/document/d/12345678901234567890/edit"
    html = f'<html><a href="{doc}">submission</a></html>'.encode()
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "folders" in url:
            return html, "text/html", ""
        return b"%PDF-child", "application/pdf", ""

    with patch.object(submission_reader, "_fetch", side_effect=fake_fetch), \
            patch("backend.scraping.submission_reader.resume_reader.extract",
                  return_value="A detailed candidate submission " * 30):
        result = submission_reader.read_submission(folder)

    assert doc in result["sources"]
    assert "A detailed candidate submission" in result["text"]
    assert len(calls) == 2

