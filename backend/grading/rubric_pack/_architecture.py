"""
The fixed architecture every grid is measured against: blocks, bands, triage
routes, the universal auto-fails, the fraud tells and the GIA overlay.

Separated from the grids because this is the part that is shared. A change
here moves every family at once; a change in _grids.py moves one.

Re-exported from the package root, so `pack.BLOCKS`, `pack.BANDS` and the rest
are unchanged for every caller.
"""

# ---------------------------------------------------------------------------
# The fixed architecture
#
# "Every rubric scores 100 points in four fixed blocks." The blocks are the
# only thing shared across all 19 grids, so they are also the only level at
# which two candidates in different families can be compared: an Investments
# 62 and a Marketing 62 mean the same decision, and their AI-forwardness rows
# are asking the same question of both.
#
# Five grids depart, deliberately and by instruction, and all five buy the
# same fifth block -- `background`, for the record the resume and the portfolio
# show. The AI Strategist pair splits 40 / 40 / 6 / 7 / 7; the Social Media and
# Marketing Intern grid splits 55 / 10 / 10 / 13 / 12; General Management and
# Growth, which has no assessment behind it, puts all 100 there; Recruiting
# splits 50 / 10 / 10 / 5 / 25. See `block_points_of` below, section 10 of
# either Strategist grid's `notes`, unit 14's second note and unit 16's header.
# Everything else in this module still assumes 70 / 10 / 10 / 10, because
# everything else still is.
# ---------------------------------------------------------------------------

BLOCKS = (
    {
        # Off by default, which is what `points: 0` means here: a grid gets
        # this block only by naming a figure for it in `block_points`, and a
        # grid that does not name one may not carry a criterion in it.
        #
        # Everywhere else in this system the resume is scored OUTSIDE the grid
        # -- config.CV_WEIGHT_BY_SEAT, evaluator's `cv_assessment`, blended
        # afterwards -- precisely so the same evidence cannot be counted twice.
        # A grid that opens this block is choosing the other arrangement, and
        # the two must not both be on: the seat's CV weight has to be 0.0 or
        # the track record is paid for in the grid and again in the blend.
        # config.cv_weight_for is where that pairing is set.
        #
        # It sits FIRST because of the AI Strategist pair, which says to read
        # the resume before the work product: at 40 points an impressive deck
        # pulls an ambivalent background score upward if you grade in the other
        # order, and that is halo rather than evidence. Unit 14 opens the block
        # too but reads it LAST, on purpose -- at 10 points the halo cannot
        # reach far enough to matter, and in an intern pool the live risk is
        # the reverse one, a thin file dragging down work that deserved better.
        # Block order is presentation, not procedure; where a grid wants the
        # other reading order it says so in its own `reviewer` path and
        # `notes`. The other fourteen grids carry no criterion here at all, so
        # this block is skipped for them and their order is unchanged.
        "key": "background",
        "label": "Background and experience",
        "points": 0,
        "asks": "The track record the resume and the Workable profile show: "
                "judgment work, client or executive exposure, and AI taken "
                "into production.",
    },
    {
        "key": "work_product",
        "label": "Work product",
        "points": 70,
        "asks": "The assessment's actual tasks, weighted by JD emphasis.",
    },
    {
        "key": "ai_forwardness",
        "label": "AI-forwardness",
        "points": 10,
        "asks": "Evidence of AI leverage with judgment: what was automated, "
                "what stayed human, how the output was verified.",
    },
    {
        "key": "communication",
        "label": "Communication and judgment",
        "points": 10,
        "asks": "Executive readability, constraint compliance, sound tradeoffs.",
    },
    {
        "key": "spike",
        "label": "Family spike",
        "points": 10,
        "asks": "The one differentiator that separates great from good in this "
                "seat.",
    },
)

# The default architecture: what a block is worth in a grid that does not say
# otherwise. `background` sits at 0 here, so the four-block pack is unchanged.
BLOCK_POINTS = {block["key"]: block["points"] for block in BLOCKS}
BLOCK_LABEL = {block["key"]: block["label"] for block in BLOCKS}

# The blocks a grid derived by the model may use, and the only ones
# evaluator.derive_grid knows how to weight. A derived grid is written from the
# assessment text alone -- there is no resume in front of it -- so it can never
# open the background block, and saying so here is cheaper than discovering it
# as a validation failure after a model call.
DERIVED_BLOCKS = tuple(key for key, points in BLOCK_POINTS.items() if points)

# Decision bands, worded as a ranking rather than a verdict: a reviewer reads
# how strong a submission is, and decides. The bar the interview system knows
# as "advance at 75" has not moved -- Best and Better both sit above it, split
# at 85 so the top of the queue is visible without opening every card.
# Ordered high to low; first match wins.
BANDS = (
    {"key": "best", "label": "Best", "min": 85, "advances": True,
     "meaning": "Clears the bar with room to spare. Interview first."},
    {"key": "better", "label": "Better", "min": 75, "advances": True,
     "meaning": "Clears the bar. Move to interview."},
    {"key": "good", "label": "Good", "min": 60, "advances": False,
     "meaning": "Credible, not yet convincing. Revisit against the rest of "
                "the queue."},
    {"key": "okay", "label": "Okay", "min": 0, "advances": False,
     "meaning": "Does not clear the bar for this seat."},
)

# The lowest score that still advances, kept as one fact rather than a literal
# 75 repeated wherever the bar is drawn.
ADVANCE_MIN = min(band["min"] for band in BANDS if band["advances"])

# "Run triage first, grade second." Six binary checks per grid; the count of
# yeses routes the submission. Triage never advances anyone on its own -- it
# only orders the queue.
TRIAGE_ROUTES = (
    {"key": "priority", "label": "Priority review", "min": 5,
     "meaning": "Grade first, newest first within the tier."},
    {"key": "full", "label": "Full rubric review", "min": 3,
     "meaning": "Grade in the normal queue."},
    {"key": "reject", "label": "Reject without full grading", "min": 0,
     "meaning": "Not worth 15 minutes of a reviewer."},
)

# "Every rubric carries these plus its own." An auto-fail is not a low score:
# it ends the grading. Each grid adds a family-specific list on top.
UNIVERSAL_AUTO_FAILS = (
    "Hard cap violation: over a stated word or length cap, or a required "
    "section missing entirely.",
    "Off-scenario template: an answer that ignores the provided data and "
    "could have been written for any company.",
    "Fabricated facts or numbers where the task supplied data, or arithmetic "
    "hidden where the task requires it shown.",
    "Missing AI process disclosure where the assessment defines one.",
)

# "Fraud tells are handled separately from scoring." These do not produce a
# score at all; they route to the fraud log and the bulk-disqualify flow.
FRAUD_TELLS = (
    "Burner-domain or automated-apply submission.",
    "Identity inconsistency between the written work and the video.",
    "JD-echo: materials that parrot the posting back instead of doing the work.",
    "Template cover letter addressed to the company name in all caps.",
)

# The GIA layer sits outside the 100 and never changes points. Ajaia
# administers no formal instrument today, so only the per-grid proxy signals
# are live; these rules are what governs the day one is added.
GIA_RULES = {
    "administered": False,
    "what_it_is": "A general intelligence assessment is a timed cognitive "
                  "aptitude test measuring general mental ability: speed of "
                  "processing, learning, and adapting to novel problems.",
    "scales": ("Reasoning", "Perceptual Speed", "Number Speed and Accuracy",
               "Word Meaning", "Spatial Visualisation"),
    "percentiles": (
        "70th and above on a primary scale is a growth signal.",
        "30th to 69th is neutral.",
        "Below 30th is a caution flag to probe at interview, never an "
        "automatic reject.",
    ),
    "band_rules": (
        "Breaks ties between candidates within 5 points of each other.",
        "Moves a candidate sitting within 3 points of a band edge by one band "
        "in either direction.",
        "Never moves anyone two bands.",
        "Never rescues an auto-fail, and never rescues a work product below 50.",
        "Never overrides a strong work sample. Work product always dominates.",
    ),
    "administration": (
        "One fixed stage for every candidate in a role, with the stage-2 "
        "assessment invite.",
        "Grade the work product blind to the GIA result.",
        "One fixed instrument, consistent administration, accommodations on "
        "request.",
    ),
    "not_measured": ("domain knowledge", "conscientiousness", "taste",
                     "follow-through", "values"),
}
