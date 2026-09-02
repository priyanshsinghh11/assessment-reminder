"""
Workable API client.

Handles authentication, rate limiting, 429 back-off, and cursor pagination.
Every Workable request in this system goes through here.
"""

import time
import logging
import requests
from typing import Optional
from urllib.parse import urlparse, parse_qs

from backend.config import (
    WORKABLE_BASE_URL,
    WORKABLE_API_TOKEN,
    WORKABLE_RATE_LIMIT,
    WORKABLE_MAX_RETRIES,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
_request_times: list[float] = []


def _wait_for_slot() -> None:
    """Block until a request slot is free (WORKABLE_RATE_LIMIT per 10 s)."""
    now = time.monotonic()
    while _request_times and _request_times[0] < now - 10:
        _request_times.pop(0)
    if len(_request_times) >= WORKABLE_RATE_LIMIT:
        sleep_for = 10 - (now - _request_times[0]) + 0.3
        log.debug("Rate limit: sleeping %.1f s", sleep_for)
        time.sleep(sleep_for)
    _request_times.append(time.monotonic())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {WORKABLE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def get(path: str, params: Optional[dict] = None) -> dict:
    """
    GET with rate limiting and 429 back-off.

    Workable returns 429 even below the documented rate, so a bare
    raise_for_status() here would surface as "candidate has no data" further
    up. Retry instead, and only raise once retries are exhausted.
    """
    url = f"{WORKABLE_BASE_URL}{path}"

    for attempt in range(WORKABLE_MAX_RETRIES):
        _wait_for_slot()
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)

        if resp.status_code == 429:
            backoff = 5 * (attempt + 1)
            log.warning(
                "429 from Workable on %s, backing off %d s (attempt %d/%d)",
                path, backoff, attempt + 1, WORKABLE_MAX_RETRIES,
            )
            time.sleep(backoff)
            continue

        resp.raise_for_status()
        return resp.json()

    raise requests.HTTPError(
        f"Workable kept returning 429 for {path} after "
        f"{WORKABLE_MAX_RETRIES} attempts"
    )


def paginate(path: str, key: str, params: Optional[dict] = None) -> list[dict]:
    """Fetch every page of a list endpoint and return the combined results."""
    params = dict(params or {})
    params.setdefault("limit", 100)
    results: list[dict] = []

    while True:
        data = get(path, params)
        results.extend(data.get(key, []))

        next_url = data.get("paging", {}).get("next")
        if not next_url:
            return results

        params["since_id"] = parse_qs(urlparse(next_url).query).get(
            "since_id", [None]
        )[0]


def get_job_candidates(
    job_shortcode: str,
    created_after: Optional[str] = None,
) -> list[dict]:
    """
    Return every candidate for a job, optionally only those created after an
    ISO timestamp. Covers all stages -- filtering happens upstream.
    """
    params = {}
    if created_after:
        params["created_after"] = created_after
    return paginate(f"/jobs/{job_shortcode}/candidates", "candidates", params)


def get_job(job_shortcode: str) -> dict:
    """One posting: title, state, location, description, application_url."""
    return get(f"/jobs/{job_shortcode}")


def get_candidate(candidate_id: str) -> dict:
    """
    One candidate's full profile.

    Worth its own call despite the rate limit, because three fields live here
    and nowhere else. `resume_url` is the big one: a presigned link to the file
    the candidate actually uploaded, which the list endpoint does not carry --
    it offers only `resume_metadata`, the filename and type. `cover_letter`,
    `summary`, `experience_entries`, `education_entries` and `skills` are
    likewise detail-only.

    The URL is signed and short-lived. Fetch it now; storing it to download
    later gets a 403.
    """
    return get(f"/candidates/{candidate_id}").get("candidate", {})
