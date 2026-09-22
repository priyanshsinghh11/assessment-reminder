"""Fetch public work documents linked from a candidate submission.

Candidates sometimes put the actual assessment in a public Drive folder and
leave only a short note in ``submission_markdown``.  The grader must see that
work, but following arbitrary URLs from candidate text would be an SSRF risk
and would make grading unpredictable.  This module therefore crawls only
known document hosts, with small depth, count, byte and text limits.
"""

from __future__ import annotations

import re
import time
from html import unescape
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from backend.scraping import resume_reader

MAX_LINKS = 8
MAX_DOCUMENT_CHARS = 50_000
MAX_TOTAL_CHARS = 120_000
MAX_CRAWL_DEPTH = 1
FETCH_TIMEOUT = 12

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
_ALLOWED_HOSTS = {
    "drive.google.com", "docs.google.com", "dropbox.com", "www.dropbox.com",
}
_MEDIA_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mp3", ".wav")


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().split(":", 1)[0]


def _allowed(url: str) -> bool:
    host = _host(url)
    content_host = host.endswith(".googleusercontent.com") or host == "drive.usercontent.google.com"
    return (host in _ALLOWED_HOSTS or content_host) and urlparse(url).scheme == "https"


def extract_links(markdown: str) -> list[str]:
    """Return de-duplicated, safe public document links from submission text."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(markdown or ""):
        url = unescape(raw).rstrip(".,;:!?\n\r")
        if not _allowed(url) or "{" in url or "}" in url:
            continue
        # Do not let a tracking fragment create another fetch of the same file.
        parsed = urlparse(url)
        normal = parsed._replace(fragment="").geturl()
        if normal not in seen:
            seen.add(normal)
            found.append(normal)
    return found[:MAX_LINKS]


def _html_links(data: bytes, base_url: str) -> Iterable[str]:
    """Find Drive/Docs links in both anchors and serialized folder HTML."""
    soup = BeautifulSoup(data, "html.parser")
    candidates = [a.get("href", "") for a in soup.find_all("a")]
    # Drive's folder page renders files as rows with a data-id, not anchors.
    # The aria label also tells us whether the item is a native Google Doc or
    # an uploaded file (pptx, mp4, etc.).
    for node in soup.select("[data-id]"):
        file_id = node.get("data-id", "")
        owner = node.find_parent(attrs={"aria-label": True})
        label = node.get("aria-label", "") or (owner or {}).get("aria-label", "")
        filename = label.split(" Google ", 1)[0].lower()
        if filename.endswith(_MEDIA_SUFFIXES):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", file_id):
            continue
        if "Google Docs" in label:
            candidates.append(
                f"https://docs.google.com/document/d/{file_id}/edit"
            )
        elif "Google Sheets" in label:
            candidates.append(
                f"https://drive.google.com/uc?export=download&id={file_id}"
            )
        else:
            candidates.append(
                f"https://drive.google.com/uc?export=download&id={file_id}"
            )
    # Drive often puts file metadata in a script blob rather than an anchor.
    candidates.extend(_URL_RE.findall(data.decode("utf-8", "ignore")))
    for candidate in candidates:
        if not candidate:
            continue
        url = urljoin(base_url, unescape(candidate)).rstrip(".,;:!?\"'")
        if _allowed(url) and "{" not in url and "}" not in url:
            yield urlparse(url)._replace(fragment="").geturl()


def _visible_html(data: bytes) -> str:
    soup = BeautifulSoup(data, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                         if line.strip())


def _fetch(url: str) -> tuple[bytes, str, str]:
    """Fetch one allowed URL with a hard response-size limit."""
    target = resume_reader.direct_url(url)
    deadline = time.monotonic() + FETCH_TIMEOUT
    try:
        with requests.get(
            target,
            timeout=FETCH_TIMEOUT,
            stream=True,
            allow_redirects=True,
            headers={"User-Agent": resume_reader.USER_AGENT},
        ) as response:
            if response.status_code != 200:
                return b"", "", f"http_{response.status_code}"
            if not _allowed(response.url):
                return b"", "", "redirected_to_disallowed_host"
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=1 << 16):
                if time.monotonic() > deadline:
                    return b"", "", "fetch_timeout"
                size += len(chunk)
                if size > resume_reader.MAX_BYTES:
                    return b"", "", "too_large"
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", ""), ""
    except requests.RequestException as exc:
        return b"", "", f"fetch_failed:{type(exc).__name__}"


def read_submission(markdown: str) -> dict:
    """Read linked candidate work and return text, sources and non-fatal errors."""
    roots = extract_links(markdown)
    queue = [(url, 0) for url in roots]
    queued = set(roots)
    visited: set[str] = set()
    sources: list[str] = []
    errors: list[str] = []
    parts: list[str] = []
    total = 0

    while queue and len(visited) < MAX_LINKS and total < MAX_TOTAL_CHARS:
        url, depth = queue.pop(0)
        if url in visited or not _allowed(url):
            continue
        visited.add(url)
        data, content_type, error = _fetch(url)
        if error:
            errors.append(f"{url}: {error}")
            continue

        kind = resume_reader._type_from_content_type(content_type)  # noqa: SLF001
        if kind in ("pdf", "docx") or resume_reader._sniff(data) in ("pdf", "docx"):  # noqa: SLF001
            try:
                text = resume_reader.extract(data, content_type)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: document_unreadable:{type(exc).__name__}")
                continue
        elif data.startswith(b"PK\x03\x04") and b"ppt/" in data[:200_000]:
            # Keep the submission reader dependency-light: PPTX is just a zip
            # of XML text runs, and the text is enough for rubric evidence.
            import io
            import zipfile
            from xml.etree import ElementTree

            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = sorted(
                        n for n in archive.namelist()
                        if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                    )
                    runs = []
                    for name in names:
                        root = ElementTree.fromstring(archive.read(name))
                        runs.extend(
                            node.text or ""
                            for node in root.iter()
                            if node.tag.rsplit("}", 1)[-1] == "t"
                        )
                    text = "\n".join(runs)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: presentation_unreadable:{type(exc).__name__}")
                continue
        else:
            text = _visible_html(data)
            if depth < MAX_CRAWL_DEPTH:
                for child in _html_links(data, url):
                    if child not in queued and len(queued) < MAX_LINKS:
                        queued.add(child)
                        queue.append((child, depth + 1))
            # Folder/share pages are navigational wrappers.  Keep useful
            # visible text only when it is substantial; otherwise the linked
            # files below are the evidence and the wrapper is noise.
            if len(text) < 200:
                text = ""

        if not text:
            continue
        remaining = MAX_TOTAL_CHARS - total
        text = text[:min(MAX_DOCUMENT_CHARS, remaining)]
        parts.append(f"SOURCE: {url}\n{text}")
        sources.append(url)
        total += len(text)

    return {
        "text": "\n\n--- LINKED DOCUMENT ---\n\n".join(parts),
        "sources": sources,
        "errors": errors,
        "links": roots,
    }


def read_folder_resume(folder_url: str) -> tuple[str, str]:
    """Fetch only a résumé-like file from a public Drive folder."""
    if not _allowed(folder_url) or "/folders/" not in urlparse(folder_url).path:
        return "", "not_a_resume_folder"
    data, content_type, error = _fetch(folder_url)
    if error:
        return "", error
    soup = BeautifulSoup(data, "html.parser")
    candidates = []
    for node in soup.select("[data-id]"):
        file_id = node.get("data-id", "")
        owner = node.find_parent(attrs={"aria-label": True})
        label = node.get("aria-label", "") or (owner or {}).get("aria-label", "")
        lower = label.lower()
        if (re.fullmatch(r"[A-Za-z0-9_-]{20,}", file_id)
                and ("resume" in lower or "cv" in lower)
                and not lower.endswith(_MEDIA_SUFFIXES)):
            candidates.append(file_id)
    for file_id in dict.fromkeys(candidates):
        child = f"https://drive.google.com/uc?export=download&id={file_id}"
        body, ctype, fetch_error = _fetch(child)
        if fetch_error:
            continue
        try:
            return resume_reader.extract(body, ctype), ""
        except Exception as exc:  # noqa: BLE001
            return "", f"document_unreadable:{type(exc).__name__}"
    return "", "resume_file_not_found"
