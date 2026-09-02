"""
Which posting a candidate applied to, when one assessment serves two.

Almost every portal assignment in this system is fed by postings that want the
same thing from the same kind of person, so the assignment picks the standard
and nothing else has to. The AI Strategist pair breaks that: Senior AI
Strategist (218F45AD60, four to seven years) and AI Strategist (32DBC63865,
zero to three) sit the identical 90-minute exercise, share one portal
assignment, and are graded against two different grids -- `ai_strategy` and
`ai_strategy_associate`, which differ in the 40-point background row and in how
gently polish is marked.

The portal cannot answer which posting someone came from. Its CSV export
carries a candidate email and an assignment id, and both postings point at the
same assignment, so a submission arrives with no seniority attached to it.
Workable can answer it: the candidate is on one posting's list and not the
other's. That is the whole job of this module -- fetch both lists, match on
email, and write the answer onto the submission so grading never has to make
a network call to know which anchors to read.

Three decisions worth stating, because each of them is a judgment rather than
a fact:

  * An unresolved candidate is graded against the senior grid, not skipped.
    `rubric_pack.for_slug` falls back to the tier marked `tier_default`, which
    is the senior one. Its background anchors are the stricter pair, so a
    junior wrongly marked against them scores low on that row and lands in the
    second look both rubrics already require -- a reviewer reads them. Wrong in
    the other direction, a senior candidate marked on associate anchors could
    advance on a background score they did not earn, and nothing would catch
    it. Between a false second look and a false advance, take the second look.

  * A candidate on both postings resolves to senior, flagged. People do apply
    to both, and the rubrics' own section 10 says to grade against the tier
    they applied to -- which is not a single answer here. Senior is the same
    conservative default, and the flag is what a reviewer needs to decide it
    properly.

  * A tier set by hand is never overwritten. Both rubrics instruct a reviewer
    to move a candidate across grids when the file does not match the posting
    -- a new graduate on the senior posting whose work is strong, a six-year
    consultant who applied to the associate one -- and to note the swap. A
    resolver that reran and undid that would be worse than no resolver.

Nothing here is on the hot path. `ensure_resolved` is called once before a
grading fan-out, does nothing at all for the fourteen families with one grid,
and only looks up submissions that have no tier yet -- so a second run over the
same role costs nothing. Grading itself reads only what this wrote.
"""

import logging
from typing import Optional

from backend.grading import rubric_pack as pack
from backend.config import JOB_ASSESSMENTS, JOB_TIERS
from backend.scraping.workable_client import get_job_candidates

log = logging.getLogger(__name__)


class TierResolutionFailed(Exception):
    """Workable could not be reached, or the slug carries no tiers."""


def tiered_postings(slug: Optional[str]) -> dict[str, str]:
    """
    Workable shortcode -> tier, for the postings that feed this portal slug.

    Empty for every slug with one grid, which is the answer that keeps callers
    from having to know which families are special: an empty map means there is
    nothing to resolve and the slug's single grid marks everyone.
    """
    if not slug or not pack.tiers_for_slug(slug):
        return {}
    return {
        shortcode: JOB_TIERS[shortcode]
        for shortcode, (_label, _portal_job_id, job_slug)
        in JOB_ASSESSMENTS.items()
        if job_slug == slug and shortcode in JOB_TIERS
    }


def posting_labels(slug: Optional[str]) -> dict[str, str]:
    """
    Tier -> the Workable posting's own title, for a slug marked at two tiers.

    The dashboard shows one card per tier and each one needs a name a recruiter
    recognises. "Senior AI Strategist" and "AI Strategist" are what the
    postings are actually called, and they are already in JOB_ASSESSMENTS, so
    the card says the same thing the job board does rather than the portal
    assignment's name with a tier appended to it.
    """
    return {
        tier: JOB_ASSESSMENTS[shortcode][0]
        for shortcode, tier in tiered_postings(slug).items()
    }


def tiers_by_email(slug: str) -> dict[str, dict]:
    """
    Every candidate on this slug's postings, keyed by lowercased email.

    One paginated list per posting, all stages, no date window -- a submission
    can be months older than any reminder window and still need a grid. Values
    carry the shortcode and tier decided on, plus `both` when the candidate
    appears on more than one posting, which is a reviewer's problem rather than
    an error.
    """
    postings = tiered_postings(slug)
    if not postings:
        raise TierResolutionFailed(
            f"Slug {slug!r} is marked by one grid, so there is no tier to "
            f"resolve. Check rubric_pack.tiers_for_slug first."
        )

    found: dict[str, dict] = {}
    for shortcode, tier in postings.items():
        try:
            candidates = get_job_candidates(shortcode)
        except Exception as exc:
            raise TierResolutionFailed(
                f"Could not fetch candidates for {shortcode}: {exc}"
            ) from exc

        log.info("%s (%s): %d candidate(s)", shortcode, tier, len(candidates))
        for cand in candidates:
            email = (cand.get("email") or "").strip().lower()
            if not email:
                continue
            seen = found.get(email)
            if not seen:
                found[email] = {"tier": tier, "shortcode": shortcode,
                                "both": False}
                continue
            if seen["shortcode"] == shortcode:
                continue
            # On both postings. Senior is the conservative read and the tier
            # `for_slug` would have fallen back to anyway; the flag is what
            # carries the ambiguity to whoever has to settle it.
            seen["both"] = True
            if tier == pack.default_tier_for_slug(slug):
                seen["tier"] = tier
                seen["shortcode"] = shortcode

    return found


def resolve_role(role: dict, store, force: bool = False) -> dict:
    """
    Write a tier onto every submission on this role that does not have one.

    `store` is passed in rather than imported so this module stays callable
    from a test or a script without a live Mongo behind it.

    Returns a tally: how many were written, how many are still unresolved
    because Workable has never heard of that address, and how many are on both
    postings and want a human. `force=True` re-resolves submissions that
    already carry a resolved tier -- it still leaves manual ones alone, because
    a reviewer's swap is the one thing here that is not a derivation.
    """
    slug = role.get("slug")
    if not pack.tiers_for_slug(slug):
        return {"slug": slug, "tiered": False, "written": 0,
                "unresolved": 0, "both": 0, "kept_manual": 0}

    mapping = tiers_by_email(slug)

    if force:
        pending = [
            sub for sub in store.list_submissions(job_id=role["_id"])
            if (sub.get("rubric_tier") or {}).get("source") != "manual"
        ]
    else:
        pending = store.submissions_missing_tier(role["_id"])

    written = unresolved = both = 0
    for sub in pending:
        email = (sub.get("candidate_email") or "").strip().lower()
        hit = mapping.get(email)
        if not hit:
            # Left without a tier on purpose rather than defaulted here: an
            # absent field says "nobody has resolved this", a stored "senior"
            # says "somebody checked and it is senior", and a reviewer looking
            # at a background score needs to be able to tell those apart.
            # Grading still falls back to the senior grid either way.
            unresolved += 1
            continue
        if hit["both"]:
            both += 1
        store.set_rubric_tier(
            sub["_id"], hit["tier"], source="workable",
            shortcode=hit["shortcode"],
            note=("Applied to both postings; graded at the default tier. "
                  "Confirm before rejecting on the background row."
                  if hit["both"] else ""),
        )
        written += 1

    kept_manual = 0
    if force:
        kept_manual = sum(
            1 for sub in store.list_submissions(job_id=role["_id"])
            if (sub.get("rubric_tier") or {}).get("source") == "manual"
        )

    log.info(
        "%s: %d tier(s) written, %d unresolved, %d on both postings.",
        role.get("title") or slug, written, unresolved, both,
    )
    return {"slug": slug, "tiered": True, "written": written,
            "unresolved": unresolved, "both": both,
            "kept_manual": kept_manual}


def ensure_resolved(role: dict, store) -> Optional[dict]:
    """
    Resolve what has not been resolved yet, and never raise.

    The call every grading path makes before a fan-out. A role with one grid
    returns None immediately and costs nothing. A tiered role costs one
    paginated Workable list per posting, and only for submissions that have no
    tier yet, so a second grading run over the same role is free.

    Failure is logged and swallowed on purpose. An unresolved candidate is
    still graded -- against the default grid, which is the stricter one -- so a
    Workable outage delays a correction instead of stopping a run. What it must
    not do is quietly stop: the warning is what tells someone to re-resolve and
    regrade whoever moves.
    """
    if not pack.tiers_for_slug(role.get("slug")):
        return None
    try:
        return resolve_role(role, store)
    except TierResolutionFailed as exc:
        log.warning(
            "%s: could not resolve rubric tiers (%s). Grading against the "
            "default grid; re-run once Workable is reachable and regrade "
            "anyone it moves.", role.get("title") or role.get("slug"), exc,
        )
        return None
