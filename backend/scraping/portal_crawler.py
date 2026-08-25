"""
Crawler for the portal's admin pages.

portal_scraper.py already handles submissions via the CSV export. This module
covers what the CSV does not: the roles themselves, and the assessment text
each role is testing against.

Three pages, in order:

    /admin/companies/1?tab=jobs   every role: title, slug, status, job id
    /admin/jobs/<id>?tab=assignments   that role's assignment versions
    /admin/assignments/<id>       the assignment markdown itself

Only the version marked LIVE is fetched -- that is the one candidates actually
sat, so it is the only one a grader should score against.
"""

import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from backend.core.config import PORTAL_BASE_URL, PORTAL_COMPANY_ID, PORTAL_CRAWL_DELAY
from backend.scraping.portal_scraper import _login

log = logging.getLogger(__name__)

JOBS_URL = f"{PORTAL_BASE_URL}/admin/companies/{PORTAL_COMPANY_ID}?tab=jobs"
_ASSIGNMENT_HREF = re.compile(r"/admin/assignments/(\d+)")
_JOB_HREF = re.compile(r"/admin/jobs/(\d+)")


def _get(session: requests.Session, url: str) -> Optional[str]:
    """Fetch a page, returning None on failure or a bounce to the login form."""
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Could not fetch %s: %s", url, exc)
        return None
    if "/login" in resp.url:
        log.error("Session expired while fetching %s", url)
        return None
    return resp.text


def fetch_roles(session: requests.Session) -> list[dict]:
    """
    Every role on the portal, from the company jobs tab.

    The table has no ids or classes to key off, so rows are matched by their
    /admin/jobs/<id> link -- the one thing on each row guaranteed to be stable.
    """
    html = _get(session, JOBS_URL)
    if html is None:
        return []

    roles = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        job_link = tr.find("a", href=_JOB_HREF)
        if not job_link:
            continue                      # header row, or a row without a job
        job_id = int(_JOB_HREF.search(job_link["href"]).group(1))

        cells = [" ".join(td.get_text(" ", strip=True).split())
                 for td in tr.find_all("td")]
        if len(cells) < 4:
            continue

        title, slug, status, live_assignment = cells[0], cells[1], cells[2], cells[3]
        roles.append({
            "job_id": job_id,
            "title": title,
            "slug": slug,
            "status": status,
            "published": status == "published",
            "live_assignment_label": live_assignment,
            "apply_url": f"{PORTAL_BASE_URL}/apply/ajaia/{slug}",
            "admin_url": f"{PORTAL_BASE_URL}/admin/jobs/{job_id}",
        })

    log.info("Portal: %d roles found.", len(roles))
    return roles


def fetch_live_assessment(session: requests.Session, job_id: int) -> Optional[dict]:
    """
    The markdown of the role's LIVE assignment, or None if it has none.

    "LIVE" is a badge rendered inside the name cell rather than its own column,
    so it is detected by substring on that cell. A role with several versions
    has exactly one; a role with none is skipped rather than guessed at.
    """
    html = _get(session, f"{PORTAL_BASE_URL}/admin/jobs/{job_id}?tab=assignments")
    if html is None:
        return None

    live_id = None
    meta: dict = {}
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        link = tr.find("a", href=_ASSIGNMENT_HREF)
        if not link:
            continue
        cells = [" ".join(td.get_text(" ", strip=True).split())
                 for td in tr.find_all("td")]
        if not cells or "LIVE" not in cells[0]:
            continue
        live_id = int(_ASSIGNMENT_HREF.search(link["href"]).group(1))
        meta = {
            "assignment_id": live_id,
            "name": cells[0].replace("LIVE", "").strip(),
            "version": cells[1] if len(cells) > 1 else None,
            "duration": cells[2] if len(cells) > 2 else None,
        }
        break

    if live_id is None:
        log.info("Job %s has no LIVE assignment; skipping.", job_id)
        return None

    detail = _get(session, f"{PORTAL_BASE_URL}/admin/assignments/{live_id}")
    if detail is None:
        return None

    soup = BeautifulSoup(detail, "html.parser")

    def textarea(name: str) -> str:
        node = soup.find("textarea", {"name": name})
        return node.get_text() if node else ""

    markdown = textarea("assignment_markdown")
    if not markdown.strip():
        log.warning("Assignment %s has empty assignment_markdown.", live_id)
        return None

    duration = soup.find("input", {"name": "duration_minutes"})
    return {
        **meta,
        "duration_minutes": (duration or {}).get("value") if duration else None,
        "markdown": markdown,
        "cover_markdown": textarea("cover_markdown"),
        "url": f"{PORTAL_BASE_URL}/admin/assignments/{live_id}",
    }


def crawl(with_assessments: bool = True) -> tuple[list[dict], dict[int, dict]]:
    """
    Crawl roles and, unless told otherwise, each role's live assessment.

    Returns (roles, {job_id: assessment}). One assignment page failing is not
    fatal -- that role simply has no assessment this run, and the stored copy
    from a previous crawl stays in place.
    """
    session = _login()
    if session is None:
        return [], {}

    roles = fetch_roles(session)
    if not with_assessments:
        return roles, {}

    assessments: dict[int, dict] = {}
    for role in roles:
        assessment = fetch_live_assessment(session, role["job_id"])
        if assessment:
            assessments[role["job_id"]] = assessment
        # Two requests per role against a small admin app; pace them.
        time.sleep(PORTAL_CRAWL_DELAY)

    log.info("Portal: %d live assessments fetched.", len(assessments))
    return roles, assessments
