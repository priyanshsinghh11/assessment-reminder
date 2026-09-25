"""
A link nobody can parse must not take the sync down with it.

THE RUN THIS COMES FROM. A scheduled `ingest --skip-roles` logged into the
portal, downloaded 5005 rows, and then died:

    File ".../submission_reader.py", line 53, in _host
      return (urlparse(url).netloc or "").lower().split(":", 1)[0]
    ValueError: Invalid IPv6 URL

urlparse does not return something inert for a malformed netloc -- it RAISES.
This code runs over whatever a candidate pasted into their submission, so one
such string cost the entire run: nothing written, exit 1, and 5005 rows of
fetching thrown away.

`_URL_RE` makes it likelier rather than safer. It excludes "]" from a match,
so a perfectly legitimate IPv6 URL arrives with its closing bracket stripped
and raises on arrival -- the input does not even have to be malformed.

These run on plain strings: no portal, no network, no database.
"""

import pytest

from backend.scraping import resume_reader, submission_reader

REAL = "https://docs.google.com/document/d/xyz/edit"

# Unbalanced, so urlparse raises on them wherever they appear.
UNPARSEABLE = [
    "https://[oops",                    # as a candidate typed it
    "https://[",
    "https://[::1",
]

# Well formed, and parses fine on its own. It reaches submission_reader
# BROKEN, because _URL_RE excludes "]" and captures only "https://[2001:db8::1"
# -- which is why a valid URL can crash a module that never sees it whole.
VALID_IPV6 = "https://[2001:db8::1]/cv.pdf"

BROKEN = UNPARSEABLE + [VALID_IPV6]


class TestExtractLinksSurvivesAnything:
    @pytest.mark.parametrize("text", BROKEN)
    def test_a_broken_url_does_not_raise(self, text):
        assert submission_reader.extract_links(text) == []

    @pytest.mark.parametrize("text", BROKEN)
    def test_a_real_link_beside_it_is_still_found(self, text):
        """
        THE POINT. Skipping the whole submission would lose the candidate's
        actual work, which is the thing being graded -- so the bad link is
        dropped and the good one kept.
        """
        assert submission_reader.extract_links(f"{text} and {REAL}") == [REAL]

    def test_several_broken_urls_at_once(self):
        text = " ".join(BROKEN) + " " + REAL
        assert submission_reader.extract_links(text) == [REAL]

    def test_empty_and_none_are_fine(self):
        assert submission_reader.extract_links("") == []
        assert submission_reader.extract_links(None) == []


class TestTheAllowListIsUnchanged:
    """The fix must not have widened what counts as a safe link."""

    def test_a_good_drive_link_still_passes(self):
        assert submission_reader.extract_links(REAL) == [REAL]

    def test_plain_http_is_still_refused(self):
        assert submission_reader.extract_links("http://drive.google.com/a") == []

    def test_an_off_host_link_is_still_refused(self):
        assert submission_reader.extract_links("https://evil.example.com/a") == []

    def test_an_unparseable_url_is_not_allowed(self):
        # The specific worry: returning "" for the host must not accidentally
        # match an allow-list entry or an empty-host check somewhere.
        for text in BROKEN:
            assert submission_reader._allowed(text) is False

    def test_a_fragment_is_still_stripped(self):
        assert submission_reader.extract_links(REAL + "#gid=1") == [REAL]


class TestResumeReaderToo:
    """
    Resume links come from the same portal export, so they carry the same risk
    -- but they arrive WHOLE. Nothing strips their brackets on the way in, so
    only a genuinely unbalanced link is unparseable here, and a complete IPv6
    URL parses normally. Pinned separately so the difference stays visible.
    """

    @pytest.mark.parametrize("link", UNPARSEABLE)
    def test_host_returns_empty_rather_than_raising(self, link):
        assert resume_reader._host(link) == ""

    def test_a_whole_ipv6_url_parses_normally(self):
        assert resume_reader._host(VALID_IPV6) == "[2001:db8::1]"

    @pytest.mark.parametrize("link", BROKEN)
    def test_direct_url_returns_the_link_unchanged(self, link):
        # Unrecognised hosts are returned unchanged by design; a link that
        # will not parse is the same case. It then fetches, fails, and is
        # recorded as unreadable -- which is a fact about one candidate, not
        # a reason to stop the run.
        assert resume_reader.direct_url(link) == link

    def test_a_real_drive_link_is_still_rewritten(self):
        link = "https://drive.google.com/file/d/abcdefghijklmnopqrstuvw/view"
        assert resume_reader._host(link) == "drive.google.com"
        assert resume_reader.direct_url(link)
