"""
The Ajaia Assessment Scoring Rubrics, version 2026-08-12, as data.

The pack covers 38 live Workable postings in 16 rubric units and 19 scoring
grids. This module is those grids: every criterion, its weight, and the
behavioural anchors at 5, 3 and 1 that decide which mark it gets -- plus the
auto-fails, the two-minute triage, the GIA overlay and the reviewer notes that
sit around the grid in the source document.

Four units do not split 70 / 10 / 10 / 10, and all four buy the same fifth
block -- `background`, for the record the resume and the portfolio show -- at
wildly different prices, from 100 points down to 10. Unit 13, AI Strategy,
arrived on 2026-08-21 with the two AI Strategist postings and pays 40, because
on that seat the track record is half the decision. Unit 14, Social Media and
Marketing Intern, arrived on 2026-08-22 and pays 10, for the opposite reason:
an intern's record is usually thin and would otherwise decide the seat by
accident, so capping it at 10 is what stops it. Unit 15, General Management
and Growth, pays the whole 100 because it has no assessment behind it at all
and the record is the only evidence there is. Unit 16, Recruiting, arrived on
2026-08-31 and pays 10 for unit 14's reason in its own source's words -- "the
Experience row adds, never blocks" -- and spends what it saved on a 25-point
spike instead. Every departure is stated and defended where it is made;
`block_points_of` is how the rest of this module was taught to read them, and
`config.CV_WEIGHT_BY_SEAT` pins all four seats' external CV weight to 0.0 so
the record is not paid for twice.

Unit 13 is also the only one whose two grids share a slug. The senior and
the associate posting sit the identical 90-minute assessment and the portal
carries one assignment for the pair, so `for_slug` takes an optional `tier` --
the seniority of the POSTING the candidate applied to, which
`config.JOB_TIERS` maps from the Workable shortcode. Everywhere else one slug
still means one grid, and `_validate_pack` still enforces that.

How this package is laid out:

    __init__.py       everything below -- validation, the indices, and the
                      lookup functions every caller uses. This file.
    _architecture.py  BLOCKS, BANDS, TRIAGE_ROUTES, UNIVERSAL_AUTO_FAILS,
                      FRAUD_TELLS, GIA_RULES -- the part all 19 grids share.
    _grids.py         GRIDS: the grids themselves, 7,400 lines of data.

It was one 8,134-line module until the data and the logic were separated, at
which point the functions a caller actually wants stopped starting at line
7,696. Nothing about the pack changed in the move -- same grids, same comments,
same validation at import, same public names. `from backend.grading import
rubric_pack` and every `pack.<name>` reference work exactly as before, which is
why the names are re-exported below rather than left in the modules they now
live in.

Why a Python module and not prose in `assessments/`:

  * The anchors are the whole value of the pack. They quote the real task
    content ("post-money $13.3M shown as $2M / 0.15", "the 90-day-old service
    account key"), which is what makes a mark checkable rather than a vibe.
    Re-deriving them from a model per role would throw that away.
  * The weights have to add up. `_validate()` runs at import and refuses to
    load a grid whose criteria do not sum to exactly 100, or whose blocks do
    not sum to the split that grid declares -- 70 / 10 / 10 / 10 unless it
    says otherwise. The pack claims this holds in every grid; here it is
    enforced rather than claimed.
  * A grid is addressable. `for_slug("investment-lead")` is what the evaluator
    and the dashboard both call, so the standard a candidate was marked
    against is the same object the reviewer reads on the role page.

Roles the pack does not cover -- the 14 portal assignments with no Workable
job mapped to them -- get a grid of the same shape derived from their
assessment text and stored in `assessments/grid-<slug>.json`. See
`evaluator.derive_grid()`. Everything downstream of `for_slug()` treats the two
kinds identically, which is the point of validating the shape here.

Source: Ajaia_Assessment_Rubric_Pack.md (built 2026-08-12 from 33 live JDs and
the live assessment assignments), plus the standalone rubric documents that
arrived after it -- AI Strategy on 2026-08-21; "Social Media and Marketing
Intern. Scoring Rubric, Interview Loop and Launch Plan", built 2026-08-20 from
Jordan's 2026-08-19 notes, on 2026-08-22; and the Recruiter scoring rubric on
2026-08-31, kept verbatim in `assessments/rubric-recruiter.md`. Where this file
paraphrases, the intent is the source's; where it quotes, the quotes are the
source's own.
"""


from typing import Optional

from backend.grading.rubric_pack._architecture import (
    BLOCKS,
    BLOCK_POINTS,
    BLOCK_LABEL,
    DERIVED_BLOCKS,
    BANDS,
    ADVANCE_MIN,
    TRIAGE_ROUTES,
    UNIVERSAL_AUTO_FAILS,
    FRAUD_TELLS,
    GIA_RULES,
)
from backend.grading.rubric_pack._grids import GRIDS

# The public surface, stated rather than left to whatever happens to be bound
# at module level. Everything a caller outside this package touches is here;
# the two underscore modules are an implementation detail of how the file was
# split, not a second place to import from.
__all__ = [
    # architecture
    "BLOCKS", "BLOCK_POINTS", "BLOCK_LABEL", "DERIVED_BLOCKS", "BANDS",
    "ADVANCE_MIN", "TRIAGE_ROUTES", "UNIVERSAL_AUTO_FAILS", "FRAUD_TELLS",
    "GIA_RULES",
    # data
    "GRIDS",
    # validation
    "block_points_of", "seeded_of", "seeded_for", "validate_grid",
    # lookup and derived facts
    "for_slug", "tiers_for_slug", "default_tier_for_slug", "by_key",
    "covered_slugs", "band_for", "route_for", "points_for", "blocks_of",
    "auto_fails_of", "summary",
]


# ---------------------------------------------------------------------------
# Validation
#
# Runs at import. A grid that does not add to 100, or whose blocks do not add
# to the split that grid declares -- 70/10/10/10 unless it says otherwise -- is
# a bug that would silently rescale every candidate in that family, so it stops
# the process here rather than showing up as a score.
# ---------------------------------------------------------------------------

def block_points_of(grid: dict) -> dict[str, int]:
    """
    What each block is worth in THIS grid.

    The pack's 70 / 10 / 10 / 10 unless the grid carries its own
    `block_points`, in which case that map replaces it wholesale -- a partial
    override would leave the reader working out which half of the split is
    still the default, and the whole point of stating a departure is that it is
    stated.

    Whatever it says, it has to add to 100. A grid that renamed its own
    denominator would break the one thing every family still shares: that a 62
    here and a 62 in Marketing mean the same decision.
    """
    points = dict(BLOCK_POINTS)
    override = grid.get("block_points")
    if not override:
        return points

    if not isinstance(override, dict):
        raise ValueError(
            f"{grid.get('key', '?')}: 'block_points' must be a dict of "
            f"block key -> points."
        )
    unknown = sorted(set(override) - set(points))
    if unknown:
        raise ValueError(
            f"{grid.get('key', '?')}: 'block_points' names block(s) {unknown} "
            f"that are not in BLOCKS."
        )
    missing = sorted(set(points) - set(override))
    if missing:
        raise ValueError(
            f"{grid.get('key', '?')}: 'block_points' is a full replacement and "
            f"omits {missing}. State every block, including the ones worth 0."
        )
    for key, value in override.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError(
                f"{grid.get('key', '?')}: block_points[{key!r}] must be a "
                f"non-negative int."
            )
    if sum(override.values()) != 100:
        raise ValueError(
            f"{grid.get('key', '?')}: block_points sum to "
            f"{sum(override.values())}, not 100."
        )
    return dict(override)


def seeded_of(grid: dict) -> tuple[dict, ...]:
    """
    The issues deliberately planted in this assessment's materials.

    A newer idea than the rest of the pack and a narrower one. An anchor says
    what a 5 looks like; a seeded issue says what was buried in the materials
    on purpose to see whether the candidate digs it up. The two are not the
    same question, and the second one is the whole reason several of these
    assessments exist: the Design Lead pack, for instance, is built around a
    stakeholder asking for the wrong thing while his own usage log says so,
    and "did they notice" is a cleaner signal than any prose about synthesis
    quality.

    Kept as its own list rather than folded into the anchors because it has to
    be reported per issue, not per mark. A reviewer opening a card wants to
    see WHICH traps a candidate walked into, and averaging that into a 1-5 on
    a criterion throws away the only part a hiring manager can act on. The
    model returns caught/missed by key; `evaluator._parse_verdict` keeps them
    beside the mark.

    Empty for every grid written before this existed, which is most of them.
    Absence means "this rubric does not track planted issues", never "this
    assessment has none".
    """
    return tuple(grid.get("seeded") or ())


def seeded_for(grid: dict, criterion_key: str) -> tuple[dict, ...]:
    """The seeded issues that bear on one criterion, in pack order."""
    return tuple(issue for issue in seeded_of(grid)
                 if criterion_key in (issue.get("criteria") or ()))


def _validate_seeded(grid: dict, where: str, criterion_keys: set[str]) -> None:
    """
    Seeded issues must be addressable and must land on real criteria.

    Both halves matter and they fail differently. A duplicate or missing key
    means the model is asked to return an issue it cannot name, and the reply
    is dropped silently. A `criteria` entry naming a row that does not exist
    means the issue is rendered into the prompt for nobody -- it is in the
    pack, it reads fine, and no criterion ever asks about it. That is the
    failure this check exists for, because it is invisible in review.
    """
    seen: set[str] = set()
    for issue in seeded_of(grid):
        key = issue.get("key")
        if not key:
            raise ValueError(f"{where}: a seeded issue has no key.")
        if key in seen:
            raise ValueError(f"{where}: duplicate seeded key {key!r}.")
        seen.add(key)
        for field in ("label", "where", "caught", "missed"):
            if not issue.get(field):
                raise ValueError(
                    f"{where}/seeded/{key}: missing '{field}'. A seeded issue "
                    f"needs a name, the material it is planted in, and what "
                    f"catching and missing it each look like."
                )
        rows = issue.get("criteria") or ()
        if not rows:
            raise ValueError(
                f"{where}/seeded/{key}: names no criteria. An issue no "
                f"criterion asks about is never put to the grader."
            )
        unknown = sorted(set(rows) - criterion_keys)
        if unknown:
            raise ValueError(
                f"{where}/seeded/{key}: names criteria {unknown} that are not "
                f"in this grid."
            )


def validate_grid(grid: dict, where: str = "grid") -> None:
    """Raise ValueError unless this grid can produce a score out of 100."""
    for field in ("key", "unit", "criteria"):
        if not grid.get(field):
            raise ValueError(f"{where}: missing '{field}'.")

    criteria = grid["criteria"]
    if not isinstance(criteria, (list, tuple)) or not criteria:
        raise ValueError(f"{where}: 'criteria' must be a non-empty list.")

    expected_points = block_points_of(grid)

    seen: set[str] = set()
    per_block: dict[str, int] = {block["key"]: 0 for block in BLOCKS}

    for criterion in criteria:
        key = criterion.get("key")
        if not key:
            raise ValueError(f"{where}: a criterion has no key.")
        if key in seen:
            raise ValueError(f"{where}: duplicate criterion key {key!r}.")
        seen.add(key)

        block = criterion.get("block")
        if block not in per_block:
            raise ValueError(
                f"{where}/{key}: block {block!r} is not one of "
                f"{sorted(per_block)}."
            )

        weight = criterion.get("weight")
        if not isinstance(weight, int) or weight <= 0:
            raise ValueError(f"{where}/{key}: weight must be a positive int.")
        per_block[block] += weight

        anchors = criterion.get("anchors") or {}
        # Anchors survive a JSON round trip for derived grids, where integer
        # keys come back as strings. Accept both rather than making the loader
        # care.
        missing = [level for level in (5, 3, 1)
                   if not (anchors.get(level) or anchors.get(str(level)))]
        if missing:
            raise ValueError(
                f"{where}/{key}: no anchor text at level(s) {missing}. Every "
                f"criterion needs a 5, a 3 and a 1 or the mark is a vibe."
            )

    total = sum(per_block.values())
    if total != 100:
        raise ValueError(f"{where}: weights sum to {total}, not 100.")
    for block_key, points in per_block.items():
        expected = expected_points[block_key]
        if points != expected:
            raise ValueError(
                f"{where}: block '{block_key}' sums to {points}, not "
                f"{expected}."
            )

    triage = grid.get("triage") or ()
    if len(triage) != 6:
        raise ValueError(
            f"{where}: triage has {len(triage)} checks, not the six the pack "
            f"routes on."
        )

    _validate_seeded(grid, where, seen)


def _validate_pack() -> None:
    """
    One grid per slug, unless the grids that share it are tiers of each other.

    The original rule was flatly one assessment, one grid, and it held for
    every family until the AI Strategist pair: two postings at different
    seniorities, one 90-minute assessment between them, and one portal
    assignment feeding both. The slug cannot separate those, so `tier` does --
    but only under conditions strict enough that the old rule is still the
    default. A shared slug needs every claimant to declare a distinct `tier`,
    and exactly one of them to declare `tier_default` so a submission whose
    posting is unknown still resolves to something.
    """
    keys: set[str] = set()
    claims: dict[str, list[dict]] = {}
    for grid in GRIDS:
        validate_grid(grid, where=grid.get("key", "?"))
        if grid["key"] in keys:
            raise ValueError(f"Duplicate grid key {grid['key']!r}.")
        keys.add(grid["key"])
        for slug in grid.get("slugs") or ():
            claims.setdefault(slug, []).append(grid)

    for slug, grids in claims.items():
        if len(grids) == 1:
            continue
        named = ", ".join(repr(g["key"]) for g in grids)
        tiers = [g.get("tier") for g in grids]
        if not all(tiers):
            raise ValueError(
                f"Slug {slug!r} is claimed by {named}. One assessment, one "
                f"grid -- unless every claimant declares a 'tier', and at "
                f"least one here does not."
            )
        if len(set(tiers)) != len(tiers):
            raise ValueError(
                f"Slug {slug!r} is claimed by {named} with duplicate tier(s) "
                f"{sorted(t for t in set(tiers) if tiers.count(t) > 1)}. A "
                f"tier has to pick out one grid."
            )
        defaults = [g for g in grids if g.get("tier_default")]
        if len(defaults) != 1:
            raise ValueError(
                f"Slug {slug!r} is claimed by {named} and "
                f"{len(defaults)} of them set 'tier_default'. Exactly one "
                f"must: it is the grid a submission is marked against when "
                f"the posting it came from cannot be resolved."
            )
        # Tiers of one assessment must mark the same rows at the same weights,
        # differing only in their anchors. Everything downstream reads a score
        # as a row on a shared table -- the dashboard's criterion columns, the
        # per-criterion sort, a reviewer comparing two candidates on the same
        # assignment -- and that only means anything if 'triage' is 12 points
        # of triage for both of them. A tier is a different bar on the same
        # question, not a different question.
        shape = [(c["key"], c["block"], c["weight"]) for c in grids[0]["criteria"]]
        for other in grids[1:]:
            theirs = [(c["key"], c["block"], c["weight"])
                      for c in other["criteria"]]
            if theirs != shape:
                raise ValueError(
                    f"Slug {slug!r}: {grids[0]['key']!r} and {other['key']!r} "
                    f"are tiers of one assessment but do not mark the same "
                    f"rows at the same weights in the same order. Tiers "
                    f"differ in their anchors, not their shape."
                )


_validate_pack()

_BY_KEY = {grid["key"]: grid for grid in GRIDS}

# Slug -> the grid to use when no tier is known. For the fourteen single-claim
# slugs that is simply their grid; for a tiered slug it is the one flagged
# `tier_default`, which _validate_pack has already proved exists and is unique.
_BY_SLUG: dict[str, dict] = {}
for _grid in GRIDS:
    for _slug in _grid.get("slugs") or ():
        if _slug not in _BY_SLUG or _grid.get("tier_default"):
            _BY_SLUG[_slug] = _grid

# (slug, tier) -> grid, for grids that declare a tier at all.
_BY_SLUG_TIER = {
    (slug, grid["tier"]): grid
    for grid in GRIDS if grid.get("tier")
    for slug in (grid.get("slugs") or ())
}


# ---------------------------------------------------------------------------
# Lookup and derived facts
# ---------------------------------------------------------------------------

def for_slug(slug: Optional[str], tier: Optional[str] = None) -> Optional[dict]:
    """
    The pack grid that marks this portal assessment slug, if any.

    `tier` is the seniority of the posting the candidate applied to, not of
    the assessment: one slug can carry two grids when two postings share an
    exercise. An unknown or unrecognised tier is not an error -- it falls back
    to the slug's default grid, because a submission whose posting we could not
    resolve still has to be marked against something, and the alternative is
    refusing to grade a candidate over a mapping gap that is ours.
    """
    if not slug:
        return None
    if tier:
        tiered = _BY_SLUG_TIER.get((slug, tier))
        if tiered:
            return tiered
    return _BY_SLUG.get(slug)


def tiers_for_slug(slug: Optional[str]) -> tuple[str, ...]:
    """
    The tiers this slug can be marked at, or () when it carries one grid.

    Non-empty means a reviewer has a real choice to make on every candidate --
    which is exactly the case the AI Strategist rubrics describe in their
    section 10, where a strong new graduate on the senior posting is moved to
    the associate grid and the swap is noted on the file.
    """
    if not slug:
        return ()
    tiers = tuple(t for (s, t) in _BY_SLUG_TIER if s == slug)
    return tuple(sorted(tiers)) if len(tiers) > 1 else ()


def default_tier_for_slug(slug: Optional[str]) -> Optional[str]:
    """The tier `for_slug(slug)` lands on when no tier is passed."""
    grid = _BY_SLUG.get(slug) if slug else None
    return grid.get("tier") if grid else None


def by_key(key: Optional[str]) -> Optional[dict]:
    return _BY_KEY.get(key) if key else None


def covered_slugs() -> tuple[str, ...]:
    return tuple(sorted(_BY_SLUG))


def band_for(score: float) -> dict:
    """The band a total lands in. Best 85+, Better 75-84, Good 60-74, Okay <60."""
    for band in BANDS:
        if score >= band["min"]:
            return band
    return BANDS[-1]


def route_for(triage_passed: int) -> dict:
    """Where a submission goes on its triage count. 0-2 reject, 3-4 full, 5-6 priority."""
    for route in TRIAGE_ROUTES:
        if triage_passed >= route["min"]:
            return route
    return TRIAGE_ROUTES[-1]


def points_for(score: Optional[int], weight: int) -> Optional[float]:
    """
    A criterion's contribution: score x weight / 5.

    The pack's arithmetic exactly. Rated 1 to 5, so a 5 earns the full weight
    and a 1 earns a fifth of it -- a criterion can never contribute zero, which
    is deliberate: zero is what an auto-fail is for.
    """
    if score is None:
        return None
    return round(score * weight / 5, 2)


def blocks_of(grid: dict) -> list[dict]:
    """
    The grid's criteria grouped into blocks, in block order.

    Four blocks for every grid in the pack but one; `points` is read from
    `block_points_of` rather than from BLOCKS so a grid that states its own
    split shows that split everywhere it is rendered -- the prompt, the role
    page and the scored card -- instead of the default it does not use.
    """
    points = block_points_of(grid)
    out = []
    for block in BLOCKS:
        rows = [c for c in grid["criteria"] if c["block"] == block["key"]]
        if not rows:
            continue
        label = block["label"]
        if block["key"] == "spike" and grid.get("spike"):
            label = f"{grid['spike']} (spike)"
        out.append({
            "key": block["key"],
            "label": label,
            "points": points[block["key"]],
            "asks": block["asks"],
            "criteria": rows,
        })
    return out


def auto_fails_of(grid: dict) -> tuple[str, ...]:
    """
    Universal auto-fails plus this family's own, in the pack's order.

    A grid may opt out of the universal list with
    `"universal_auto_fails": False`, and five grids do: the AI Strategist pair,
    the Social Media and Marketing Intern, General Management and Growth, and
    Recruiting. The universal rules are written for assessments with stated
    caps and required sections. The AI Strategist pack has neither -- it says
    in as many words that there are no caps, that a missing task scores 1
    rather than ending the grading, and that the only auto-fail is confirmed
    fraud. Unit 14 has caps but scores them: format and scope compliance is a
    5-point row, a written piece under 250 words scores 1, and "Grade what is
    in front of you" is the instruction. Unit 15 has no submission at all, only
    a resume, so caps and required sections do not apply to it. Unit 16 is unit
    14's case again -- an absent video is a 1 on a 5-point row and an absent AI
    note is a 1 on a 10-point row -- and carries the one universal rule it does
    not repeal, on fabricated data, forward by hand. Prepending the universal
    list in any of them would have the grader end candidacies on rules its own
    rubric repeals.

    Opting out is not a way to be lenient: FRAUD_TELLS is separate from this
    list and still reaches every grader.
    """
    own = tuple(grid.get("auto_fails") or ())
    if grid.get("universal_auto_fails") is False:
        return own
    return tuple(UNIVERSAL_AUTO_FAILS) + own


def summary() -> list[dict]:
    """One row per grid: what it covers and whether it is wired to a slug."""
    return [
        {
            "key": grid["key"],
            "unit": grid["unit"],
            "grid_name": grid.get("grid_name"),
            "spike": grid.get("spike"),
            "slugs": grid.get("slugs") or (),
            "roles": len(grid.get("roles") or ()),
            "criteria": len(grid["criteria"]),
            "blocked": grid.get("blocked"),
            # Only interesting when it is not the pack's 70/10/10/10, which is
            # exactly when a coverage table needs to say so.
            "block_points": block_points_of(grid),
            "standard_blocks": block_points_of(grid) == BLOCK_POINTS,
        }
        for grid in GRIDS
    ]
