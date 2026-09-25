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

from backend.config import MAX_LINKED_CHARS
from backend.scraping import resume_reader

MAX_LINKS = 20
# Lower than it was, because MAX_LINKS is higher than it was. At 50k a single
# verbose document could take a third of the budget and leave the rest of the
# folder unread, which is the opposite of what raising the file count was for.
# 30k is about 5,000 words -- longer than any artefact these assessments ask
# for.
MAX_DOCUMENT_CHARS = 30_000
# The grader's budget, not the crawler's own: see config.MAX_LINKED_CHARS for
# why fetching more than the prompt can carry is waste rather than headroom.
MAX_TOTAL_CHARS = MAX_LINKED_CHARS
# Two, because a candidate who uploads "everything" rarely uploads it flat: the
# share link points at a folder holding /docs, /code and /video, and at depth 1
# the grader sees the folder listing and none of the work inside it.
MAX_CRAWL_DEPTH = 2
FETCH_TIMEOUT = 12

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
_ALLOWED_HOSTS = {
    "drive.google.com", "docs.google.com", "dropbox.com", "www.dropbox.com",
}
_MEDIA_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mp3", ".wav")

# `_fetch` reports a recording through the error channel rather than returning
# its bytes, and this prefix is how `read_submission` tells that apart from a
# real failure. It is a marker, never shown to anyone.
MEDIA_MARKER = "__media__:"


def _parse(url: str):
    """
    urlparse's result, or None for a string it refuses to parse.

    THE RUN THIS COMES FROM. urlparse does not return something inert for a
    malformed netloc -- it RAISES. An unbalanced "[" gives
    ValueError("Invalid IPv6 URL"), and because this runs over whatever a
    candidate pasted into their submission, one such string killed an entire
    scheduled sync: portal login fine, 5005 rows fetched, then a traceback and
    exit 1, with nothing written.

    _URL_RE manufactures the condition rather than protecting against it: it
    excludes "]" from a match, so even a legitimate IPv6 URL arrives here with
    its closing bracket already stripped and raises on arrival.

    A URL that cannot be parsed cannot be on the allow-list, so None is answer
    enough for every caller in this module.
    """
    try:
        return urlparse(url)
    except ValueError:
        return None


def _host(url: str) -> str:
    parsed = _parse(url)
    if parsed is None:
        return ""
    return (parsed.netloc or "").lower().split(":", 1)[0]


def _allowed(url: str) -> bool:
    # Parsed once, not twice: the scheme and the host come from the same
    # result, so a URL cannot pass one check and fail to parse for the other.
    parsed = _parse(url)
    if parsed is None or parsed.scheme != "https":
        return False
    host = (parsed.netloc or "").lower().split(":", 1)[0]
    content_host = (host.endswith(".googleusercontent.com")
                    or host == "drive.usercontent.google.com")
    return host in _ALLOWED_HOSTS or content_host


def extract_links(markdown: str) -> list[str]:
    """Return de-duplicated, safe public document links from submission text."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(markdown or ""):
        url = unescape(raw).rstrip(".,;:!?\n\r")
        if not _allowed(url) or "{" in url or "}" in url:
            continue
        # Do not let a tracking fragment create another fetch of the same file.
        parsed = _parse(url)
        if parsed is None:          # _allowed already rejects these; belt and braces
            continue
        normal = parsed._replace(fragment="").geturl()
        if normal not in seen:
            seen.add(normal)
            found.append(normal)
    return found[:MAX_LINKS]


def _folder_entries(data: bytes):
    """Yield ``(file_id, label, filename)`` for each item row in folder HTML.

    Drive's folder page renders files as rows carrying a ``data-id`` rather
    than as anchors, and the aria label is what tells us the filename and
    whether the item is a native Google Doc or an uploaded file (pptx, mp4).
    Shared by link discovery, media detection and the resume lookup so the
    three cannot drift apart about what a folder contains.
    """
    soup = BeautifulSoup(data, "html.parser")
    for node in soup.select("[data-id]"):
        file_id = node.get("data-id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", file_id):
            continue
        owner = node.find_parent(attrs={"aria-label": True})
        label = node.get("aria-label", "") or (owner or {}).get("aria-label", "")
        yield file_id, label, label.split(" Google ", 1)[0].strip()


def _media_entries(data: bytes) -> dict:
    """``{file_id: filename}`` for the recordings this folder lists."""
    return {file_id: filename
            for file_id, _, filename in _folder_entries(data)
            if filename.lower().endswith(_MEDIA_SUFFIXES)}


def media_names(data: bytes) -> list[str]:
    """Filenames of recordings sitting in this folder, de-duplicated.

    These are NOT fetched -- a screen recording is tens of megabytes, there is
    no transcript to mark, and downloading one spends the whole fetch budget
    to learn nothing. They are reported instead, because "the candidate
    uploaded a walkthrough into the folder" and "the candidate never recorded
    one" are opposite facts, and the grader was reading the first as the
    second.
    """
    return list(dict.fromkeys(_media_entries(data).values()))


def file_id_of(url: str) -> str:
    """The Drive/Docs file id in a URL, or "" -- the identity of a document.

    One file has several URLs. The same Google Doc is
    ``/document/d/<id>/edit``, ``/document/d/<id>/export?format=pdf`` and
    ``uc?export=download&id=<id>``, and on submission 9692 the crawler queued
    two of those forms for one document: it fetched the doc, then fetched it
    again as a download, got an HTTP 500, and recorded a failure against a
    file it had already read successfully. That error then counted toward the
    "some of their links could not be read" note the grader is shown.

    De-duplicating on the URL string cannot see that. De-duplicating on the id
    can.
    """
    match = re.search(r"/d/([A-Za-z0-9_-]{20,})", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
    return match.group(1) if match else ""


def _html_links(data: bytes, base_url: str) -> Iterable[str]:
    """Find Drive/Docs links in both anchors and serialized folder HTML."""
    soup = BeautifulSoup(data, "html.parser")
    candidates = [a.get("href", "") for a in soup.find_all("a")]
    # THE VIDEOS MUST NOT BE QUEUED, AND SKIPPING THEM IN THE LOOP BELOW IS
    # NOT ENOUGH TO STOP IT.
    #
    # The loop reads the folder's item rows and passes over anything whose
    # filename ends in a media suffix. The regex sweep further down then reads
    # the SAME page as raw text and picks the very same file ids back up out
    # of Drive's script blobs, with no filename attached and so no way to tell
    # a deck from a screen recording.
    #
    # On submission 9692 that queued two .mp4 files of 12.1 MB and 13.8 MB.
    # Both ran the 12-second fetch clock out and were recorded as
    # `fetch_timeout`, which cost 24 seconds of the grading call, consumed two
    # slots of the link budget, and left the candidate's error list looking
    # like their work was unreachable when the deck and the document had
    # actually been read.
    #
    # So the exclusion is by file id, applied to every candidate whatever path
    # produced it.
    skip_ids = set(_media_entries(data))
    for file_id, label, filename in _folder_entries(data):
        if file_id in skip_ids:
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
        if not _allowed(url) or "{" in url or "}" in url:
            continue
        if file_id_of(url) in skip_ids:
            continue
        # Drive's own furniture: the sidebar's "my drive" and "shared with me"
        # tabs, the sign-in link, the upgrade banner. They are on an allowed
        # host and so pass every check above, and each one costs a fetch that
        # ends in a redirect off-host -- `?tab=oo` did exactly that here.
        if _is_drive_furniture(url):
            continue
        parsed = _parse(url)
        if parsed is None:
            continue
        yield parsed._replace(fragment="").geturl()


def _is_drive_furniture(url: str) -> bool:
    """Whether this URL names no file, and so cannot be candidate work.

    An allowlist, after a denylist failed. Naming the paths to avoid meant
    naming them all, and one real folder produced `/viewer/main`,
    `/video/captions/edit`, `/drive?authuser`, `/picker`, a bare
    `lh3.googleusercontent.com` and a bare `drive.usercontent.google.com` --
    six more entries for a list that was already wrong, each one a fetch whose
    failure went into the error list the grader reads as "this candidate's
    work could not be read".

    Every actual document carries a file id, and a folder carries `/folders/`.
    Nothing else on these hosts is a candidate artefact, so the question is
    asked that way round instead.
    """
    parsed = _parse(url)
    if parsed is None:
        # Not parseable, so not a document either. Treated as furniture so the
        # caller skips it instead of spending a fetch on it.
        return True
    if file_id_of(url):
        return False
    if "/folders/" in parsed.path:
        return False
    return True


def _looks_like_text(data: bytes, content_type: str) -> bool:
    """Whether these bytes are HTML or plain text rather than a binary file.

    Decided from the bytes as well as the header because Drive's content types
    are not reliable on share URLs. A NUL in the first few kilobytes is the
    usual giveaway; so is a decode that produces mostly replacement
    characters.
    """
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind and not (kind.startswith("text/")
                     or kind in ("application/xhtml+xml", "application/json")):
        return False
    head = data[:4096]
    if b"\x00" in head:
        return False
    decoded = head.decode("utf-8", "replace")
    return decoded.count("\ufffd") <= max(2, len(decoded) // 100)


def _visible_html(data: bytes) -> str:
    soup = BeautifulSoup(data, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                         if line.strip())


_DISPOSITION_NAME = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
                               re.IGNORECASE)


def _media_name(response) -> str:
    """This response's recording filename, or "" if it is not a recording.

    Prefers the name Drive puts in Content-Disposition, because "ajaia.ai
    Strategy Presentation.mp4" tells a reviewer which artefact is on file and
    "video/mp4" does not.
    """
    content_type = (response.headers.get("Content-Type") or "").lower()
    disposition = response.headers.get("Content-Disposition") or ""
    match = _DISPOSITION_NAME.search(disposition)
    name = unescape(match.group(1).strip()) if match else ""
    if content_type.startswith(("video/", "audio/")):
        return name or content_type
    if name.lower().endswith(_MEDIA_SUFFIXES):
        return name
    return ""


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
            # STOP AT THE HEADERS WHEN THIS IS A RECORDING.
            #
            # The headers arrive before the body, so a video costs one round
            # trip to identify and nothing to skip. Reading it instead is what
            # produced submission 9692's two `fetch_timeout`s: 12.1 MB and
            # 13.8 MB of .mp4 pulled through a 12-second clock, 24 seconds of
            # a grading call spent on bytes that were going to be discarded,
            # and an error list that made a candidate whose deck and document
            # had both been read look unreachable.
            #
            # This is also the only media check that works. The filename-based
            # one reads Drive's rendered folder HTML, and a signed-out folder
            # does not serve the `data-id`/`aria-label` rows it expects -- the
            # ids come back out of script blobs with no filename attached. A
            # response saying `video/mp4` is unambiguous.
            media = _media_name(response)
            if media:
                return b"", response.headers.get("Content-Type", ""), \
                    MEDIA_MARKER + media
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
    # Fetched documents, by file id rather than by URL. See `file_id_of`: the
    # same Google Doc reached the queue twice on submission 9692, once as
    # /edit and once as a uc?export=download, and the second attempt's HTTP
    # 500 was recorded as a failure to read a document already sitting in
    # `parts`.
    fetched_ids: set[str] = set()
    sources: list[str] = []
    failures: list[tuple] = []
    parts: list[str] = []
    media: list[str] = []
    total = 0

    while queue and len(visited) < MAX_LINKS and total < MAX_TOTAL_CHARS:
        url, depth = queue.pop(0)
        if url in visited or not _allowed(url):
            continue
        identity = file_id_of(url)
        if identity and identity in fetched_ids:
            continue
        visited.add(url)
        data, content_type, error = _fetch(url)
        if error.startswith(MEDIA_MARKER):
            name = error[len(MEDIA_MARKER):]
            if name not in media:
                media.append(name)
            continue
        if error:
            # Kept against the file rather than the URL so a document read
            # successfully under one of its URLs is not also reported as a
            # failure under another -- see `file_id_of`. 9692's Google Doc
            # was fetched as /edit and as a download, and the download's HTTP
            # 500 was counted against a document already in `parts`.
            failures.append((identity, f"{url}: {error}"))
            continue

        kind = resume_reader._type_from_content_type(content_type)  # noqa: SLF001
        if kind in ("pdf", "docx") or resume_reader._sniff(data) in ("pdf", "docx"):  # noqa: SLF001
            try:
                text = resume_reader.extract(data, content_type)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    (identity, f"{url}: document_unreadable:{type(exc).__name__}"))
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
                failures.append(
                    (identity, f"{url}: presentation_unreadable:{type(exc).__name__}"))
                continue
        elif not _looks_like_text(data, content_type):
            # A file we cannot read is not evidence, and its bytes are not
            # prose. `_visible_html` will happily run over a PNG and return
            # the mojibake its decoder produces -- which is how 440 characters
            # beginning "\x89PNG" came to sit in a candidate's submission
            # under a SOURCE header, next to their strategy deck, as though
            # they had written it. Drive serves the signed-out page's avatar
            # from an allowed host, so nothing upstream of here stopped it.
            failures.append(
                (identity, f"{url}: not_a_document:{content_type or 'unknown'}"))
            continue
        else:
            text = _visible_html(data)
            for name in media_names(data):
                if name not in media:
                    media.append(name)
            if depth < MAX_CRAWL_DEPTH:
                for child in _html_links(data, url):
                    if child not in queued and len(queued) < MAX_LINKS:
                        queued.add(child)
                        queue.append((child, depth + 1))
            # Folder/share pages are navigational wrappers: the files
            # queued above are the evidence and the page itself is chrome.
            #
            # The 200-character floor alone did not catch them. A signed-out
            # Drive folder renders its sign-in banner, keyboard-shortcut help,
            # sort controls and column headers as visible text, which on a
            # real submission came to 13,000 characters -- sixty times the
            # floor -- and went into the prompt under a SOURCE header as
            # though the candidate had written it. The grader read it, found
            # no assessment in it, and marked every work-product row 1.
            #
            # So a folder URL is dropped on what it IS rather than on how much
            # text it produced.
            if "/folders/" in urlparse(url).path or len(text) < 200:
                text = ""

        if not text:
            continue
        if identity:
            fetched_ids.add(identity)
        remaining = MAX_TOTAL_CHARS - total
        text = text[:min(MAX_DOCUMENT_CHARS, remaining)]
        parts.append(f"SOURCE: {url}\n{text}")
        sources.append(url)
        total += len(text)

    # A failure on a file we went on to read from another URL is not a
    # failure. Reporting it would put "some of their links could not be read"
    # in front of the grader about work it has in full.
    errors = [message for identity, message in failures
              if not (identity and identity in fetched_ids)]

    return {
        "text": "\n\n--- LINKED DOCUMENT ---\n\n".join(parts),
        "sources": sources,
        "errors": errors,
        "links": roots,
        "media": media,
        # How many things were actually FETCHED, which is not len(links).
        # `roots` are the links written in the submission; a single one of them
        # can be a folder holding twenty files, and each of those is visited,
        # can fail, and gets its own entry in `errors`. Reporting errors
        # against len(links) produced log lines like "12 of 1 link(s) could not
        # be read", which is not a ratio of anything.
        "attempted": len(visited),
    }


def read_folder_resume(folder_url: str) -> tuple[str, str]:
    """Fetch only a résumé-like file from a public Drive folder."""
    if not _allowed(folder_url) or "/folders/" not in urlparse(folder_url).path:
        return "", "not_a_resume_folder"
    data, content_type, error = _fetch(folder_url)
    if error:
        return "", error
    candidates = []
    for file_id, label, filename in _folder_entries(data):
        lower = label.lower()
        if (("resume" in lower or "cv" in lower)
                and not filename.lower().endswith(_MEDIA_SUFFIXES)):
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
