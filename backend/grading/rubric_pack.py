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


# ---------------------------------------------------------------------------
# The grids
#
# One entry per scoring grid. `slugs` are the portal assessment slugs this grid
# marks -- the same slugs used for assessments/<slug>.md and in
# config.JOB_ASSESSMENTS -- so a role resolves to its family without a second
# mapping table to keep in sync.
# ---------------------------------------------------------------------------

GRIDS = (

    # -- 1. AI Training ----------------------------------------------------
    {
        "key": "ai_training",
        "unit": "AI Training",
        "entity": "Ajaia",
        "slugs": ("ai-trainer",),
        "roles": ("AI Trainer (5854865)", "Enterprise AI Trainer (5854899)",
                  "Generative AI Trainer (5854903)"),
        "assessment": "Ajaia AI Trainer Assessment (Director Level), 180 minutes",
        "location": "Hybrid New York, 1099 contractor",
        "spike": "Room command under skepticism",
        "seat": "The seat owns Workforce Training and Enablement and is \"not a "
                "role for theoretical educators; it is for authoritative "
                "practitioners who can command a room and demonstrate how AI "
                "solves high-value industry problems in real-time.\" Success is "
                "adoption.",
        "core_skill": "Teaching craft: turning an AI capability into a workflow "
                      "a skeptical non-technical worker uses on Monday.",
        "competencies": (
            {"label": "Instructional design",
             "asks": "One outcome scoped for a named audience.",
             "anchor": "\"prioritize practical application over theoretical "
                       "design\" (Task 1)"},
            {"label": "Live demonstration",
             "asks": "A real tool run on screen, inputs to outputs.",
             "anchor": "\"Execute real-time demonstrations of AI-native "
                       "workflows\" (Task 3B)"},
            {"label": "Communication and presence",
             "asks": "Authority with an unconvinced room.",
             "anchor": "\"educate executive-level stakeholders with "
                       "confidence\" (Task 3A, deck)"},
            {"label": "Adoption orientation",
             "asks": "Training tied to work that changes after it.",
             "anchor": "\"measurable adoption rather than theoretical "
                       "learning\" (Task 1 objective, deck takeaways)"},
            {"label": "AI-native practice",
             "asks": "Daily working use of AI, not familiarity.",
             "anchor": "\"AI tools and automation in a practical, daily "
                       "professional context\" (Workflow Note)"},
            {"label": "Practitioner credibility",
             "asks": "A record a healthcare room respects.",
             "anchor": "\"a sophisticated track record in high-performance "
                       "fields\" (introduction video, prior evidence)"},
        ),
        "criteria": (
            {"key": "session_design", "label": "Session design and deck",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Doable objective, audience named at role level (front "
                    "desk, scheduling, ops managers), timed sections, "
                    "disruption fear answered in the agenda.",
                 3: "Audience generic (\"healthcare staff\"), sections "
                    "untimed, deck replaces the speaker.",
                 1: "Generic \"Intro to AI\" agenda, no healthcare role, "
                    "length outside 30 to 45 minutes.",
             }},
            {"key": "workflow_teachability", "label": "Workflow teachability",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Actual prompt text, the input a participant would paste, "
                    "expected output, failure mode.",
                 3: "Steps given but prompts summarized, not reproducible "
                    "unaided.",
                 1: "Tool categories at headline level, nothing executable.",
             }},
            {"key": "live_demo", "label": "Live demo execution",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Tool on screen running the taught workflow, input to "
                    "output, narrated live, with one honest correction.",
                 3: "Pre-generated result walked through, or a workflow the "
                    "design never promised.",
                 1: "No tool on screen; slides read aloud or the demo "
                    "described in past tense.",
             }},
            {"key": "prior_delivery", "label": "Prior delivery evidence",
             "block": "work_product", "weight": 10,
             "anchors": {
                 5: "Real recording facilitating a live audience, with context "
                    "on who was there, or materials plus walkthrough.",
                 3: "Clip with no audience context, or a walkthrough "
                    "describing a plan not a delivery.",
                 1: "Missing, a re-recorded demo passed as prior delivery, or "
                    "the introduction video reused.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names tools and where used, splits what AI accelerated "
                    "from what the candidate decided, one verification step.",
                 3: "Tools named, the AI versus judgment line asserted, no "
                    "verification.",
                 1: "\"I used ChatGPT to help\", or advocacy with no account "
                    "of this submission.",
             }},
            {"key": "readability", "label": "Readability and constraints",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Structured markdown, video 5 to 8 minutes, introduction 1 "
                    "to 2, viewable folder, ajaia.ai reference, one stated "
                    "tradeoff.",
                 3: "One constraint missed (video length, ajaia.ai reference, "
                    "section order).",
                 1: "Unstructured dump, several constraints missed.",
             }},
            {"key": "room_command", "label": "Room command under skepticism",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Names the disruption fear on camera and answers it with "
                    "the demo, no defensiveness about \"this replaces us\".",
                 3: "Confident delivery, but skepticism is a slide topic not "
                    "handled in the room.",
                 1: "Reads notes, hedges, apologizes for the tool, or plays to "
                    "an imagined friendly audience.",
             },
             "note": "Room command scores audience handling, not demo "
                     "mechanics."},
        ),
        "auto_fails": (
            "No training and demo video.",
            "No AI tool visible on screen at any point (\"prioritize "
            "demonstration over explanation\").",
            "Prior training evidence absent -- a separately required "
            "deliverable.",
            "Video over 12 minutes or under 3.",
            "An unviewable Drive link: one access request, then reject.",
        ),
        "red_flags": (
            "No ajaia.ai reference, which the assessment calls possible "
            "incompleteness -- deduct under constraints.",
            "Different people in the two videos: routes to the fraud log.",
            "Vendor-branded prior evidence.",
            "JD phrasing echoed in the design.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: same person in both "
                                      "videos, prior evidence not a re-recorded "
                                      "demo, no JD phrasing in the design."},
            {"key": "complete", "label": "Complete: design, deck, demo video, "
                                         "introduction video, prior evidence, "
                                         "Workflow Note."},
            {"key": "caps", "label": "Caps: video 5 to 8 minutes, introduction "
                                     "1 to 2, session 30 to 45."},
            {"key": "scenario", "label": "Scenario: names a healthcare role and "
                                         "task, addresses skepticism."},
            {"key": "specific", "label": "One checkable specific: a real prompt, "
                                         "a named tool, a minutes-saved figure."},
            {"key": "ai_note", "label": "Workflow Note non-generic."},
        ),
        "tells": {
            "strong": "A real prompt with real healthcare input in the deck.",
            "weak": "A narrated deck with no tool on screen.",
        },
        "gia": {
            "primary": ("Word Meaning", "Reasoning"),
            "secondary": ("Perceptual Speed",),
            "why": "Word Meaning because the act is converting a capability "
                   "into language a front desk worker accepts. Reasoning "
                   "because one workflow must generalize across operations "
                   "staff, administrative teams and managers in 30 to 45 "
                   "minutes. Perceptual Speed because a live demo means "
                   "catching a wrong output and recovering.",
            "proxies": (
                "How much of a messy healthcare process compresses into a "
                "teachable step sequence in Task 1.",
                "Vocabulary control on the capability slide.",
                "Recovery when the Task 3B demo returns something imperfect.",
            ),
        },
        "reviewer": {
            "path": (
                "0-2: confirm six deliverables and video lengths, run triage.",
                "2-6: watch the demo segment first, scoring Live demo and Room "
                "command.",
                "6-9: read the design against that demo -- did it deliver what "
                "was promised?",
                "9-12: page the deck, read the Workflow Note, spot-check prior "
                "evidence.",
                "12-15: skim the introduction video and total.",
            ),
            "calibration": "Watch the demo before the deck. Deck polish "
                           "inflates scores for candidates who never put a tool "
                           "on screen.",
            "probes": (
                "Teach one workflow step cold to an interviewer playing a "
                "skeptical scheduler.",
                "What gets cut if 45 minutes becomes 20?",
                "One training that did not drive adoption, and what changed "
                "after.",
            ),
        },
        "gaps": (
            "No numeric task here, so no proxy for Number Speed and Accuracy.",
            "The introduction video asks for background and interest, not "
            "salary expectations -- collect compensation fit at screen against "
            "the $30 to $300 per hour band.",
            "The JD body says \"Remote (US) or Hybrid\" while structured fields "
            "say New York, hybrid. Grade to hybrid New York, and do not screen "
            "on the Master's those fields require but the Requirements never "
            "mention.",
        ),
    },

    # -- 2. Marketing ------------------------------------------------------
    {
        "key": "marketing",
        "unit": "Marketing",
        "entity": "Ajaia",
        "slugs": ("marketing-advertising",),
        "roles": ("Google Ads Specialist (5909764)",
                  "Performance Marketing Manager (5909716)",
                  "Digital Marketing Specialist (5909732)"),
        "assessment": "Marketing Strategy & Execution Assessment, 120 minutes "
                      "plus a 10-15 minute video",
        "location": "Remote, offshore (Philippines)",
        "spike": "Lead-economics arithmetic",
        "seat": "The seat owns paid acquisition end to end: \"Build, launch, and "
                "manage Google Ads campaigns across Ajaia's products.\" Judged "
                "on outcome: \"Drive measurable, low-cost leads that convert, "
                "not vanity traffic.\"",
        "core_skill": "Build a campaign a practitioner would recognize, then "
                      "defend every number in it.",
        "competencies": (
            {"label": "Search intent modeling",
             "asks": "Research language versus buying language.",
             "anchor": "\"separating informational from transactional intent\" "
                       "(Task 1A)"},
            {"label": "Ad build craft",
             "asks": "Copy that fits platform limits without losing the buying "
                     "term.",
             "anchor": "\"character limits, line 1/2/3 copy\" (Task 1B)"},
            {"label": "Campaign architecture and measurement",
             "asks": "Makes the campaign measurable before launch.",
             "anchor": "\"the full sales funnel\" (Task 1C)"},
            {"label": "Lead economics",
             "asks": "A budget carried to an expected cost per lead that drives "
                     "bidding.",
             "anchor": "\"low-cost leads that convert\" (Task 1C)"},
            {"label": "Practitioner credibility",
             "asks": "Defends the campaign line by line; shows a real account "
                     "with decisions isolated.",
             "anchor": "\"certification is a baseline, not proof of skill\" "
                       "(Task 2)"},
            {"label": "AI-native execution",
             "asks": "Compresses research and drafting with AI, then verifies "
                     "it.",
             "anchor": "\"AI usage is a core execution requirement, not "
                       "optional tooling\" (Task 1)"},
        ),
        "criteria": (
            {"key": "keyword_strategy",
             "label": "Keyword strategy and intent split (1A)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "Four lists populated; buying terms separated from research "
                    "terms; negatives block job-seeker and free-course traffic, "
                    "with reasons.",
                 3: "Lists present, intent blurred, negatives generic.",
                 1: "Keyword dump, or no negatives.",
             }},
            {"key": "ad_build",
             "label": "Ad build under character limits (1B)",
             "block": "work_product", "weight": 14,
             "anchors": {
                 5: "Counts printed per line and correct on recount; headlines "
                    "carry the legal qualifier and a buying term; bottom-funnel "
                    "call to action.",
                 3: "Counts wrong on recount; copy suits any buyer.",
                 1: "No counts, or copy that would not fit.",
             }},
            {"key": "campaign_architecture",
             "label": "Campaign architecture and measurement (1C)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "Nine settings answered; tracking names the lead action "
                    "counted; geo, schedule, device follow from how law firms "
                    "buy; landing page matched.",
                 3: "Defaults with no reasoning; counted action undefined.",
                 1: "Vocabulary only; tracking absent.",
             }},
            {"key": "campaign_defense",
             "label": "Campaign defense and account evidence (Parts B and C)",
             "block": "work_product", "weight": 24,
             "anchors": {
                 5: "Each headline and description walked individually with "
                    "audience, intent, and exclusions; Part C gives platform, "
                    "budget scale, a before-and-after metric, decisions "
                    "isolated.",
                 3: "Document-level walkthrough, or results without personal "
                    "decisions.",
                 1: "Narrates the doc, or involvement undisclosed.",
             }},
            {"key": "ai_leverage", "label": "AI leverage with verification",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names tool and step (keyword expansion, negative lists, ad "
                    "variants), what stayed human, how output was checked.",
                 3: "Mentions AI per task, no verification.",
                 1: "\"I used ChatGPT to help\", or nothing.",
             }},
            {"key": "structure", "label": "Structure and constraint compliance",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Markdown mirrors 1A, 1B, 1C; video inside the window with "
                    "parts near 2-4, 3-5, 5-8 minutes; salary stated plainly.",
                 3: "Sections merged or video overlong; salary vague.",
                 1: "Unstructured, far over time, or salary skipped.",
             }},
            {"key": "cpl_math", "label": "Cost per qualified lead math (1C)",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Budget resolves into clicks, leads and CPL through a stated "
                    "cost per click and conversion rate; bidding follows the CPL "
                    "target; assumptions labeled.",
                 3: "Isolated figures, no chain.",
                 1: "No numbers, or budget and bidding contradict.",
             }},
        ),
        "auto_fails": (
            "No character counts in 1B, or counts that fail a recount.",
            "No negative keyword list, a required 1A output.",
            "Salary expectation missing from Part A.",
            "A document that never names law firms or legal departments.",
            "Part C claiming an account while dodging \"what decisions you "
            "personally made\". Overstatement routes to the fraud log.",
            "JD-echo: the posting's own phrases handed back as analysis.",
        ),
        "red_flags": (
            "Nine settings answered in nine words.",
            "Presentation polish standing in for evidence -- candidates are "
            "told explicitly it is \"not your presentation skills\".",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, JD phrasing parroted back, or a "
                                      "stock deck."},
            {"key": "complete", "label": "Complete: 1A, 1B, 1C and a video that "
                                         "plays."},
            {"key": "format", "label": "Format: structured Markdown, video "
                                       "inside 10-15 minutes."},
            {"key": "scenario", "label": "Scenario: names law firms or legal "
                                         "departments."},
            {"key": "number", "label": "One checkable number: a character "
                                       "count, budget split, CPL target, or "
                                       "account metric."},
            {"key": "ai_note", "label": "Process disclosure naming tools and "
                                        "steps."},
        ),
        "tells": {
            "strong": "Negatives with reasons attached.",
            "weak": "Nine settings in nine words.",
        },
        "gia": {
            "primary": ("Number Speed and Accuracy", "Perceptual Speed"),
            "secondary": ("Reasoning",),
            "why": "The seat is daily bid arithmetic, mismatch-catching across "
                   "long keyword and copy lists, and intent classification of "
                   "new search terms.",
            "proxies": (
                "Whether the 1B counts survive a recount.",
                "Whether the 1A negatives anticipate this scenario's bleed.",
                "Whether budget, CPC, conversion rate and CPL agree in 1C.",
                "Whether the intent split holds across the three keyword "
                "buckets.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage.",
                "2:00 1A keywords.",
                "5:00 recount three headlines and two descriptions against "
                "platform limits, 30 and 90 characters.",
                "7:00 1C architecture, then the spike on numbers alone.",
                "9:30 video: 60 seconds of Part B plus Part C and the salary "
                "answer.",
                "13:30 last two blocks.",
            ),
            "calibration": "Presentation quality is not evidence. A halting "
                           "speaker who names the excluded keyword and why "
                           "outscores a fluent one narrating the doc.",
            "per_title": "Google Ads Specialist: weight keywords and ad build. "
                         "Performance Marketing Manager: weight architecture "
                         "and the spike; this seat owns budget. Digital "
                         "Marketing Specialist: accept Part C evidence from "
                         "LinkedIn or Meta Ads.",
            "probes": (
                "What CPL did the Part C account run at, and what moved it?",
                "Rewrite one headline now to fit the limit, keeping the buying "
                "term.",
                "Which negative keyword would you add after week one, and what "
                "triggers it?",
            ),
        },
        "gaps": (
            "No word cap on Task 1; the hard limits are the 120-minute timer "
            "and the video window.",
            "No \"AI Workflow Note\" is required. Score AI-forwardness against "
            "the cover instruction to \"Show your process, not just final "
            "answers\". Absent disclosure is the equivalent auto-fail.",
            "The scenario supplies no budget, CPC or conversion data, so spike "
            "numbers are candidate-generated. Score the chain's internal "
            "consistency, not accuracy against a key.",
            "The three postings are byte-identical below the title line, so "
            "per-title emphasis reads role intent from the family map, not JD "
            "text.",
        ),
    },

    # -- 3. Analysts and AI consulting -------------------------------------
    {
        "key": "analysts",
        "unit": "Analysts and AI Consulting",
        "entity": "Ajaia",
        "slugs": ("workflow-analyst",),
        "roles": ("AI Process Consultant (5914684)",
                  "AI Workflow Analyst (5914699)",
                  "Business Process Consultant (5914706)"),
        "assessment": "Business Workflow Analyst Assessment (61), 120 minutes",
        "location": "Remote Philippines, mid-senior, U.S. clients",
        "spike": "Automation restraint",
        "seat": "The consultant helps clients \"understand, document, and "
                "improve their business operations,\" and must \"create clear "
                "process documentation that enables future automation and "
                "optimization.\"",
        "core_skill": "Turn an incomplete conversation into a process someone "
                      "else can run, and say what to change first.",
        "competencies": (
            {"label": "Process comprehension and systems thinking",
             "asks": "Reconstructs actors, systems and branch points from "
                     "narrative.",
             "anchor": "\"Identify process dependencies, decision points, and "
                       "bottlenecks\" (Task 1A)"},
            {"label": "Gap and assumption discipline",
             "asks": "Splits stated fact from inference.",
             "anchor": "\"clearly label your assumptions\" (Tasks 1B, 2B)"},
            {"label": "Discovery questioning",
             "asks": "Questions that close a named gap.",
             "anchor": "\"validate business rules, understand ownership, and "
                       "identify exceptions\" (Task 1C)"},
            {"label": "Documentation craft",
             "asks": "Artifacts a non-specialist can follow.",
             "anchor": "\"understood by technical and non-technical "
                       "audiences\" (Tasks 2A, 2B)"},
            {"label": "Improvement and automation reasoning",
             "asks": "Findings into prioritized change.",
             "anchor": "\"translate business processes into automation "
                       "requirements\" (Tasks 3A to 3C)"},
        ),
        "criteria": (
            {"key": "process_comprehension",
             "label": "Process comprehension (1A)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Intake through invoicing; managing partner, project "
                    "manager, accounting mapped to HubSpot, Asana, Drive, "
                    "Slack, spreadsheet; fit and fixed-fee versus hourly "
                    "branches.",
                 3: "Stages ordered, systems named, one path, billing branch "
                    "missed.",
                 1: "Narrative retell, no stages, systems without owners.",
             }},
            {"key": "gaps_questions",
             "label": "Gaps, assumptions, questions (1B, 1C, 2B)",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Fact separated from inference; ownership gaps named (who "
                    "authorizes work before setup, who chases late entries); "
                    "questions target business rules and exceptions.",
                 3: "Gaps plausible, some assumptions labeled, inference stated "
                    "as fact.",
                 1: "Process treated as understood; questions thin or already "
                    "answered.",
             }},
            {"key": "documentation",
             "label": "Documentation craft (2A, 2B)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Map runs lead intake to billing with decision points and "
                    "owner lanes; document carries all five sections including "
                    "exceptions and clarifications.",
                 3: "Diagram linear, no branches or owners; exceptions or "
                    "clarifications missing.",
                 1: "No diagram; retell without owners or systems.",
             }},
            {"key": "improvement",
             "label": "Improvement and AI reasoning (3A to 3C)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Symptoms traced to root causes, not one fix each; priority "
                    "basis stated; each 3C item carries its four elements and a "
                    "named failure like late entries delaying billing.",
                 3: "Right problems and fixes, priority asserted; 3C items "
                    "missing an element.",
                 1: "Generic advice untied to this firm, or an AI wish list.",
             }},
            {"key": "ai_leverage", "label": "AI leverage with judgment",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Decomposition shown (model pass for the stage list, human "
                    "pass for the hedges), what stayed human, verification "
                    "against the transcript.",
                 3: "Tools named, no verification, no AI-versus-human split.",
                 1: "\"I used ChatGPT to help\", no disclosure, or unedited "
                    "model scaffolding.",
             }},
            {"key": "readability", "label": "Readability and constraints",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Markdown, sectioned to the task numbering, three "
                    "deliverables findable in seconds, tradeoffs stated where "
                    "the transcript contradicts itself.",
                 3: "Organized but verbose, findings buried under the process "
                    "dump.",
                 1: "One block, or too long to find the deliverables.",
             }},
            {"key": "automation_restraint", "label": "Automation restraint",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Names which fixes are a required field, template or owner "
                    "rather than an AI agent, and sequences AI behind process "
                    "definition.",
                 3: "Sensible mix, automation and AI undistinguished, "
                    "sequencing unexplained.",
                 1: "An AI agent per symptom, including ones a required HubSpot "
                    "field closes.",
             }},
        ),
        "auto_fails": (
            "No workflow diagram (2A) in any form; prose describing one scores "
            "1 rather than passing.",
            "Invented firm data. The transcript supplies no numbers, so any "
            "headcount, cycle time or savings figure asserted as fact is "
            "fabrication. Labeled an estimate, fine.",
            "Off-scenario: names none of HubSpot, Asana, the proposal step or "
            "the time-entry delay.",
            "Duplicates filed under alternate addresses.",
        ),
        "red_flags": (
            "Missing AI disclosure scores 1, not an auto-fail -- no named AI "
            "Workflow Note is required here, only \"Show your process\".",
            "Finding all seven of the obvious problems and stopping there.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: no duplicate address, no "
                                      "posting echo, no template cover letter "
                                      "addressed to the company name in all "
                                      "caps."},
            {"key": "complete", "label": "All three deliverables under either "
                                         "heading scheme, diagram included."},
            {"key": "format", "label": "Markdown, sectioned to the task "
                                       "structure, not one essay."},
            {"key": "scenario", "label": "Names HubSpot, Asana and one of Drive, "
                                         "Slack or the spreadsheet, plus actors "
                                         "rather than \"the team\"."},
            {"key": "claim", "label": "One checkable claim: a named decision "
                                      "point, an exception such as work "
                                      "starting before setup, or a labeled "
                                      "estimate."},
            {"key": "ai_note", "label": "AI disclosure non-generic: "
                                        "decomposition or verification "
                                        "described, not a tool name."},
        ),
        "tells": {
            "strong": "A contradiction in the transcript caught and written up "
                      "as a rule question.",
            "weak": "The seven obvious findings, listed.",
        },
        "gia": {
            "primary": ("Reasoning", "Perceptual Speed"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "The transcript is hedged, so the process must be deduced, "
                   "and the best findings are contradictions: \"Occasionally "
                   "consultants start work before everything is fully set up\" "
                   "against the intended process stated one line earlier.",
            "proxies": (
                "Abstraction of a process from narrative in 1A.",
                "Error detection on the candidate's own inference in 1B and 2B.",
                "Prioritization without data in 3B.",
                "Nine sub-deliverables inside 120 minutes.",
            ),
        },
        "reviewer": {
            "path": (
                "Triage (1 min).",
                "Open 3C first -- widest variance, and where the spike lives "
                "(3).",
                "Read 1C; question quality separates analysts fastest (2).",
                "Scan 2B for its five sections and the diagram for branches and "
                "owners (3).",
                "Skim 1A and 1B for fact-versus-inference discipline (2).",
                "Check AI disclosure and readability (2), then score and band "
                "(2).",
            ),
            "calibration": "Nearly every competent submission finds the same "
                           "seven: inconsistent HubSpot logging, no proposal "
                           "template, work starting before setup, fragmented "
                           "documentation, weak Asana compliance, late time "
                           "entries, informal resourcing. Finding all seven is "
                           "a 3, not a 5. The 5s come after that: root causes "
                           "over a list, an ordering with a reason, a baseline "
                           "before impact is claimed, and calling a fix a "
                           "required field, not an AI agent.",
            "probes": (
                "Which recommendation should not go first, and what breaks out "
                "of order?",
                "What would you measure in week one to baseline the impact you "
                "claimed, and who gives it to you?",
                "The managing partner says the project manager assigns work, "
                "then that he sometimes assigns it directly: defect or "
                "exception, and how would you write the rule?",
            ),
        },
        "gaps": (
            "No length caps. Judge verbosity against \"avoid unnecessary "
            "verbosity\" and the time budget.",
            "No numbers and no required arithmetic, so the Number Speed proxy "
            "is weak; read it only from whether the candidate estimates "
            "defensibly and asks for a baseline.",
            "Older submissions number tasks 1./2./3.; grade both schemes.",
        ),
    },

    # -- 4. Customer Success -----------------------------------------------
    {
        "key": "customer_success",
        "unit": "Customer Success",
        "entity": "RDAI Labs / Ethos Intelligence",
        "slugs": ("ce-product-success",),
        "roles": ("Customer Success Manager (5917303)",
                  "EdTech Customer Success Manager (5947887)",
                  "Customer Experience & Product Success Lead (5917302)"),
        "assessment": "Client Success Assessment, 90 minutes plus a 5-8 minute "
                      "video",
        "location": "Remote, Fort Lauderdale",
        "spike": "Product signal loop",
        "seat": "The seat owns the account after signature and is also the "
                "routing layer, \"the bridge between customers and our Product "
                "and Engineering teams,\" on a platform serving \"more than "
                "80,000 students, 5,000 teachers, and 100 schools.\"",
        "core_skill": "Run a rollout and a live queue at once, and write one "
                      "incident correctly for a teacher and for an engineer.",
        "competencies": (
            {"label": "Onboarding architecture",
             "asks": "Phases a district rollout with owners.",
             "anchor": "\"onboarding and product adoption\" (Part 1)"},
            {"label": "Triage under load",
             "asks": "Orders issues by who is blocked.",
             "anchor": "\"manage multiple customer issues simultaneously\" "
                       "(Part 2, Task 1)"},
            {"label": "Dual-register incident writing",
             "asks": "One failure written twice: plainly for a teacher, "
                     "reproducibly for an engineer.",
             "anchor": "\"explain software functionality to non-technical "
                       "users\" and \"reproduce bugs\" (Part 2, Task 2)"},
            {"label": "Support-operations diagnosis",
             "asks": "Finds the real constraint in a metric set.",
             "anchor": "\"improving support resources\" (Part 3, A and B)"},
            {"label": "AI-native execution",
             "asks": "Applies AI to support work, says where it stops.",
             "anchor": "\"Familiarity with AI tools or generative AI "
                       "applications\" (Part 3C)"},
        ),
        "criteria": (
            {"key": "onboarding_plan", "label": "Onboarding plan (Part 1)",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "One page, six elements, four owners distinct, one risk "
                    "with a proactive move. Rostering before teacher training "
                    "across 42 schools.",
                 3: "Owners blur; metrics untargeted; risk without a move.",
                 1: "Elements missing, no phases, district numbers absent.",
             }},
            {"key": "triage",
             "label": "Triage and escalation (Part 2, Task 1)",
             "block": "work_product", "weight": 14,
             "anchors": {
                 5: "All three ranked on a stated basis, escalation called each: "
                    "B record integrity growing per sync, C a multi-teacher "
                    "defect, A one teacher's access.",
                 3: "Generic urgency; escalation on some.",
                 1: "No ranking, A and B answered anyway, or over cap.",
             }},
            {"key": "ticket_c",
             "label": "Ticket C, both audiences (Part 2, Task 2)",
             "block": "work_product", "weight": 22,
             "anchors": {
                 5: "Teachers: under cap, no jargon, names the Grade 8 Science "
                    "quiz failure, one move this week, an update by a stated "
                    "time. Engineering: six fields, numbered steps ending at "
                    "the failure, severity with a basis, real evidence, asks "
                    "whether other grades fail.",
                 3: "One register for both; no workaround; steps narrative; "
                    "severity unjustified; evidence listed as \"logs\".",
                 1: "Never names quiz generation, fields missing, error codes "
                    "invented, or over cap.",
             }},
            {"key": "cx_plan", "label": "CX plan (Part 3, A and B)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "A picks one challenge over the alternatives -- low doc use, "
                    "password resets, unfindable templates behind the 14-hour "
                    "first response. B: three initiatives, each tagged adoption, "
                    "satisfaction or efficiency and tied to a metric.",
                 3: "Challenge undefended; initiatives untied to the metrics.",
                 1: "Several challenges, over three initiatives, or \"hire more "
                    "staff\".",
             }},
            {"key": "ai_leverage",
             "label": "AI leverage with verification (Part 3C)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Two opportunities, each with impact and a real limit: "
                    "deflection cannot cover district-specific permissions; "
                    "drafts reviewed before student data. Says what AI drafted "
                    "and how it was checked.",
                 3: "Token limit or one missing; a tool name, no step.",
                 1: "Under two, no limit, or \"I used ChatGPT to help\".",
             }},
            {"key": "constraints", "label": "Constraint compliance",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Every cap held (one page, 150/150/100, three, two), "
                    "Markdown by section, video covering all four walkthrough "
                    "items and salary.",
                 3: "One cap over or a section unlabeled; video skips an item.",
                 1: "Caps broken, unstructured, or no video link.",
             }},
            {"key": "product_signal", "label": "Signal routed to product",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Parts 2 and 3 as one system: duplicate accounts and admin "
                    "permissions questions are one provisioning surface. Names a "
                    "tag taxonomy or repeat threshold opening a product ticket.",
                 3: "One-time handoff, no threshold, no metric traced to a "
                    "ticket.",
                 1: "Handled alone; Product only receives Ticket C.",
             }},
        ),
        "auto_fails": (
            "No video link. It is a required section.",
            "Cap breach past 50 percent: triage or teacher response over 225 "
            "words, top challenge over 150.",
            "Never names Ethos Educate, Ethos Study, Grade 8 Science quiz "
            "generation or roster synchronization. Off-scenario template.",
            "Invented error codes, log excerpts or district names; the scenario "
            "supplies none.",
            "Student personally identifiable information included, requested or "
            "offered as bug evidence -- a hard stop in K-12.",
            "A fix date or service level promised to teachers with no basis.",
            "Part 3C absent (the equivalent of a missing AI disclosure).",
        ),
        "red_flags": (
            "JD-echo: the \"bridge between customers and our Product and "
            "Engineering teams\" line recited, not performed.",
            "Part 3C present but with no disclosure scores 1.",
            "A warm, fluent teacher response that never names what broke.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, video identity mismatch, JD "
                                      "phrasing returned."},
            {"key": "complete", "label": "Complete: Part 1, triage, both Ticket "
                                         "C artifacts, 3A, 3B, 3C, a playable "
                                         "video."},
            {"key": "format", "label": "Format: one page, 150/150/100, three "
                                       "initiatives, two opportunities."},
            {"key": "scenario", "label": "Scenario named: Grade 8 Science quiz "
                                         "generation, roster sync, or the "
                                         "42-school district."},
            {"key": "number", "label": "One checkable number: a target on the "
                                       "14-hour response, a milestone date, a "
                                       "severity basis."},
            {"key": "ai_note", "label": "AI evidence specific."},
        ),
        "tells": {
            "strong": "Ticket A ranked below B or C with a reason.",
            "weak": "An apology that never names what broke.",
        },
        "gia": {
            "primary": ("Word Meaning", "Perceptual Speed"),
            "secondary": ("Reasoning",),
            "why": "The seat is register-switching across teachers, "
                   "administrators and district IT, plus error-spotting in a "
                   "queue arriving faster than it clears.",
            "proxies": (
                "Whether the teacher response and the bug report read as two "
                "registers or one -- the cleanest Word Meaning proxy here.",
                "Whether three tickets order correctly inside 150 words.",
                "Whether 3A pulls one constraint from eight mixed bullets.",
                "Whether 3B follows 3A.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage, word-counting the four capped items.",
                "2:00 Part 2 next: the triage and the two Ticket C artifacts "
                "separate the field fastest and the register shift shows in one "
                "pass.",
                "5:00 Part 1, checking owners stay distinct.",
                "9:00 Part 3.",
                "12:00 video at 1.5x for the tradeoff answer, salary and "
                "identity.",
                "14:00 spike, total.",
            ),
            "calibration": "Do not pay for polish in the teacher response. A "
                           "warm, fluent 150 words that never name the quiz "
                           "failure and leave the teacher nothing to do is a 1, "
                           "not a 4.",
            "per_title": "Manager titles weight triage and Part 1; the Lead "
                         "weights Part 3 and the spike.",
            "probes": (
                "District IT says the duplicate accounts must clear today for "
                "state reporting. What changes in your order, and what do you "
                "tell the teachers waiting on quiz generation?",
                "Name a time a pattern in your queue changed the product. What "
                "evidence did you bring?",
                "Your first initiative assumes teachers use documentation they "
                "ignore today. What makes it different?",
            ),
        },
        "gaps": (
            "No AI Workflow Note is required. Score AI-forwardness on Part 3C "
            "plus the cover line \"Show your process where asked.\"",
            "The body header says \"video: 10-15 min\", the video section 5-8. "
            "Penalize neither.",
            "The three postings are identical below the title line, with empty "
            "About and Responsibilities sections.",
        ),
    },

    # -- 5A. Executive Operations, Chief of Staff --------------------------
    {
        "key": "exec_ops_cos",
        "unit": "Executive Operations",
        "grid_name": "Grid A. Chief of Staff",
        "entity": "Ajaia",
        "slugs": ("chief-of-staff",),
        "roles": ("Chief of Staff, AI-Enabled CEO Operations Partner (5906553)",),
        "assessment": "Chief of Staff Assessment (assignment 63), 180 minutes",
        "location": "On-site, Fort Lauderdale",
        "spike": "Decision custody",
        "seat": "The Chief of Staff exists to \"increase the CEO's decision "
                "velocity, execution throughput, and organizational leverage,\" "
                "owning \"the CEO's central operating dashboard\" so \"no "
                "critical commitment or follow-up falls through the cracks.\"",
        "core_skill": "Turn a noisy day into decisions, owners and open loops, "
                      "and know which loops are yours to close.",
        "competencies": (
            {"label": "Prioritization under ambiguity",
             "asks": "Ranks when everything is called urgent.",
             "anchor": "\"Filter noise and prioritize the highest-impact "
                       "issues\" (2C)"},
            {"label": "Executive operating systems",
             "asks": "Cadence and thresholds that outlive the builder.",
             "anchor": "\"Own and maintain the CEO's central operating "
                       "dashboard\" (2A)"},
            {"label": "Executive synthesis",
             "asks": "Many inputs into one actionable page.",
             "anchor": "\"briefing documents ... with minimal revisions\" (2B)"},
            {"label": "Accountability without authority",
             "asks": "Tasks people who do not report to them.",
             "anchor": "\"Ability to influence without formal authority\" "
                       "(Task 3)"},
            {"label": "AI-native execution with checkpoints",
             "asks": "Uses AI on volume work, names where it stops.",
             "anchor": "\"Leverage AI tools to improve executive productivity\" "
                       "(Task 1)"},
        ),
        "criteria": (
            {"key": "prioritization", "label": "Prioritization (2C)",
             "block": "work_product", "weight": 22,
             "anchors": {
                 5: "Four initiatives ranked against the metrics: 78 percent "
                    "approval rate, 3 churn accounts, 4 percent expansion. "
                    "Deferral named with its cost; 90-day metrics move a "
                    "supplied number.",
                 3: "Generic rationale; nothing deferred; metrics not numeric.",
                 1: "No ranking, all four critical, or metrics restating the "
                    "title.",
             }},
            {"key": "crisis",
             "label": "Crisis response (Task 3, less Decision Authority)",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "Sequenced against both clocks, the 10-day renewal and "
                    "72-hour absence: 18 percent account first, the two waiting "
                    "prospects messaged before the delay leaks. Four leaders, "
                    "one workstream each, escalation event-based.",
                 3: "Priorities without the clocks; leaders named, outcomes "
                    "vague.",
                 1: "Waits for the CEO, or the 18 percent account waits three "
                    "days.",
             }},
            {"key": "dashboard", "label": "Dashboard (2A)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "Five required elements. Each of the six metrics carries a "
                    "threshold that fires, time-to-fill past 58 days upward. "
                    "Cadence named and dated.",
                 3: "Escalation a color, no threshold; cadence \"regular\".",
                 1: "Metric list only, an element missing, or invented metrics.",
             }},
            {"key": "ceo_update", "label": "CEO update (2B)",
             "block": "work_product", "weight": 14,
             "anchors": {
                 5: "One page, five sections; decisions posed with options and a "
                    "recommendation, not status. Risks name the 3 accounts and "
                    "the 3-week slip.",
                 3: "Sections present; decisions are a status list.",
                 1: "Over a page by half, a section missing, or no decision "
                    "asked.",
             }},
            {"key": "ai_leverage",
             "label": "Leverage plan (Task 1) and AI Workflow Note",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "1B and 1C draw the automation line on a rule, 1D shows "
                    "hours-saved arithmetic and a baseline. The Note ties tools "
                    "to steps and names one rejected output.",
                 3: "Line asserted without a rule; tools plus general "
                    "acceleration.",
                 1: "\"Automate everything\", or \"I used ChatGPT to help\". No "
                    "Note at all is an auto-fail, not a 1.",
             }},
            {"key": "structure", "label": "Structure and constraints",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Seven deliverables labeled in Markdown, update inside one "
                    "page, video 8-12 minutes, salary and all five walkthrough "
                    "items.",
                 3: "One miss: video short, an item skipped, a deliverable "
                    "unlabeled.",
                 1: "No video link, unstructured, or the page cap ignored.",
             }},
            {"key": "decision_authority",
             "label": "Decision Authority (Task 3)",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "A rule with a threshold: reversible and under it is the "
                    "Chief of Staff's call; price, headcount and signed "
                    "agreements wait. Names a mechanism that lowers the 78 "
                    "percent.",
                 3: "Two lists, no rule, no link to the 78 percent.",
                 1: "All routed to the CEO, or renewal terms claimed.",
             }},
        ),
        "auto_fails": (
            "A missing deliverable of the seven.",
            "No video link.",
            "The six supplied metrics replaced by invented figures.",
            "An invented client, investor or dollar amount -- the scenario gives "
            "percentages only.",
            "No AI Workflow Note at all.",
            "JD-echo of \"protect the CEO's time and maximize their "
            "effectiveness\", or an answer that never touches the four "
            "initiatives.",
        ),
        "red_flags": (
            "A missing ajaia.ai reference is not an auto-fail; the cover says "
            "such submissions \"may be considered incomplete\", so deduct in "
            "the communication block.",
            "Fluent completeness: every section present, clean formatting, and "
            "the CEO's reversals written up as settled decisions.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, identity mismatch, JD phrasing "
                                      "returned."},
            {"key": "complete", "label": "Complete: seven deliverables, video "
                                         "plays."},
            {"key": "caps", "label": "Caps: one page on the update, Markdown by "
                                     "section."},
            {"key": "scenario", "label": "Scenario named: the four initiatives "
                                         "and six metrics."},
            {"key": "number", "label": "One checkable number reused: 58 days, "
                                       "78 percent."},
            {"key": "ai_note", "label": "AI note specific."},
        ),
        "tells": {
            "strong": "Open questions carrying dates.",
            "weak": "A clean document in which nothing is unresolved.",
        },
        "gia": {
            "primary": ("Perceptual Speed", "Reasoning"),
            "secondary": ("Word Meaning",),
            "why": "The seat takes fast, messy, partly contradictory input and "
                   "must find the error and the dependency before anyone asks. "
                   "Weighted harder for the Chief of Staff seat.",
            "proxies": (
                "Whether the reversals on dashboards, the board deck and the QA "
                "Lead are caught as reversals.",
                "Whether a threshold on the dashboard actually fires against a "
                "supplied number.",
                "Whether the two clocks in Task 3 both drive the sequence.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage and page-count the update.",
                "2:00 2C with Task 3 -- they carry 36 of the 70 and separate "
                "the field fastest.",
                "6:00 dashboard, checking a threshold fires.",
                "9:00 update, Task 1, AI note.",
                "12:00 video at 1.5x for the tradeoff answer, salary, identity.",
                "14:00 spike, total.",
            ),
            "calibration": "Do not pay for fluent completeness. Every section "
                           "present, clean formatting, and the CEO's reversals "
                           "written up as settled decisions is a 1 on the "
                           "spike, not a 4.",
            "probes": (
                "The 18 percent account and the two waiting prospects need "
                "different things from the same launch date. What do you tell "
                "each?",
                "Name one decision here you would have made without the CEO, "
                "and what would have made that wrong.",
                "The closing recap left items out. What in your system catches "
                "them in the room?",
            ),
        },
        "gaps": (
            "Assignment 63 front matter says 180 minutes, its cover 120. Do not "
            "penalize time allocation.",
            "The Chief of Staff posting has no Responsibilities section, so "
            "anchors quote Requirements, and it reads \"Our client is looking "
            "for a Chief of Staff\" while the account is Ajaia.",
            "The candidate-facing table (Strategic Judgment 25, Prioritization "
            "25, Executive Communication 20, Systems Thinking 15, Crisis "
            "Leadership 10, AI-Native 5) is preserved, not overridden; AI-Native "
            "rises to 10, which the cover supports by listing AI Leverage first "
            "of the four areas evaluated.",
        ),
    },

    # -- 5B. Executive Operations, Operations Associate --------------------
    {
        "key": "exec_ops_associate",
        "unit": "Executive Operations",
        "grid_name": "Grid B. Operations Associate",
        "entity": "Ajaia",
        "slugs": ("operations-associate",),
        "roles": ("Executive Operations Associate (5924849)",),
        "assessment": "Operations Associate Assessment (assignment 60), 120 "
                      "minutes",
        "location": "On-site, Fort Lauderdale",
        "spike": "Decision custody",
        "seat": "The Executive Operations Associate builds \"systems for "
                "capturing meeting notes, decisions, and follow-up actions\" "
                "for RDI, CSUSA and RAD. AI is mandatory: \"a core execution "
                "requirement, not optional tooling.\"",
        "core_skill": "Turn a noisy day into decisions, owners and open loops, "
                      "and know which loops are yours to close.",
        "competencies": (
            {"label": "Executive synthesis",
             "asks": "Many inputs into one actionable page.",
             "anchor": "\"meeting summaries, briefing documents\" (Task 1)"},
            {"label": "Prioritization under ambiguity",
             "asks": "Ranks when everything is called urgent.",
             "anchor": "\"Filter noise and prioritize the highest-impact "
                       "issues\" (Task 2)"},
            {"label": "Error detection in messy inputs",
             "asks": "Catches the contradiction, the reversal, the broken time "
                     "math.",
             "anchor": "\"Synthesize information from multiple stakeholders\" "
                       "(Tasks 1 and 2)"},
            {"label": "Accountability without authority",
             "asks": "Tasks people who do not report to them.",
             "anchor": "\"Ability to influence without formal authority\" "
                       "(Task 1 owners)"},
            {"label": "AI-native execution with checkpoints",
             "asks": "Uses AI on volume work, names where it stops.",
             "anchor": "\"Leverage AI tools to improve executive productivity\" "
                       "(Task 3)"},
        ),
        "criteria": (
            {"key": "meeting_synthesis", "label": "Meeting synthesis (Task 1)",
             "block": "work_product", "weight": 24,
             "anchors": {
                 5: "Five sections, settled split from live: one onboarding "
                    "definition, Product drafts and Customer Success validates "
                    "by Wednesday, the recommendation not a document. Owners "
                    "match the closing recap, and the document recovers what "
                    "that recap dropped: Jefferson County numbers, Chicago "
                    "packets, board deck edits.",
                 3: "Decisions blended with discussion; dropped items "
                    "unrecovered; deadlines vague.",
                 1: "Transcript retelling, a section missing, or the Q4 "
                    "dashboard move written as decided.",
             }},
            {"key": "calendar",
             "label": "Calendar and prioritization (Task 2)",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "6:30-8:30 plus 90 minutes is 8:00-10:00, which breaks the "
                    "9:30 investor breakfast, so the breakfast moves and the "
                    "11:00 district presentation holds. Engineering roadmap "
                    "review lands before Thursday board prep. Wednesday's 10:00 "
                    "investor ask resolved against the workshop.",
                 3: "Delay shifted but the breakfast conflict unresolved; no "
                    "dependencies.",
                 1: "Calendar unchanged, the 90 minutes never applied, or "
                    "everything accepted.",
             }},
            {"key": "briefing_packet",
             "label": "Chicago briefing packet (Task 2)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "One document, three Tuesday meetings, each with the CEO's "
                    "headings: background, objectives, people, previous "
                    "conversations, questions to ask. Missing inputs get an "
                    "owner and a date.",
                 3: "Headings uneven, thin sections, no owner on missing inputs.",
                 1: "Three documents against \"one document\", generic agendas, "
                    "or invented prior conversations.",
             }},
            {"key": "repeatable_workflow",
             "label": "Repeatable AI workflow (Task 3)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "One page: capture, follow-up tracking, packet generation, "
                    "the AI and human split, confidentiality. Grounded in Tasks "
                    "1 and 2. Decisions instead of transcripts, nothing external "
                    "unreviewed, workflow before software.",
                 3: "Topics covered generically; human review asserted with no "
                    "checkpoint.",
                 1: "Opens with a platform purchase, or unreviewed AI follow-ups "
                    "to customers.",
             }},
            {"key": "ai_disclosure", "label": "AI Workflow Disclosure",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Tools tied to steps, a model compressing the transcript, a "
                    "manual pass checking owners against the closing recap. One "
                    "real override named.",
                 3: "Tools plus general acceleration; override generic.",
                 1: "\"I used ChatGPT to help\", or no AI use against an "
                    "explicit instruction.",
             }},
            {"key": "structure", "label": "Structure and constraints",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Markdown by task, Task 3 inside one page, video 5-8 minutes "
                    "with a 1-2 minute intro, salary stated, an ajaia.ai "
                    "reference.",
                 3: "One miss: video length off, no ajaia.ai reference, salary "
                    "buried.",
                 1: "No video, no salary, or Task 3 far past one page.",
             }},
            {"key": "open_loops", "label": "Open loops carried forward",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Each live item gets a trigger and a date: Jefferson County "
                    "numbers still owed; QA Lead backfill parked until Monday "
                    "unless a resignation lands first; no external word on "
                    "dashboards until Engineering and Product agree.",
                 3: "Open questions without triggers or dates; most live items "
                    "lost.",
                 1: "Section empty, or reversals reported as decisions.",
             }},
        ),
        "auto_fails": (
            "Fabricated Jefferson County figures or Dallas ISD terms.",
            "Participants renamed beyond the transcript's Sarah and Mike.",
            "A Dallas note that apologizes or mentions reporting dashboards -- "
            "both ruled out on the record.",
            "Placeholder people written as real.",
            "No AI Workflow Disclosure at all.",
            "JD-echo of \"protect the CEO's time and maximize their "
            "effectiveness\", or an answer that never touches the transcript.",
        ),
        "red_flags": (
            "A missing ajaia.ai reference is not an auto-fail; deduct in the "
            "communication block.",
            "Reversals written up as settled decisions.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, identity mismatch, JD phrasing "
                                      "returned."},
            {"key": "complete", "label": "Complete: three tasks, video plays."},
            {"key": "caps", "label": "Caps: one page on Task 3, Markdown by "
                                     "section."},
            {"key": "scenario", "label": "Scenario named: Jefferson County, "
                                         "Dallas ISD, Chicago."},
            {"key": "number", "label": "One checkable number reused: the "
                                       "8:00-10:00 flight, the 90 minutes."},
            {"key": "ai_note", "label": "AI note specific."},
        ),
        "tells": {
            "strong": "Open questions carrying dates.",
            "weak": "A clean document in which nothing is unresolved.",
        },
        "gia": {
            "primary": ("Perceptual Speed", "Reasoning"),
            "secondary": ("Word Meaning",),
            "why": "Both seats take fast, messy, partly contradictory input and "
                   "must find the error and the dependency before anyone asks.",
            "proxies": (
                "Whether the 90-minute delay carries through to the 9:30 "
                "breakfast collision -- the cleanest Perceptual Speed proxy in "
                "the pack.",
                "Whether the reversals on dashboards, the board deck and the QA "
                "Lead are caught as reversals.",
                "Whether Engineering is sequenced before board prep, a "
                "Reasoning proxy.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage.",
                "2:00 Task 1 against the closing recap.",
                "6:00 flight math.",
                "9:00 packet, Task 3.",
                "12:00 video.",
                "14:00 spike, total.",
            ),
            "calibration": "Do not pay for fluent completeness. The CEO's "
                           "reversals written up as settled decisions is a 1 on "
                           "the spike, not a 4.",
            "probes": (
                "The closing recap left items out. What in your system catches "
                "them in the room?",
                "Name one decision here you would have made without the CEO, "
                "and what would have made that wrong.",
            ),
        },
        "gaps": (
            "RAD is never expanded in any source, and the Operations transcript "
            "expands RDI as Research, Development and Innovation.",
        ),
    },

    # -- 6. Investments ----------------------------------------------------
    {
        "key": "investments",
        "unit": "Investments",
        "entity": "LaunchEd (created by RDI)",
        "slugs": ("investment-lead",),
        "roles": ("Strategic Investment Lead (5910621)",
                  "Head of Strategic Investments (5910643)",
                  "Corporate Development Lead (5910637)"),
        "assessment": "AI-Native Strategic Investment Lead, 120 minutes plus a "
                      "5 to 7 minute video",
        "location": "On-site, Fort Lauderdale",
        "spike": "Portfolio fit and conflict test",
        "seat": "The seat leads \"the financial evaluation and execution of "
                "strategic transactions across LaunchEd's portfolio,\" owning "
                "execution \"from initial evaluation to closing.\"",
        "core_skill": "Price a deal fast on incomplete data, take one position, "
                      "and convert what you cannot resolve into terms.",
        "competencies": (
            {"label": "Deal arithmetic under time",
             "asks": "Prices a round from raw metrics and shows the work.",
             "anchor": "\"Strong financial modeling and valuation skills\" "
                       "(Part 1.1)"},
            {"label": "Positioned judgment",
             "asks": "Reaches one recommendation rather than surveying options.",
             "anchor": "\"practical deal judgment\" (Parts 1.2, 2.1, 2.2)"},
            {"label": "Risk triage and diligence design",
             "asks": "Ranks risks by severity and names the evidence that "
                     "settles each.",
             "anchor": "\"Coordinate financial, operational, and market "
                       "diligence\" (Parts 1.3, 1.4)"},
            {"label": "Transaction structuring",
             "asks": "Converts unresolved concerns into terms.",
             "anchor": "\"Structure transactions including minority "
                       "investments, majority acquisitions ...\" (Part 2.1)"},
            {"label": "Translation for non-specialists",
             "asks": "Explains a priced deal to people who do not model.",
             "anchor": "\"translate complex financial structures into clear "
                       "explanations\" (Part 2.2, video Part B)"},
            {"label": "Ecosystem fit",
             "asks": "Reads a target against what the platform already owns.",
             "anchor": "\"Evaluate strategic opportunities connected to the "
                       "organization's ecosystem\" (Part 2.1)"},
        ),
        "criteria": (
            {"key": "deal_arithmetic",
             "label": "Deal arithmetic and pricing (1.1)",
             "block": "work_product", "weight": 14,
             "anchors": {
                 5: "Post-money $13.3M shown as $2M / 0.15, pre-money $11.3M, "
                    "multiple labeled pre or post on the $850,000 base (13.3x "
                    "or 15.7x; forward on 80 percent growth fine if labeled), "
                    "21 months at $95,000 burn with the assumption named. Tests "
                    "the multiple against the 80 percent growth and 72 percent "
                    "margin.",
                 3: "Post-money right, pre-money conflated with it, or multiple "
                    "unlabeled; 21 months with no assumption; \"defensible\" "
                    "asserted without touching growth or margin.",
                 1: "Math absent or conclusion bare; multiple taken off burn or "
                    "gross profit; runway never divided.",
             }},
            {"key": "thesis",
             "label": "Thesis and decision (1.2, 2.1, 2.2)",
             "block": "work_product", "weight": 22,
             "anchors": {
                 5: "Three attractions, ranked, each pinned to a supplied fact: "
                    "80 percent growth on 72 percent margin, 88 percent "
                    "retention, three new clients, 18 institutions. One decision "
                    "in the memo's first line, held through board answer and "
                    "video. Board answer under 150 words argues why now on its "
                    "own terms.",
                 3: "Reasons unranked, or one rests on assertion not a supplied "
                    "metric; decision stated then softened; board answer "
                    "restates the memo.",
                 1: "More than three reasons, or a survey with no decision; "
                    "nothing in the first line; board answer over cap or dodges "
                    "\"now versus a year\".",
             }},
            {"key": "risk_diligence",
             "label": "Risk ranking and diligence (1.3, 1.4)",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Five risks ranked on a stated basis, each with why it "
                    "matters and one named piece of evidence. Material ones "
                    "surface: the 35 percent customer, 10-month runway against a "
                    "Series A 12 to 18 months out, larger-funded entrants, 88 "
                    "percent retention as implied churn on 18 accounts, "
                    "Southeast Asia concentration. Three diligence areas, each a "
                    "question plus the threshold that flips the recommendation.",
                 3: "Ranking nominal, or evidence is \"talk to customers\"; "
                    "diligence areas without a flip condition, or the flip is "
                    "\"if it looks bad\".",
                 1: "Fewer than five, unranked, or generic execution and market "
                    "risk with no scenario number; diligence restates the risk "
                    "list.",
             }},
            {"key": "structure_value",
             "label": "Structure and value creation (2.1)",
             "block": "work_product", "weight": 9,
             "anchors": {
                 5: "Answers whether to take $2,000,000 for 15 percent as "
                    "offered; any counter carries a reason. Conditions are "
                    "deal-specific: tranching on a concentration or bookings "
                    "milestone, information rights, pro rata, a covenant on the "
                    "35 percent account. Five or fewer plan items, each with "
                    "action, owner type, metric.",
                 3: "Boilerplate governance tied to nothing in the scenario; "
                    "\"as offered\" with no reason; plan items missing owner "
                    "type or metric.",
                 1: "No structure answer, conditions omitted, or plan items with "
                    "neither owner nor metric.",
             }},
            {"key": "ai_log", "label": "AI leverage log (Part 3)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Verbatim prompt that did work on this deal, not \"act as a "
                    "VC analyst.\" One named instance AI got wrong here "
                    "(mis-multiplied valuation, invented comparable, fabricated "
                    "benchmark), how it was caught, what replaced it. One part "
                    "kept human with a reason: the decision, counter or ranking.",
                 3: "Prompt generic; the error is a category (hallucination) not "
                    "an instance; human-only part named without a reason.",
                 1: "Process narration, no verbatim prompt, or \"I used ChatGPT "
                    "to help\".",
             }},
            {"key": "ic_readiness",
             "label": "Constraint compliance and IC readiness",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Caps held: 700 / 700 / 150 / 250 words, 250 lines. Markdown "
                    "by named section. Memo could go to the investment committee "
                    "unedited, decision first, no preamble. Video 5 to 7 minutes "
                    "covering both parts and salary.",
                 3: "One cap over or a section unlabeled; memo buries the "
                    "decision; video skips a Part B item or salary.",
                 1: "Caps broken, unstructured prose, no video link, or a script "
                    "read aloud.",
             }},
            {"key": "portfolio_fit",
             "label": "Target tested against the three holdings",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Names the workforce development platform, university "
                    "admissions software company and online credentialing "
                    "platform by function, and says which relation applies to "
                    "each: overlap, shared channel, or conflict. Converts it "
                    "into a term: partnership condition, referral terms, an "
                    "information wall.",
                 3: "Names the holdings and asserts synergy without saying which "
                    "relation applies; fit left at \"strong strategic "
                    "alignment\".",
                 1: "Portfolio never mentioned, or the fit paragraph would suit "
                    "any investor.",
             }},
        ),
        "auto_fails": (
            "No arithmetic in 1.1. The task says \"Show the arithmetic, not just "
            "the conclusion,\" so a bare conclusion is an auto-fail, not a low "
            "score.",
            "No video link, or no decision at all. The cover instructs \"Take a "
            "position.\"",
            "Cap breach past 50 percent: Part 1 or memo over 1,050 words, board "
            "answer over 225, log over 375, submission over 375 lines.",
            "Fabrication: invented comparable transactions, market sizes, churn "
            "or acquisition-cost figures, or a named competitor treated as fact. "
            "Labeled assumptions are not.",
            "Never names $2,000,000 for 15 percent, the 35 percent customer, the "
            "10-month runway or Southeast Asia. Off-scenario template.",
        ),
        "red_flags": (
            "JD-echo, letters addressed to the company rather than the deal, and "
            "video identity that does not match the written work: these route to "
            "the fraud log.",
            "A memo that summarizes the snapshot table back at you and never "
            "prices anything.",
        ),
        "do_not_penalize": (
            "The scenario is written in Ajaia's voice (\"Ajaia's existing "
            "portfolio\", \"The CEO\") while the seat sits at LaunchEd. "
            "Candidates writing to Ajaia are following the prompt.",
            "\"Do Not Invest\" is not a wrong answer; the cover says there is no "
            "single correct answer. Grade the defense, not the verdict.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells."},
            {"key": "complete", "label": "Complete: all four Part 1 "
                                         "subsections, a memo with a decision "
                                         "line, board answer, AI log, playable "
                                         "video."},
            {"key": "caps", "label": "Caps: 700 / 700 / 150 / 250 words, 250 "
                                     "lines."},
            {"key": "scenario", "label": "Scenario named: the $2,000,000 for 15 "
                                         "percent, the 35 percent account, the "
                                         "10-month runway, or a portfolio "
                                         "holding."},
            {"key": "number", "label": "One checkable number: post-money near "
                                       "$13.3M, or runway near 21 months."},
            {"key": "ai_note", "label": "AI log specific, with a verbatim "
                                        "prompt."},
        ),
        "tells": {
            "strong": "A risk ranking whose top entry is argued rather than "
                      "obvious.",
            "weak": "A memo that summarizes the snapshot table back at you and "
                    "never prices anything.",
        },
        "gia": {
            "primary": ("Number Speed and Accuracy", "Reasoning"),
            "secondary": ("Word Meaning",),
            "why": "The seat prices transactions on a clock and supports "
                   "decisions \"with valuation frameworks and scenario "
                   "analyses,\" then ranks what it cannot resolve.",
            "proxies": (
                "Whether the four 1.1 computations land inside a 35-minute block "
                "shared with three other subtasks.",
                "Whether 88 percent retention becomes a consequence rather than "
                "a repeated figure.",
                "Whether the risks order on a stated basis and the diligence "
                "flips are thresholds.",
                "Whether the 150-word board answer compresses without losing the "
                "argument.",
                "What the candidate chose to skip -- \"Deciding what to skip is "
                "part of what we are scoring.\"",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage plus counts on the four capped items.",
                "1:30 Part 1.1 -- the fastest true or false in the pack: "
                "$13.3M, $11.3M, the multiple's label, 21 months and its "
                "assumption.",
                "4:00 memo first line and board answer together, testing whether "
                "the position holds.",
                "7:00 Parts 1.3 and 1.4, reading only the ranking basis and the "
                "evidence column.",
                "10:00 conditions, structure answer, plan.",
                "11:30 AI log.",
                "12:30 video at 1.5x for the four Part B items, salary and "
                "identity.",
                "14:00 spike and total.",
            ),
            "calibration": "Do not pay for fluency. A memo that hits every "
                           "heading, reads well, and never states a post-money "
                           "or a counter is a 1 on arithmetic, not a 4 overall. "
                           "A rough memo that computes $13.3M, ranks the 35 "
                           "percent concentration first, and counters the price "
                           "advances.",
            "per_title": "Head of Strategic Investments weights the memo, "
                         "conditions and spike; Corporate Development Lead "
                         "weights structure and the conflict test; Strategic "
                         "Investment Lead weights 1.1 arithmetic and diligence.",
            "probes": (
                "The 35 percent customer does not renew four months after close. "
                "What happens to your model, your conditions and the Series A "
                "timeline?",
                "You countered on price and the founders hold a competing term "
                "sheet at $13.3M post. What do you drop first, and what do you "
                "refuse to drop?",
                "Name a deal you recommended against that closed anyway, or one "
                "you pushed that underperformed. What did the diligence miss?",
            ),
        },
        "gaps": (
            "Reconciliation with the candidate-facing weights (judgment 30, risk "
            "25, valuation 20, communication 15, AI 10): rank order is "
            "preserved. Judgment carries 32 across the thesis criterion and the "
            "spike, risk 25, valuation and structure 23, AI 10. Communication is "
            "capped at 10 by the fixed architecture, so the \"could go to an "
            "Investment Committee unedited\" standard is also scored inside the "
            "thesis and structure criteria rather than dropped.",
            "The three postings are identical below the title line, with empty "
            "About and Responsibilities sections.",
        ),
    },

    # -- 7. Research and Data ----------------------------------------------
    {
        "key": "research_data",
        "unit": "Research and Data",
        "entity": "LaunchEd (created by RDI)",
        "slugs": ("data-scientist",),
        "roles": ("Data Scientist, Research & Evaluation (5931728)",
                  "Data Analyst Researcher (5931633)",
                  "Education Research & Data Analyst (5931721)"),
        "assessment": "Data Scientist Assessment, 120 minutes, four deliverables",
        "location": "On-site, Fort Lauderdale",
        "spike": "Evaluator independence",
        "seat": "LaunchEd helps education companies \"prove the real-world "
                "impact of their products through independent research and "
                "evidence-based validation.\" The seat owns \"the LaunchEd "
                "Validation Score as a core piece of platform IP.\"",
        "core_skill": "Get a defensible effect estimate out of non-experimental "
                      "school data, price it, and say plainly what it does not "
                      "support.",
        "competencies": (
            {"label": "Evaluation design",
             "asks": "Turns a question into a study that could run.",
             "anchor": "\"design and run studies independently\" (1A, 1C, 2A)"},
            {"label": "Causal reasoning",
             "asks": "Separates an effect from a selection artifact.",
             "anchor": "\"evaluations that hold up to outside scrutiny\" "
                       "(1C, 2B)"},
            {"label": "Analytical craft",
             "asks": "Prioritized, correct queries against a schema.",
             "anchor": "\"Excel, SQL, and Python or R\" (Task 1B)"},
            {"label": "Measurement and ROI arithmetic",
             "asks": "Turns model output into a costed decision.",
             "anchor": "\"Quantify return on investment\" (Tasks 2C, 3)"},
            {"label": "Decision-ready translation",
             "asks": "Writes for the person who signs, not the one who models.",
             "anchor": "\"translate data into decision-ready findings\" "
                       "(Task 3)"},
            {"label": "Evaluator independence",
             "asks": "Holds a finding to a standard the buyer does not set.",
             "anchor": "\"a defensible methodology that can serve as a credible "
                       "market standard\" (Tasks 2C, 3)"},
        ),
        "criteria": (
            {"key": "evaluation_design",
             "label": "Evaluation design (1A, 1C, 2A)",
             "block": "work_product", "weight": 22,
             "anchors": {
                 5: "Names outcome, window and comparison group and how it is "
                    "formed, before any method. States the unit of assignment "
                    "(student, class, school). Treats the intake baseline as a "
                    "threat: attrition, regression to the mean, testing "
                    "calendar. Separates dosage from assignment. Flags which "
                    "measures need data not yet supplied.",
                 3: "Comparison left at \"pre versus post\"; measures listed "
                    "without why each is predictive; missing data named as a "
                    "category, not as fields.",
                 1: "Method first, question never; supplied statistics "
                    "paraphrased back as a plan; no unit of analysis.",
             }},
            {"key": "causal_reasoning",
             "label": "Causal reasoning and validity (1C, 2B)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Says which supplied cuts are confounded and why, then names "
                    "the test that settles it: randomized holdout, matched "
                    "comparison, or difference-in-differences on the intake "
                    "baseline. Clusters at the level of assignment. States what "
                    "would falsify each hypothesis. No feature knowable only "
                    "after the outcome.",
                 3: "Confounding named once, then the design proceeds as if the "
                    "cut were causal; validation is a split with no time "
                    "ordering or clustering.",
                 1: "Correlation reported as effect; hypotheses restate the "
                    "supplied percentages; outcome-derived features used as "
                    "predictors.",
             }},
            {"key": "analytical_craft", "label": "Analytical craft (1B)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Four to six blocks as instructed, each with a stated "
                    "purpose, executable against the supplied schema and its "
                    "real field names, joins correct, cheapest "
                    "decision-relevant query first, one checking data quality "
                    "(nulls, duplicates, date coverage).",
                 3: "Runs, but ignores half the schema, purposes unstated, or "
                    "one query per table with no prioritization.",
                 1: "Pseudocode only, invented tables or fields, or fewer than "
                    "four blocks.",
             }},
            {"key": "measurement_roi",
             "label": "Measurement, ROI and thresholds (2C, 3)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "Metrics defended against the base rate, not asserted (why "
                    "recall at fixed capacity, or precision-recall over "
                    "accuracy). Threshold derived from intervention capacity and "
                    "unit cost with the arithmetic shown. Impact given as a "
                    "range built from labeled assumptions.",
                 3: "Metrics named but never tied to the base rate; threshold "
                    "asserted (0.5, or \"high risk\") with no capacity or cost "
                    "input; impact quoted as one number.",
                 1: "Accuracy as headline metric; no threshold; impact claimed "
                    "as a share of the supplied cost figure.",
             }},
            {"key": "ai_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names each tool and the specific place it moved this work "
                    "(drafting queries, feature brainstorm, tightening the "
                    "summary), one analytical call the model got wrong here and "
                    "what replaced it, and what stayed human.",
                 3: "Tools and rough uses named; the split asserted with no "
                    "instance; no verification step.",
                 1: "\"I used ChatGPT to help,\" or a note about AI in general, "
                    "not this assessment.",
             }},
            {"key": "readability",
             "label": "Constraint compliance and readability",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Task 3 holds one page, covers all five required points, "
                    "uses no term the reader must look up, makes the ask of the "
                    "client specific. Structured Markdown by named section, "
                    "assumptions labeled where the task says to state them.",
                 3: "Runs long or leaks model vocabulary untranslated; one of "
                    "the five points thin; sections unlabeled.",
                 1: "Technical memo pasted under a summary heading; a deliverable "
                    "missing; unstructured prose.",
             }},
            {"key": "independence",
             "label": "What the candidate will not claim",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Puts the limitation in the summary, not a footnote: what "
                    "the analysis cannot yet support, at what confidence, what "
                    "evidence would change it. Sets the standard the finding "
                    "must clear before it is reported. Declines to promise a "
                    "number the design cannot produce.",
                 3: "Caveats generic (\"results may vary\"); confidence "
                    "unquantified; no standard of proof.",
                 1: "Sells the solution: impact promised as certain, no "
                    "limitation, no falsification condition, findings shaped to "
                    "the buyer's preferred answer.",
             }},
        ),
        "auto_fails": (
            "Fewer than the four listed deliverables.",
            "Fabricated numbers: invented effect sizes, model accuracies, "
            "benchmark rates, or unnamed cited studies. Labeled assumptions are "
            "not.",
            "Code written against tables or fields absent from the supplied "
            "schema.",
            "Privacy failure: moving identified student or patient records with "
            "no access, consent or de-identification note. The JD names FERPA "
            "and COPPA.",
            "Off-scenario template: algorithms, imbalance handling and metrics "
            "recited without touching one supplied number.",
        ),
        "red_flags": (
            "JD-echo materials, which route to the fraud log rather than a "
            "reject score. This family carries the heaviest JD-echo spam in the "
            "pack.",
            "One submission filed against two or three postings is a duplicate, "
            "not three candidates: dedupe to one record.",
            "The long, fluent, textbook answer that names every algorithm and "
            "never says a supplied comparison is confounded.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells -- the highest-yield check "
                                      "in this family. JD phrases returned "
                                      "(\"decision-ready findings\", "
                                      "\"defensible methodology\", \"Strong "
                                      "Minds, Good Hearts\") without an "
                                      "analysis is echo, not evidence."},
            {"key": "complete", "label": "Complete: all four deliverables."},
            {"key": "format", "label": "Format: structured Markdown by named "
                                       "section, Task 3 near one page, scope "
                                       "plausible inside 120 minutes."},
            {"key": "scenario", "label": "Engages the scenario: at least two "
                                         "supplied figures or schema fields "
                                         "named."},
            {"key": "claim", "label": "One checkable claim: a threshold with "
                                      "arithmetic, a named confound, or a query "
                                      "against real field names."},
            {"key": "ai_note", "label": "AI note specific to this assessment."},
        ),
        "tells": {
            "strong": "A hypothesis written so it could be disproved.",
            "weak": "The summary statistics table paraphrased into prose.",
        },
        "gia": {
            "primary": ("Reasoning", "Number Speed and Accuracy"),
            "secondary": ("Perceptual Speed",),
            "why": "The work is deductive (which comparison is contaminated, "
                   "what would falsify this) on constant manipulation of rates, "
                   "costs and thresholds.",
            "proxies": (
                "Whether the reminder cuts are named as selection effects rather "
                "than repeated.",
                "Whether a percentage becomes a consequence (8 percent of "
                "patients produce 38 percent of no-shows, therefore a targeting "
                "rule).",
                "Whether the threshold arithmetic is right.",
                "Whether queries use real field names and correct joins.",
                "What was skipped to land four deliverables inside 120 minutes.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage and deliverable count.",
                "2:00 Task 1C first -- hypotheses expose causal reasoning in one "
                "paragraph.",
                "4:00 Task 1B, reading purposes and field names only.",
                "6:30 Task 2B validation and imbalance.",
                "9:00 Task 2C metrics and threshold, checking the arithmetic.",
                "11:00 Task 3 against one page and the five points.",
                "12:30 Task 4.",
                "14:00 spike and total.",
            ),
            "calibration": "Do not pay for volume. One that names every "
                           "algorithm and never says a supplied comparison is "
                           "confounded scores 1 on causal reasoning, not 3. A "
                           "shorter one that kills its own hypothesis with a "
                           "reason advances.",
            "per_title": "Data Scientist (Research & Evaluation) weights model "
                         "design and thresholds; Data Analyst Researcher weights "
                         "craft and ROI arithmetic; Education Research and Data "
                         "Analyst weights design, comparison group and "
                         "translation.",
            "probes": (
                "Your evaluation shows no effect for a company heading into a "
                "Gate 2 review. What goes in the Validation Report, and how do "
                "you say it?",
                "Intake benchmarks rise in year two with no product effect. How "
                "do you tell regression to the mean from real gain?",
                "Twelve schools, no randomization, one semester. What design can "
                "you actually run, and what will you not be able to claim?",
            ),
        },
        "notes": (
            "Bridge for healthcare-version submissions. The live assignment is "
            "the healthcare no-show version, not the education-efficacy retool. "
            "Transfers at full weight: design, causal reasoning, craft, metric "
            "and threshold judgment, communication, the AI note, the spike. Does "
            "not transfer: intake-to-graduation baselining, school-level "
            "clustering, the Validation Score, external benchmarking, student "
            "privacy -- read those as not applicable, deduct nothing, move the "
            "missing evidence to a probe.",
            "Healthcare equivalents at the 5 level: \"Reminder SMS not opened, "
            "no-show rate 41%\" and \"No reminder sent, no-show rate 35%\" "
            "against a 22 percent overall rate are selection effects, and a 5 "
            "says so and proposes a holdout. The 11 / 27 / 34 percent lead-time "
            "gradient is confounded with New Patient at 31 percent. A 22 percent "
            "base rate is mild imbalance, so reflexive resampling caps that "
            "clause at 3.",
        ),
        "gaps": (
            "No education-efficacy assessment exists. This grid marks the four "
            "slots the live healthcare assignment already uses against the "
            "education standard the JDs set.",
            "This assignment has no video and no salary-expectations question, "
            "so identity confirmation and compensation fit must happen at "
            "screen.",
            "Front matter carries no duration; the 120 minutes comes from the "
            "body.",
        ),
    },

    # -- 8. Partnerships (no platform assessment) --------------------------
    {
        "key": "partnerships",
        "unit": "Partnerships",
        "entity": "LaunchEd (created by RDI)",
        "slugs": (),
        "roles": ("Education Partnerships Coordinator (5931829)",
                  "Partnership Operations Coordinator (5931818)",
                  "Strategic Partnerships Coordinator (5931839)"),
        "assessment": "No platform assessment exists. Graded on the intake-triage "
                      "working session: fifteen inbound school inquiries and four "
                      "vendor pilots mid-flight.",
        "location": "On site, Fort Lauderdale",
        "spike": "Loop closure",
        "seat": "The seat coordinates a two-sided intake pipeline, carrying "
                "opportunities \"from initial intake through early evaluation\" "
                "and keeping \"pipeline status and notes current in "
                "Monday.com.\"",
        "core_skill": "Hold nineteen live items in one system another person "
                      "could run on Monday, and be right about which three "
                      "matter today.",
        "competencies": (
            {"label": "Pipeline system design",
             "asks": "A structure holding every live item with the fields needed "
                     "to act.",
             "anchor": "\"Keep pipeline status and notes current in "
                       "Monday.com\" (tracking deliverable)"},
            {"label": "Prioritization on a stated basis",
             "asks": "A rule declared up front, applied to the last item as "
                     "strictly as the first.",
             "anchor": "\"urgency in a fast-moving pipeline\" (prioritization "
                       "deliverable)"},
            {"label": "Screening and routing",
             "asks": "Decides what becomes a Stage 1 opportunity, what needs "
                     "more information, what routes out.",
             "anchor": "\"early-stage screening ... against LaunchEd intake "
                       "criteria and the four core pillars\""},
            {"label": "Coordination and handoff",
             "asks": "Turns decisions into meetings, owners and transfers that "
                     "hold.",
             "anchor": "\"Ensure clean handoffs between business development, "
                       "diligence, and implementation teams\""},
            {"label": "Discretion with partner information",
             "asks": "Records enough to run the pipeline and no more.",
             "anchor": "\"discretion handling confidential pipeline and partner "
                       "information\""},
        ),
        "criteria": (
            {"key": "tracking_system", "label": "Tracking system design",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "All nineteen items in one structure. Fields carry stage "
                    "(Stage 1 Intake, Stage 2 Initial Screen), owner, dated next "
                    "action, source and a screen field. The four pilots carry "
                    "checkpoint and status fields the fifteen inquiries do not.",
                 3: "The nineteen listed with stage and owner, but no dated next "
                    "action, or pilots and inquiries modeled identically.",
                 1: "Prose with no fields, a count that misses items, or a "
                    "generic CRM description that never names the intake stages.",
             }},
            {"key": "prioritization",
             "label": "Prioritization on a stated basis",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "The basis is stated before it is used and holds at item "
                    "fifteen as at item one. All fifteen inquiries tiered, one "
                    "tie broken on a named factor. The four pilots sequenced "
                    "against each other rather than all continued.",
                 3: "Tiers exist, basis implicit or applied to the top few only. "
                    "The four pilots treated as one block.",
                 1: "Arrival order, everything urgent, or a ranking with no "
                    "reason attached to any item.",
             }},
            {"key": "screening", "label": "Screening and routing",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Each inquiry gets a decision and the criterion behind it: "
                    "advance to Stage 1, hold pending named missing information, "
                    "or route out. Separates an inquiry ready for a demo from "
                    "one needing a validated problem statement from the "
                    "Innovators Network first. Says whether each pilot still "
                    "meets the intake bar.",
                 3: "Decisions given, criterion generic (\"good fit\"). No "
                    "missing-information list. Pilots not re-screened.",
                 1: "All fifteen advanced with no screen, or intake criteria "
                    "never referenced.",
             }},
            {"key": "coordination", "label": "Coordination and handoff",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Names which items get an intake meeting, demo or screening "
                    "call, and in which week. Each pilot has an owner role and a "
                    "next checkpoint date. States the trigger for a handoff and "
                    "the artifact that travels with it.",
                 3: "Scheduling and handoffs mentioned without triggers or "
                    "artifacts. Pilots have owners but no checkpoints.",
                 1: "No calendar, no owners, handoffs described as staying in "
                    "touch.",
             }},
            {"key": "ai_leverage", "label": "AI leverage with verification",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names what AI did (first-pass clustering of the fifteen, a "
                    "draft field schema, a holding reply template), what stayed "
                    "human (the screen decision, anything founder-facing, "
                    "confidential detail kept out of consumer tools), and the "
                    "check run (all nineteen re-read against the board, counts "
                    "confirmed).",
                 3: "A tool named and one step described, with no verification "
                    "or no line on what stays human.",
                 1: "\"I used ChatGPT to help,\" AI credited with the screen "
                    "decisions and unchecked, or no note at all.",
             }},
            {"key": "readability",
             "label": "Readability and constraint compliance",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "A reader who has not seen the nineteen items can act from "
                    "the artifact in two minutes. Formats and stated caps held. "
                    "Says plainly what will not get done this week and why that "
                    "is the right call.",
                 3: "One section unlabeled or one cap missed. Tradeoffs implied, "
                    "not stated.",
                 1: "Unstructured dump, a deliverable missing, or a plan "
                    "acknowledging no capacity limit.",
             }},
            {"key": "loop_closure",
             "label": "Nothing leaves the system silently",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Every item, bottom tier included, carries a next human, a "
                    "next date and an end condition. Deprioritized inquiries "
                    "have a revisit trigger and a named owner for the "
                    "school-facing or founder-facing message. Identifies the "
                    "pilot most likely to go quiet and the date it would.",
                 3: "Top tier has owners and dates. Parked items have neither. "
                    "End conditions absent.",
                 1: "Prioritization ends at a ranked list, the bottom tier "
                    "disappears, no pilot has a next checkpoint.",
             }},
        ),
        "auto_fails": (
            "A count error: fewer than fifteen inquiries or four pilots in the "
            "tracker. This seat is paid to not lose items.",
            "The four core pillars or the intake criteria named with invented "
            "content stated as fact.",
            "Invented school, district or founder names, figures or dates.",
            "Confidential information mishandled: one vendor's terms in a field "
            "other vendors can see, school-identifying feedback attributed in a "
            "shared column, or pipeline detail pasted into a consumer AI tool "
            "with no handling note.",
            "A tracker that could belong to any pipeline, never naming Stage 1 "
            "Intake, Stage 2 Initial Screen, Monday.com, the Innovators Network "
            "or the Sandbox.",
            "All fifteen advanced to Stage 2. That is intake, not screening.",
        ),
        "red_flags": (
            "JD-echo: \"Strong Minds, Good Hearts\" or a school-choice values "
            "statement recited in place of the work.",
            "A polished Monday.com board where every row says \"follow up\".",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, identity inconsistency, JD "
                                      "phrasing returned."},
            {"key": "complete", "label": "Both deliverables present, tracker and "
                                         "prioritization."},
            {"key": "format", "label": "Format and stated caps respected, "
                                       "deliverables labeled."},
            {"key": "scenario", "label": "Engages the scenario: the fifteen "
                                         "inquiries and four pilots appear as "
                                         "specific items, not categories."},
            {"key": "claim", "label": "One checkable claim: a count, a date, a "
                                      "named ranking factor."},
            {"key": "ai_note", "label": "AI evidence specific rather than a tool "
                                        "name."},
        ),
        "tells": {
            "strong": "A bottom tier that still carries dates.",
            "weak": "A clean board of nineteen rows where every next action says "
                    "\"follow up\".",
        },
        "gia": {
            "primary": ("Perceptual Speed", "Word Meaning"),
            "secondary": ("Reasoning",),
            "why": "Nineteen items in mixed formats where the errors are "
                   "omissions and duplicates rather than hard problems, and "
                   "where a school leader's actual request sits under how they "
                   "phrased it.",
            "proxies": (
                "Whether all nineteen are accounted for with no count error -- "
                "the cleanest Perceptual Speed proxy in this session.",
                "Whether two inquiries describing one underlying problem are "
                "recognized as one.",
                "Whether the basis declared at the top is the basis applied at "
                "item eleven.",
                "Whether the pilot quietly off track is found from the materials "
                "rather than from a stated flag.",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage and count the items, fifteen and four.",
                "2:00 read the prioritization before the tracker -- the basis "
                "and its consistency separate the field fastest.",
                "5:00 the tracker, checking the fields support the priorities "
                "just claimed.",
                "8:00 screening decisions and the missing-information list.",
                "10:00 calendar, owners, handoff triggers.",
                "12:00 AI note, communication, then the spike, scanning the "
                "bottom tier only.",
                "14:00 close.",
            ),
            "calibration": "Do not pay for the tool. A polished Monday.com board "
                           "is a 3 if every row says \"follow up.\" Nineteen "
                           "rows in a plain table with owners, dates and end "
                           "conditions is a 5. The artifact is the decisions, "
                           "not the software.",
            "per_title": "Operations weights the tracker and coordination, "
                         "Education weights screening and Innovators Network "
                         "routing, Strategic weights prioritization and the "
                         "spike.",
            "probes": (
                "Two bottom-tier inquiries came from the same school leader. "
                "What changes?",
                "A pilot you sequenced third has a founder calling weekly. What "
                "do you tell them, and what do you change?",
                "Which of the fifteen would you route out even if it came from "
                "inside the Red Apple enterprise, and what would you say?",
            ),
        },
        "gaps": (
            "No candidate-facing rubric exists to reconcile with, and no time "
            "budget, word cap or required-section list is set for this session. "
            "Set them before the first sitting or the constraint anchors have "
            "nothing to bind to.",
            "No AI requirement appears in the three postings; the AI block is "
            "the Ajaia-wide expectation.",
            "The JD references the intake criteria and four core pillars without "
            "enumerating either. Flagging the gap and stating a working "
            "assumption is credited under screening; asserting invented pillars "
            "as LaunchEd's is fabrication.",
            "ITSS appears in all three postings and is never expanded.",
        ),
    },

    # -- 9. EdTech Implementation (blocked) --------------------------------
    {
        "key": "implementation",
        "unit": "EdTech Implementation",
        "entity": "LaunchEd / CSUSA (Charter Schools USA), RDI arm",
        "slugs": (),
        "blocked": "The one-page RDI initiative brief the working session "
                   "references does not exist as an artifact. No initiative, "
                   "campus count, start date, calendar or baseline is available, "
                   "so these anchors test structure and reasoning, not fidelity "
                   "to supplied facts. Write and file the brief before the first "
                   "sitting.",
        "roles": ("EdTech Implementation Specialist (5932246)",
                  "Implementation Specialist (5932230)",
                  "Education Implementation Specialist (5932232)"),
        "assessment": "None on the platform. Graded on the defined working "
                      "session: 60 minutes with a one-page RDI initiative brief, "
                      "then a 15-minute presentation.",
        "location": "On site, Fort Lauderdale",
        "spike": "School-calendar realism",
        "seat": "The seat turns a decided initiative into classroom practice "
                "across a network, delivering through school-based RDI "
                "Implementation Leads and judged on \"adoption rates against "
                "established goals.\"",
        "core_skill": "Design a rollout a school with no spare hours will "
                      "actually finish, and know by day 45 which campus is "
                      "slipping.",
        "competencies": (
            {"label": "Rollout sequencing and differentiation",
             "asks": "Dated phases, different paths by school condition.",
             "anchor": "\"differentiated rollout strategies ... re-engagement "
                       "strategies ... sustainment protocols\" (90-day plan)"},
            {"label": "Field network design",
             "asks": "Adoption survives the specialist's absence.",
             "anchor": "\"coaching, resources, and real-time support to field "
                       "leads\" (field-lead structure)"},
            {"label": "Adoption measurement",
             "asks": "Metrics two people would count identically.",
             "anchor": "\"Analyze adoption and implementation data\" (three "
                       "metrics)"},
            {"label": "Barrier and dependency anticipation",
             "asks": "The fix ahead of the launch.",
             "anchor": "\"Anticipate and proactively remove barriers to "
                       "adoption\" (pre-rollout section)"},
            {"label": "Facilitation for adults",
             "asks": "Training pedagogically sound and adult learning-aligned.",
             "anchor": "\"comfort commanding a room and engaging diverse "
                       "audiences\" (the 15 minutes)"},
        ),
        "criteria": (
            {"key": "rollout_plan", "label": "90-day rollout plan",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Dated phases; an entry criterion and a checkpoint per "
                    "phase; separate paths for new, underperforming and mature "
                    "campuses with what differs named (training dose, check-in "
                    "frequency, entry bar); states what must be true before week "
                    "1.",
                 3: "A 30/60/90 with milestones; one path for every campus, or "
                    "tiering asserted without saying what differs.",
                 1: "No dates, no entry criteria, or a plan that never names the "
                    "initiative in the brief and fits any network.",
             }},
            {"key": "field_leads", "label": "Field-lead structure",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "Selection criteria and who nominates; hours the role costs a "
                    "working teacher; what leads receive before launch; check-in "
                    "cadence with a stated purpose; what a lead decides versus "
                    "what escalates.",
                 3: "Role, reporting line and cadence present; no selection "
                    "basis, or the time cost to the person unacknowledged.",
                 1: "\"A champion at each school\" restated from the brief; no "
                    "selection, support or cadence.",
             }},
            {"key": "adoption_metrics", "label": "Three adoption metrics",
             "block": "work_product", "weight": 17,
             "anchors": {
                 5: "Exactly three, each with definition, source, cadence and a "
                    "target with its basis; one goes past usage into "
                    "instructional practice or student effect; names which flags "
                    "a slipping campus before day 60.",
                 3: "Three defined roughly, all of them usage counts, or targets "
                    "with no source and no cadence.",
                 1: "\"Engagement,\" \"buy-in,\" \"satisfaction\" left undefined, "
                    "or targets asserted with no basis.",
             }},
            {"key": "barriers", "label": "Barriers and dependencies",
             "block": "work_product", "weight": 10,
             "anchors": {
                 5: "Barriers in three of the four JD categories -- logistical, "
                    "technical, cultural, instructional -- each with a mitigation "
                    "and an owner; device access, integrations and infrastructure "
                    "with the ITSS team gate week 1.",
                 3: "Mitigations without owners, barriers confined to technical, "
                    "or ITSS named without a pre-launch gate.",
                 1: "No risks, or \"teacher resistance\" with no mitigation and "
                    "no dependency on another team.",
             }},
            {"key": "ai_leverage", "label": "AI leverage with verification",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "What AI did (module outlines, field-lead job aids, metric "
                    "definitions), what stayed human (sequencing, anything a "
                    "principal reads, teacher or student data), and the check run "
                    "back against the brief.",
                 3: "A tool and one step; no verification, no human-versus-AI "
                    "line.",
                 1: "\"I used ChatGPT to help,\" the plan itself credited to AI, "
                    "or no note at all.",
             }},
            {"key": "presentation",
             "label": "Presentation and constraint compliance",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Recommendation first, all three deliverables covered, 15 "
                    "minutes held with room for questions; a VP of RDI can act "
                    "from the artifact alone; a challenge changes the plan or "
                    "draws a reason.",
                 3: "All three present, but time overruns or the plan consumes it "
                    "and the metrics are rushed; tradeoffs implied.",
                 1: "Over time with a deliverable uncovered, slides read aloud, "
                    "or nothing on what gets cut.",
             }},
            {"key": "calendar_realism",
             "label": "The 90 days fit a real school year",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Assumed start date stated and used: heavy training kept off "
                    "weeks already spent, a testing window or other blackout "
                    "named launch-free, and when in a teacher's week the training "
                    "happens and what it replaces.",
                 3: "Calendar or teacher time referenced generically, or a start "
                    "date assumed but nothing in the sequence moves.",
                 1: "A 30/60/90 that could begin in any month, training dropped "
                    "wherever the arithmetic lands.",
             },
             "note": "The spike scores calendar placement and teacher time only, "
                     "never tiering or milestones, which belong to the rollout "
                     "plan."},
        ),
        "auto_fails": (
            "A 90-day plan with no dates. A sequence with no calendar is not an "
            "implementation plan.",
            "Campus counts, baselines, budgets or school names asserted as CSUSA "
            "fact when the brief supplied none.",
            "Fewer than three metrics, or a deliverable never produced or never "
            "presented inside the 15 minutes.",
            "A rollout that never names the initiative and fits any network with "
            "any platform.",
            "JD-echo: the responsibilities list recited back as the plan.",
        ),
        "red_flags": (
            "Three usage counts and nothing else.",
            "Leads appointed with no release time.",
            "Every campus treated identically.",
            "Everything front-loaded into a launch training with nothing after "
            "day 30, against a JD requiring \"structured follow-up support.\"",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, identity inconsistency, JD "
                                      "phrasing returned."},
            {"key": "complete", "label": "All three deliverables present: plan, "
                                         "field-lead structure, metrics."},
            {"key": "format", "label": "Format held and the 15 minutes "
                                       "respected."},
            {"key": "scenario", "label": "The initiative from the brief is "
                                         "named, and its specifics drive at "
                                         "least one choice in the plan."},
            {"key": "claim", "label": "One checkable item: a date, a metric "
                                      "definition with a source, a target with a "
                                      "basis."},
            {"key": "ai_note", "label": "AI evidence specific rather than a tool "
                                        "name."},
        ),
        "tells": {
            "strong": "A dependency gate placed before week 1.",
            "weak": "A tidy 30/60/90 with no dates and metrics called \"usage, "
                    "engagement, satisfaction.\"",
        },
        "gia": {
            "primary": ("Reasoning", "Word Meaning"),
            "secondary": ("Perceptual Speed",),
            "why": "Sixty minutes on a one-page brief is deduction: a dependency "
                   "order and a 90-day sequence derived from a short input. The "
                   "seat then runs on language, reading what a principal or "
                   "teacher means and writing a plan they will follow.",
            "proxies": (
                "An ordering constraint the brief never stated, technical "
                "readiness before training being the clearest.",
                "The one brief detail that changes the plan.",
                "Metric definitions two reviewers would count identically.",
                "A field-lead role written in words a teacher would recognize.",
                "15 minutes reorganized for a listener rather than read off the "
                "artifact.",
            ),
        },
        "reviewer": {
            "path": (
                "Triage, three deliverables confirmed (1 min).",
                "Metrics first -- definition, source, cadence and target separate "
                "the field fastest (3).",
                "Plan for dates, entry criteria, real differentiation (4).",
                "Field-lead structure: selection, support, cadence, escalation "
                "(2).",
                "Barriers and the ITSS gate (2).",
                "AI note (1).",
                "Presentation notes, then the spike, scanning only for calendar "
                "placement (2).",
            ),
            "calibration": "Do not pay for the format. A polished phase grid is "
                           "a 3 if every milestone reads \"train teachers.\" A "
                           "former principal's plan often looks less tidy than a "
                           "project manager's; check whether the untidiness is "
                           "calendar realism before marking it down.",
            "per_title": "EdTech weights the ITSS gate and platform usage, "
                         "Education weights practice measurement and adult "
                         "learning, the unmodified title weights sequencing and "
                         "the field network.",
            "probes": (
                "Campus four sits at 20 percent of your usage target on day 60 "
                "and the principal says teachers are just busy: what changes in "
                "two weeks, and what do you stop?",
                "Your strongest field lead resigns in week 6: what in your "
                "structure made that survivable?",
                "The VP of RDI reads one of your three metrics and none of the "
                "others: which, and what is lost?",
            ),
        },
        "gaps": (
            "No word cap, required-section list or AI Workflow Note is defined "
            "for the 60 minutes, and no candidate-facing rubric exists to "
            "reconcile with.",
            "On site, so the artifact and a record of the 15 minutes must be "
            "captured at the sitting or nothing is gradeable later.",
        ),
    },

    # -- 10. IT and Security -----------------------------------------------
    {
        "key": "it_security",
        "unit": "IT and Security",
        "entity": "Ajaia",
        "slugs": ("director-it-ciso",),
        "roles": ("Director of IT & Security (5954769)",
                  "Cloud Security & DevOps Engineer (5957273)",
                  "Information Security Analyst (5957297)"),
        "assessment": "Core Assignment (platform title \"Director of IT / "
                      "CISO\"), 180 minutes, 5 modules, plus a 3 to 5 minute "
                      "video",
        "location": "Remote, Poland",
        "spike": "AI risk governance",
        "seat": "The seat owns \"the full scope of internal IT operations, "
                "information security, and compliance across a multi-cloud, "
                "multi-country organization\" and \"starts as an individual "
                "contributor,\" so it is hands-on, not supervisory.",
        "core_skill": "Reach a defensible security or compliance call fast, "
                      "express it as a specific cloud configuration, and hold it "
                      "against a CEO, a legal team and an engineering lead who "
                      "each want a different answer.",
        "competencies": (
            {"label": "Multi-cloud security architecture",
             "asks": "Converts an inherited misconfiguration into a named "
                     "remediation.",
             "anchor": "\"multi-cloud environments (GCP and Azure required)\" "
                       "(Module 1)"},
            {"label": "Incident command",
             "asks": "Sequences containment and evidence across two concurrent "
                     "events.",
             "anchor": "\"incident response procedures\" (Module 2.1)"},
            {"label": "Regulatory determination",
             "asks": "Applies a standard to facts and reaches a notification "
                     "decision.",
             "anchor": "\"Working knowledge of HIPAA and SOC 2 compliance\" "
                       "(Modules 2.2, 3A-3C)"},
            {"label": "Identity and vendor risk",
             "asks": "Designs access tiers, and reads a vendor claim for what it "
                     "omits.",
             "anchor": "\"identity management (SSO, MFA, RBAC)\" (Module 4)"},
            {"label": "Executive translation and refusal",
             "asks": "Says no upward with reasoning and an alternative path.",
             "anchor": "\"translate security concepts for non-technical "
                       "stakeholders\" (Modules 2.3, 3B, 4A.2)"},
            {"label": "AI-native operation",
             "asks": "Uses AI inside the security workflow, and governs its use "
                     "by everyone else.",
             "anchor": "\"Proficiency with AI tools and a working practice of "
                       "using automation\" (Module 5)"},
        ),
        "criteria": (
            {"key": "cloud_architecture",
             "label": "Cloud architecture and remediation (1.1, 1.2)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Five risks ranked on a stated severity basis, each fixed "
                    "with a named service: private connectivity for the Cloud "
                    "SQL public IP and its 0.0.0.0/0 authorized network, workload "
                    "identity federation and least privilege for the Owner-role "
                    "service accounts, per-tunnel VPN keys, Artifact Registry "
                    "with pinned digests for Docker Hub latest, cluster-admin off "
                    "the 5 developers. Target state covers all four pieces, "
                    "secrets migration included.",
                 3: "Right risks, remediation at control level (\"restrict "
                    "IAM\", \"use private networking\"); ranking asserted; target "
                    "state covers two or three pieces.",
                 1: "Generic hardening checklist that never cites this "
                    "environment; no ranking; target state skipped.",
             }},
            {"key": "incident_command",
             "label": "Incident command and breach determination (2.1, 2.2, 2.3)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "First 60 minutes ordered and justified: kill the "
                    "90-day-old service account key, revoke the copy committed to "
                    "the public repo, preserve Cloud SQL audit logs first, scope "
                    "the 14 SELECT queries against symptom and provider-note "
                    "tables, connect the failed Azure AD attempt to an "
                    "offboarding gap. Treats the 3:30 AM stolen laptop as a "
                    "second incident with an explicit prioritization rule and "
                    "names the 4-digit PIN as the hole in FileVault. Breach "
                    "analysis per incident, burden of proof on Ajaia. CEO answer "
                    "refuses the premise and offers a path.",
                 3: "Containment sound, order unexplained, preservation implied; "
                    "incidents merged; PIN unaddressed; CEO answer asserts "
                    "disclosure without reasoning.",
                 1: "Unordered, no preservation or prioritization; no breach "
                    "because no export confirmed; ignores the public-repo "
                    "exposure or the laptop.",
             }},
            {"key": "compliance",
             "label": "Compliance program and policy gaps (3A, 3B, 3C)",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "Overlap and divergence argued concretely, landing on one "
                    "program with overlays. Memo rejects \"since we strip PII, "
                    "HIPAA doesn't apply\" and works the safe harbor standard "
                    "rather than asserting it, treating symptom text and provider "
                    "notes as PHI and naming the BAA requirement. Gap list cites "
                    "absent evidence preservation and the 96-hour notification "
                    "clock, and flags student education records classified as "
                    "Internal as the immediate regulatory risk, with the "
                    "consequence.",
                 3: "Right PHI call, safe harbor asserted not worked; "
                    "preservation or the notification clock missed; "
                    "classification error named without its consequence.",
                 1: "Framework recitation, no application to the three "
                    "obligations; agrees with the legal team; misses the "
                    "classification error.",
             }},
            {"key": "vendor_access",
             "label": "Vendor risk and access design (4A, 4B)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Works every vendor claim: Type II asserted with no report, "
                    "TLS 1.2 as a floor, a no-training claim with no contract "
                    "language, a 2023 internal pen test that is neither current "
                    "nor independent, 90-day input and output logging against PHI "
                    "minimization and the BAA, region and residency, a BAA "
                    "promised but not signed. Refuses the \"we've already built "
                    "the integration\" sign-off with a risk owner, a conditional "
                    "path and an escalation. Access answer is a real matrix "
                    "across roles, systems and tiers, resolving SSO, MFA and the "
                    "three personal GitHub accounts.",
                 3: "Catches the obvious flags, not the retention or residency "
                    "conflict; sign-off met with process language; a role list, "
                    "not a matrix.",
                 1: "Accepts the summary or sends a generic questionnaire; "
                    "approves under pressure or refuses with no path; no access "
                    "structure.",
             }},
            {"key": "ai_workflow",
             "label": "AI in the candidate's own workflow (5A, disclosure)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Three IT or security tasks, each with the specific tool and "
                    "model, the input to output workflow, a trust boundary with a "
                    "reason (no AI-authored breach determination, no client data "
                    "in unapproved tools), and a time-savings estimate. "
                    "Disclosure names an override.",
                 3: "Three tasks and tools named, workflows at altitude, trust "
                    "boundary one line, savings asserted; disclosure lists tools "
                    "with no override.",
                 1: "Fewer than three tasks, no model named, no trust boundary, "
                    "or \"I used ChatGPT to help\".",
             }},
            {"key": "structure",
             "label": "Structure, format and stated tradeoffs",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Markdown by module number; the memo is memo-length and "
                    "readable by a non-technical CEO; assumptions stated where "
                    "the scenario is silent; time allocation across five modules "
                    "visible; disagrees with a premise where warranted and says "
                    "why.",
                 3: "Modules present but uneven, one or two starved; memo runs "
                    "long or reads as a technical brief; assumptions unstated.",
                 1: "No module structure; modules answered in a line each or "
                    "wildly over length; format ignored.",
             }},
            {"key": "ai_governance",
             "label": "Governing AI risk for the organization (5B)",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Five or more risks genuinely specific to AI or LLM use, each "
                    "with likelihood, impact, current control and mitigation. The "
                    "developer pasting client source code into ChatGPT is a "
                    "register entry, and \"OpenAI doesn't train on API inputs\" "
                    "is corrected on specifics (consumer against API terms, "
                    "retention, contract). Framework covers approval, risk "
                    "tiering, data classification, detection of unapproved spend "
                    "against the 9 vendors in expense reports, and consequences, "
                    "rolled out without blame.",
                 3: "Five risks but two or three are generic IT risks reworded; "
                    "likelihood and impact without controls; the technical claim "
                    "uncorrected; framework skips monitoring.",
                 1: "Fewer than five risks, or all generic; developer scenario or "
                    "shadow spend ignored; no approval path, no detection method.",
             }},
        ),
        "auto_fails": (
            "Endorsing the CEO's \"can we just not tell\" position with no "
            "regulatory analysis, or finding no breach because no export was "
            "confirmed while the logs show 14 queries against patient data.",
            "Agreeing with the legal team's HIPAA claim without testing it "
            "against the safe harbor standard.",
            "Signing off on the vendor as presented, or clearing the "
            "already-built integration with no conditions.",
            "A Module 1 answer with no named GCP or Azure service. The cover asks "
            "for \"specific services and configurations, not generic best "
            "practices.\"",
            "Never naming the inherited environment (the 0.0.0.0/0 authorized "
            "network, Owner-role service accounts, the shared pre-shared key, "
            "recovery keys in a shared Sheet).",
            "Fabricated audit findings, log evidence or vendor report contents.",
            "Missing modules entirely, or no walkthrough video link.",
        ),
        "red_flags": (
            "No https://ajaia.ai reference anywhere, which the cover says \"may "
            "be considered incomplete\".",
            "A certification list standing in for a judgment call.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells."},
            {"key": "complete", "label": "Complete: five modules labeled by "
                                         "number, playable video, Drive link "
                                         "where diagrams are referenced."},
            {"key": "format", "label": "Format: Markdown by module, memo-length "
                                       "memo, access answer as a matrix."},
            {"key": "scenario", "label": "Scenario named: an artifact from the "
                                         "inherited environment or the incident "
                                         "timeline."},
            {"key": "specific", "label": "One checkable specific: a named GCP or "
                                         "Azure service tied to a named risk, or "
                                         "a cited regulatory standard."},
            {"key": "ai_note", "label": "AI disclosure non-generic, naming tool, "
                                        "model and one override."},
        ),
        "tells": {
            "strong": "A risk ranking whose top entry is argued rather than "
                      "obvious.",
            "weak": "A Module 1 answer that would fit any company's cloud.",
        },
        "gia": {
            "primary": ("Reasoning", "Perceptual Speed"),
            "secondary": ("Spatial Visualisation",),
            "why": "The seat resolves competing obligations across three "
                   "frameworks and derives a notification decision from "
                   "incomplete evidence; most graded material is dense text whose "
                   "signal is the defect inside it. Spatial Visualisation is "
                   "secondary because 1.2 asks for a network topology and 4B for "
                   "an access matrix. Number Speed is not primary; the assessment "
                   "requires no arithmetic.",
            "proxies": (
                "How many seeded defects in the Module 1 environment block are "
                "caught, and whether they are ranked rather than listed.",
                "How many of the seven vendor claims draw a follow-up question.",
                "Whether the 3:15 AM public-repo disclosure and the 3:30 AM "
                "laptop report re-order the plan instead of being appended to it.",
                "How the 180 minutes were allocated -- \"how you allocate 180 "
                "minutes across 5 modules is itself a signal.\"",
            ),
        },
        "reviewer": {
            "path": (
                "0:00 triage and module count.",
                "2:00 Module 1.1 first -- the fastest true or false in the pack, "
                "counting named services against seeded defects.",
                "5:00 Module 2, reading only the ordering of the first 60 minutes "
                "and the breach call.",
                "8:00 Module 3C classification call, then the Part B memo.",
                "10:00 Module 4 vendor questions and the shape of the access "
                "matrix.",
                "12:00 Module 5 register and framework.",
                "13:30 video at 1.5x for approach, tradeoffs, salary and "
                "identity.",
            ),
            "calibration": "Do not pay for coverage. A submission that lists "
                           "twelve risks and fixes none with a named service "
                           "scores below one that ranks five and names the "
                           "services. A correct call in three sentences beats a "
                           "survey of options.",
            "per_title": "Score all three titles on this grid unchanged so the "
                         "seats stay comparable, then apply emphasis at the band "
                         "edge only. Cloud Security & DevOps Engineer: weight "
                         "cloud architecture and access design, and do not hold a "
                         "thin Module 3A or 2.3 against them at a boundary. "
                         "Information Security Analyst: weight incident command, "
                         "policy gaps and vendor assessment, and treat a light "
                         "Module 1.2 the same way.",
            "probes": (
                "Your first containment move was X. What evidence did it destroy, "
                "and what did you preserve first?",
                "The CEO overrules you and delays client notification by a week. "
                "What do you do on day one of that week?",
                "Name a vendor you approved with conditions. What was the "
                "condition, and did anyone check that it held?",
            ),
        },
        "gaps": (
            "No dedicated assessment exists for the Cloud Security & DevOps "
            "Engineer or Information Security Analyst postings. The only live "
            "assignment maps to the Director/CISO seat and never tests "
            "infrastructure-as-code and CI/CD pipeline security, or periodic "
            "access reviews and alert investigation. Both need their own "
            "instrument.",
            "No JD in this family has a Responsibilities section, and none names "
            "FERPA, which Module 3 tests heavily. Grade the FERPA answers, but do "
            "not reject on FERPA alone.",
        ),
    },

    # -- 11. Full Stack Engineering ----------------------------------------
    {
        "key": "full_stack",
        "unit": "Full Stack Engineering",
        "entity": "Ajaia",
        "slugs": ("full-stack-developer-assignment", "product-engineer"),
        "roles": ("Full Stack Developer (5854957)",
                  "Full Stack Product Engineer (5884789)"),
        "assessment": "AI-Native full stack build, assignments 31 (v2) and 69 "
                      "(v3), 240 platform minutes, body cap 4-6 hours, plus a 3 "
                      "to 5 minute video",
        "location": "Remote, Philippines",
        "spike": "Scope cut defense",
        "seat": "Ajaia hires this engineer to take \"vague requirements\" to "
                "\"robust solutions\" across frontend, backend, API design, "
                "database operations and cloud deployment, where \"priorities "
                "evolve\" and AI is \"a core execution capability.\"",
        "core_skill": "Turn an open prompt into a narrow product slice that runs "
                      "on a URL a stranger can open, and defend the cuts.",
        "competencies": (
            {"label": "Product surface construction",
             "asks": "A usable editor, not a form.",
             "anchor": "\"responsive frontend interfaces and reliable backend "
                       "services\" (Task 1)"},
            {"label": "Access and data modeling",
             "asks": "Ownership enforced server side.",
             "anchor": "\"APIs, databases, authentication systems\" (Tasks 3, 4)"},
            {"label": "Input handling at the boundary",
             "asks": "Untrusted input made useful.",
             "anchor": "\"maintainable, secure, observable\" (Task 2)"},
            {"label": "Shipping discipline",
             "asks": "A testable build in front of a reviewer.",
             "anchor": "\"shipping full stack applications in professional "
                       "environments\" (Task 5)"},
            {"label": "AI-native engineering judgment",
             "asks": "Uses AI hard, owns the output.",
             "anchor": "\"critically evaluate AI-generated output\" (AI Workflow "
                       "Note)"},
            {"label": "Stakeholder-legible communication",
             "asks": "Explains a build to non-coders.",
             "anchor": "\"technical and non-technical stakeholders\" "
                       "(architecture note, SUBMISSION.md, video)"},
        ),
        "criteria": (
            {"key": "editing_experience",
             "label": "Editing experience end to end (1)",
             "block": "work_product", "weight": 18,
             "anchors": {
                 5: "Create, rename, edit, save and reopen work on the live URL; "
                    "bold, italic, underline, a heading and a list survive "
                    "reopen, nesting intact.",
                 3: "Create and edit work, but rename or a format is missing, or "
                    "nested lists flatten.",
                 1: "Plain textarea, or content does not survive save and reopen.",
             }},
            {"key": "sharing_persistence",
             "label": "Sharing and persistence correctness (3, 4)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Owner stored, a second seeded user granted access, owned and "
                    "shared visibly split, the server refuses an ID the caller "
                    "was never granted, and both survive refresh.",
                 3: "Sharing works in the UI, but the check is client side only, "
                    "or the shared list is hardcoded.",
                 1: "Share button with no backing logic, an insecure direct "
                    "object reference, or documents that vanish on refresh.",
             }},
            {"key": "file_upload", "label": "File upload into the workflow (2)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "A stated type yields a real outcome such as a new editable "
                    "document; types listed in UI and README; unsupported or "
                    "oversized files rejected with a message.",
                 3: "Works for one type, but the type list is in only UI or only "
                    "README, or unsupported types fail silently.",
                 1: "No upload path, or the file never surfaces in the product.",
             }},
            {"key": "delivery",
             "label": "Delivery and engineering quality (5)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Live URL opens with no payment or setup step, README "
                    "instructions run as written, credentials supplied, one test "
                    "asserts real sharing or persistence behavior, not that the "
                    "app boots.",
                 3: "Deployment or local run works but not both, the test asserts "
                    "only a render or a 200, or one error path is handled.",
                 1: "No deployment and README does not start the app, no test, or "
                    "reviewers must pay.",
             }},
            {"key": "ai_note", "label": "AI workflow note",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names tools and the slice each carried, one rejected output "
                    "such as a generated serializer that lost list nesting, and "
                    "how it was verified.",
                 3: "Tools and areas of help named, a thin verification claim, no "
                    "rejected output.",
                 1: "\"I used ChatGPT to help,\" a bare tool list, or dead "
                    "generated scaffolding.",
             }},
            {"key": "readability",
             "label": "Constraint compliance and readability",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "SUBMISSION.md matches the folder; architecture note reasons "
                    "the storage and editor-library picks readably; video inside "
                    "3 to 5 minutes naming what was deprioritized; a specific "
                    "next-2-4-hours list.",
                 3: "Materials present, but the video runs long or skips "
                    "deprioritization, or the note omits why.",
                 1: "No SUBMISSION.md, no video, or no statement of what is "
                    "incomplete.",
             }},
            {"key": "scope_cut", "label": "The depth-versus-breadth call",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Names the capability that got depth and what was cut to fund "
                    "it, ties the cut to the 4-6 hour budget, and the depth shows "
                    "in the build.",
                 3: "Says what was deprioritized but not why it beat the "
                    "alternative, or the depth is not findable.",
                 1: "No cut stated, five half-working features across tasks 1 to "
                    "4, or a stretch item over a broken core.",
             },
             "note": "The spike scores the decision; communication scores the "
                     "telling of it."},
        ),
        "auto_fails": (
            "Screenshots or video with no source code.",
            "No deployment plus a README that does not start the app.",
            "A build that is not the scenario -- a to-do app rather than a "
            "document editor.",
            "A link that 404s or is paywalled.",
            "No AI Workflow Note.",
        ),
        "red_flags": (
            "Red flags cap a criterion rather than end the review. No test caps "
            "Delivery at 1. A share endpoint with no server-side authorization "
            "caps Sharing and persistence at 1 however complete the UI.",
            "A bulk first commit of a scaffold, or a README naming another "
            "product, points at a cloned starter.",
            "Missing credentials: ask once, then score task 3 on the code.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: no burner domain, no "
                                      "README echoing JD phrasing, no bulk "
                                      "commit of an unrelated scaffold."},
            {"key": "complete", "label": "Complete: source, README, architecture "
                                         "note, AI note, SUBMISSION.md, live "
                                         "URL, video."},
            {"key": "caps", "label": "Caps and format: video 3 to 5 minutes, "
                                     "Markdown or PDF, credentials supplied, "
                                     "nothing paywalled."},
            {"key": "scenario", "label": "Engages the scenario: a document editor "
                                         "with create, rename, edit, reopen."},
            {"key": "claim", "label": "One checkable claim: the live URL opens "
                                      "and a document survives refresh."},
            {"key": "ai_note", "label": "AI note names tools and one rejection."},
        ),
        "tells": {
            "strong": "A live URL opening clean with seeded logins.",
            "weak": "No deployment and a README promising features the code "
                    "lacks.",
        },
        "gia": {
            "primary": ("Reasoning", "Spatial Visualisation"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "The task is deductive, deriving a product slice from "
                   "\"inspired by Google Docs\" and access rules from three "
                   "sentences, and spatial: a rich-text document is a nested "
                   "structure candidates either preserve through save and reopen "
                   "or flatten.",
            "proxies": (
                "The distance from open prompt to delivered slice.",
                "Whether nested lists survive reopen (task 1).",
                "Whether sharing is data rather than a hardcoded map, enforced as "
                "a rule not drawn in the UI (tasks 3, 4).",
                "Whether unsupported types and missing documents were anticipated "
                "(tasks 2, 5).",
                "Weak numerical proxy: whether the \"another 2-4 hours\" list fits "
                "what got finished.",
            ),
        },
        "reviewer": {
            "path": (
                "0-2: the six checks.",
                "2-6: on the live URL -- create a document, rename it, type a "
                "heading, a bold run and a nested list, refresh, reopen; upload "
                "the README's stated type, then one it does not.",
                "6-9: as the second seeded user, confirm the owned versus shared "
                "split, then request the owner's document ID with no grant.",
                "9-12: SUBMISSION.md and the architecture note for the cuts.",
                "12-15: the share check, the upload handler, the test, the video "
                "at 1.5x.",
            ),
            "calibration": "Breadth is not the target: five features at half "
                           "working is a 3 across the board; three working end to "
                           "end plus a clear account of the two cut is the 5 "
                           "pattern. A polished editor with cosmetic sharing "
                           "scores below a plain one with real access logic.",
            "per_title": "Same grid, different tie-breaks. Full Stack Developer "
                         "breaks on the AI note and deployment. Full Stack "
                         "Product Engineer breaks on the spike (\"Own projects "
                         "from concept to production deployment\").",
            "probes": (
                "What happens server side when a non-owner requests a document ID "
                "never granted them?",
                "On the next-2-4-hours list, what would you cut to ship sooner?",
                "Which AI-generated block did you reject, and why?",
            ),
        },
        "gaps": (
            "The Full Stack Product Engineer JD is partly designer copy-paste, "
            "asking for \"product design, brand design, or adjacent digital "
            "design roles\". Do not grade portfolio or brand work.",
            "Nothing in the assessment exercises its \"accessible frontend "
            "experiences,\" analytics or regulated-environment lines; score only "
            "if volunteered.",
            "No salary question on either posting; no benefit summary on 5884789.",
            "The body's 4-6 hours governs the platform's 240 minutes.",
        ),
    },

    # -- 12A. IT Manager ---------------------------------------------------
    {
        "key": "it_manager",
        "unit": "IT Manager and Social Media",
        "grid_name": "Grid A. IT Manager",
        "entity": "Ajaia",
        "slugs": ("it-manager",),
        "roles": ("IT Manager (5896097)",),
        "assessment": "AI-Native IT Manager. Systems, Security and Compliance "
                      "Design, 180 minutes",
        "location": "Remote",
        "spike": "Approved-stack ownership",
        "seat": "The seat is \"the technical backbone of our AI-first "
                "operations\": internal cloud environments and the SaaS stack, "
                "security over \"sensitive client data,\" CRM/CMS automation, and "
                "translation for non-technical leadership.",
        "core_skill": "Turning ungoverned tool sprawl into a controlled system.",
        "competencies": (
            {"label": "Incident judgment",
             "asks": "Containment before cleanup.",
             "anchor": "\"high-level judgment in fast-paced, cross-functional "
                       "startup environments\" (Task 1)"},
            {"label": "Infrastructure and identity design",
             "asks": "An access model, not a list.",
             "anchor": "\"internal cloud environments and SaaS stacks\" (Tasks "
                       "2A-2B)"},
            {"label": "Compliance enforcement",
             "asks": "Regulation as controls.",
             "anchor": "\"protect Ajaia's proprietary AI workflows\" (Task 2C)"},
            {"label": "AI-native execution",
             "asks": "AI as daily method.",
             "anchor": "\"Use AI tools daily for network ideation\" (Workflow "
                       "Note, 2D)"},
        ),
        "criteria": (
            {"key": "incident_response", "label": "Incident response (1A-1C)",
             "block": "work_product", "weight": 24,
             "anchors": {
                 5: "Rotates the exposed key first, checks logs, then Notion and "
                    "Drive cleanup, named comms owner.",
                 3: "Key rotated, permissions cleaned, no log check, vague comms.",
                 1: "Generic phases; never acts on the exposed key.",
             }},
            {"key": "architecture",
             "label": "Architecture, access, compliance (2A-2C)",
             "block": "work_product", "weight": 26,
             "anchors": {
                 5: "Cloud, identity, collaboration, AI layers with identity "
                    "flowing between them; tiers by role; HIPAA and FERPA "
                    "separated against the scenario data.",
                 3: "Platforms without connections; access is admin and user; "
                    "three frameworks as one control set.",
                 1: "Tool inventory; no identity model; frameworks with no "
                    "enforcement.",
             }},
            {"key": "ai_ops",
             "label": "AI ops, ticketing, 30-day plan (2D-2F)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Three workflows with the automated versus human boundary; "
                    "intake, priority, routing, tiers; plan ordered by exposure.",
                 3: "No automated versus human split; service tiers but no "
                    "routing.",
                 1: "AI tools listed as workflows; ticketing is a shared inbox.",
             }},
            {"key": "ai_note", "label": "AI Workflow Note",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Tools per task, one concrete override of AI output with its "
                    "reason.",
                 3: "Tools named; override claimed, not tied to an artifact.",
                 1: "\"Used ChatGPT to help.\" A missing note is an auto-fail.",
             }},
            {"key": "structure", "label": "Structure and constraints",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Sectioned Markdown, architecture as diagram or structured "
                    "explanation, video inside 5-8 minutes.",
                 3: "Unstructured; video outside 5-8 minutes; ajaia.ai reference "
                    "missing.",
                 1: "Wall of text, link dead or restricted.",
             }},
            {"key": "approved_stack",
             "label": "Who approves a tool, and who notices when nobody did",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Approved-tool inventory with a named owner, a request path a "
                    "requester can follow, rules on which data enters which "
                    "model, and how an unapproved tool is spotted after the fact.",
                 3: "An approved list and a policy, no owner and no route for a "
                    "new request.",
                 1: "\"Create an AI usage policy,\" no inventory, no owner, no "
                    "request path.",
             }},
        ),
        "auto_fails": (
            "Task 1 or Task 2 missing.",
            "An incident response that never addresses the exposed API key.",
            "A compliance section that never touches the healthcare and "
            "education data.",
            "A dead Drive link.",
            "No AI Workflow Note.",
        ),
        "red_flags": (
            "No ajaia.ai reference, grounds for incomplete.",
            "A vendor list presented as architecture.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: JD phrasing parroted back, "
                                      "block-capital salutations, burner "
                                      "domains."},
            {"key": "complete", "label": "Complete: Tasks 1 and 2, video, "
                                         "Workflow Note."},
            {"key": "caps", "label": "Caps: 5-8 minute video."},
            {"key": "scenario", "label": "Names the scenario: the exposed key and "
                                         "Drive permissions."},
            {"key": "number", "label": "One checkable number: a service tier, "
                                       "retention period, or threshold with a "
                                       "decision."},
            {"key": "ai_note", "label": "AI evidence non-generic."},
        ),
        "tells": {
            "strong": "A request path a requester could actually follow.",
            "weak": "A long Task 2 that never says who approves a new AI tool.",
        },
        "gia": {
            "primary": ("Reasoning", "Perceptual Speed"),
            "secondary": ("Spatial Visualisation",),
            "why": "Reasoning fits sequencing containment and separating three "
                   "compliance regimes; Perceptual Speed fits error detection "
                   "across permissions and logs. Spatial Visualisation is added "
                   "for the 2A architecture; the pack maps no secondary.",
            "proxies": (
                "The ordering of Task 1's four issues.",
                "Whether 2C treats HIPAA, FERPA and SOC 2 as distinct.",
                "Scope control over six sub-tasks.",
            ),
        },
        "reviewer": {
            "path": (
                "Video for the incident narrative (2 min).",
                "Task 1 for revocation and ordering (3).",
                "2B tiers and 2C enforcement (4).",
                "2D-2F for the automated versus human line (2).",
                "Workflow Note (1).",
            ),
            "calibration": "Volume is not quality. A long Task 2 that never says "
                           "who approves a new AI tool scores below a short one "
                           "that does.",
            "probes": (
                "What would you skip in the first hour?",
                "Which AI workflow would you kill?",
            ),
        },
        "gaps": (
            "The assessment tests no asset procurement or CRM/CMS automation, "
            "both of which the JD names.",
            "No video task asks salary expectations.",
        ),
    },

    # -- 12B. Social Media -------------------------------------------------
    {
        "key": "social_media",
        "unit": "IT Manager and Social Media",
        "grid_name": "Grid B. Social Media and Video Content Manager",
        "entity": "Ajaia",
        "slugs": ("social-media-manager",),
        "roles": ("Social Media and Video Content Manager (5993245)",),
        "assessment": "Repurpose an Ajaia Report Into Multi-Channel Content, 120 "
                      "minutes, hard stop",
        "location": "Remote",
        "spike": "Repurposing judgment",
        "seat": "The seat owns \"content execution across social, short and "
                "long-form video, podcast workflows, and training video,\" writes "
                "for LinkedIn, X and email, and owns \"the calendar, the output, "
                "and the numbers.\"",
        "core_skill": "Taste at speed: one idea rendered correctly per channel.",
        "competencies": (
            {"label": "Video craft",
             "asks": "Cuts for retention, not effects.",
             "anchor": "\"You edit video people actually finish watching\" (Task "
                       "1, weighted heaviest)"},
            {"label": "Channel-native writing",
             "asks": "Platform register, not one message reposted.",
             "anchor": "\"platform-native content for LinkedIn, X, and email\" "
                       "(Tasks 2-3)"},
            {"label": "Brand voice control",
             "asks": "Voice held under pressure.",
             "anchor": "\"Hold the line on brand voice and quality\" (Task 2)"},
            {"label": "Performance literacy",
             "asks": "A decision on every metric.",
             "anchor": "\"You own the calendar, the output, and the numbers\" "
                       "(Task 4)"},
        ),
        "criteria": (
            {"key": "video_edit", "label": "Video edit (Task 1)",
             "block": "work_product", "weight": 28,
             "anchors": {
                 5: "30-60 seconds, hook inside 1-3 seconds, tight cuts, legible "
                    "captions, on-screen hierarchy; bullets name hook, cut, CTA, "
                    "v2 test.",
                 3: "Hook after 5 seconds; captions uncorrected; bullets restate "
                    "the video.",
                 1: "Lightly trimmed source, no captions, generic bullets.",
             }},
            {"key": "posts", "label": "LinkedIn and X posts (Tasks 2-3)",
             "block": "work_product", "weight": 26,
             "anchors": {
                 5: "Written for the buyer who \"has already sat through a failed "
                    "pilot\"; correct dimensions; X visual carries the idea.",
                 3: "General AI-vendor voice; asset off-dimension; X reads as "
                    "trimmed LinkedIn copy.",
                 1: "Report summary, no point of view; placeholder text.",
             }},
            {"key": "kpi_plan", "label": "KPI plan (Task 4)",
             "block": "work_product", "weight": 16,
             "anchors": {
                 5: "Each channel gets a primary KPI, why it matters, and the "
                    "change underperformance triggers.",
                 3: "KPIs with rationale, but the change on underperformance is "
                    "vague.",
                 1: "Generic engagement metrics with no decision attached.",
             }},
            {"key": "ai_leverage", "label": "AI leverage",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Tools named by stage (script, caption, design, edit) with "
                    "time saved, plus one refusal.",
                 3: "Tools named and speed claimed; no stage mapping, no refusal.",
                 1: "\"Used AI for ideas.\" No AI segment is an auto-fail.",
             }},
            {"key": "walkthrough", "label": "Walkthrough and time box",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Three minutes or less on angles, adaptation, edit, AI, "
                    "judgment; says what was cut.",
                 3: "Over 3 minutes, or the AI or judgment segment missing.",
                 1: "No walkthrough, or over the 2-hour box unexplained.",
             }},
            {"key": "cross_channel", "label": "Cross-channel angle",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "A specific angle from the 2026 AI Reality Check Report "
                    "rendered per channel: compression on X, argument on "
                    "LinkedIn, hook on video.",
                 3: "One angle carried across all three channels with light "
                    "rewording.",
                 1: "Generic summary; would fit any AI vendor.",
             }},
        ),
        "auto_fails": (
            "No Google Drive folder, or an inaccessible one, which the assignment "
            "says will not be reviewed.",
            "No edited video.",
            "Video under 15 seconds or over 90, breaching the 30-60 second cap by "
            "half.",
            "No walkthrough.",
            "No AI segment.",
        ),
        "red_flags": (
            "Placeholder text or wrong dimensions against a production-ready "
            "instruction.",
            "Statistics absent from the report.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: JD phrasing parroted back, "
                                      "block-capital salutations, burner "
                                      "domains."},
            {"key": "complete", "label": "Complete: video, LinkedIn post and "
                                         "asset, two X posts, KPI page, "
                                         "walkthrough."},
            {"key": "caps", "label": "Caps: 30-60 second video, 5 slides, "
                                     "one-page KPI plan, 3-minute walkthrough."},
            {"key": "scenario", "label": "Names the scenario: the 2026 AI Reality "
                                         "Check Report."},
            {"key": "number", "label": "One checkable number: a KPI threshold "
                                       "with a decision."},
            {"key": "ai_note", "label": "AI evidence non-generic."},
        ),
        "tells": {
            "strong": "A KPI with the change underperformance triggers.",
            "weak": "A video whose motion ignores the hook.",
        },
        "gia": {
            "primary": ("Word Meaning", "Perceptual Speed"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "Word Meaning fits compressing a report into a hook and three "
                   "channel registers; Perceptual Speed fits caption timing and "
                   "dimension errors. Number Speed is added for KPI thresholds; "
                   "the pack maps no secondary.",
            "proxies": (
                "The angle chosen in 15 minutes.",
                "What was cut.",
                "Whether KPI targets are numbers.",
            ),
        },
        "reviewer": {
            "path": (
                "Video cold, judging the first 3 seconds and captions (2 min).",
                "Edit bullets (2).",
                "LinkedIn asset full size (3).",
                "X posts (2).",
                "KPI page for the decision on each metric (3).",
                "Walkthrough (1).",
            ),
            "calibration": "Volume is not quality. A video whose motion ignores "
                           "the hook scores below a short one that does not.",
            "probes": (
                "Which asset would you publish first?",
                "What did you cut at 90 minutes?",
            ),
        },
        "gaps": (
            "The assessment tests no email newsletter, podcast or training video, "
            "all of which the JD names.",
            "It needs designed graphics the JD never names.",
            "There is no AI Workflow Note; the walkthrough's AI segment is graded "
            "instead.",
            "No video task asks salary expectations.",
        ),
    },
    # -- 13. AI Strategy ---------------------------------------------------
    #
    # The one grid in the pack that does not split 70/10/10/10. Background and
    # experience is a 40-point row here, not the 10-point tiebreaker it is
    # everywhere else, and the work product gives up the difference. That was
    # the instruction with the pack; section 10 of `notes` sets out what it
    # costs, and `config.CV_WEIGHT_BY_SEAT` pins this seat's external CV weight
    # to 0.0 so the resume is not also paid for a second time in the blend.
    #
    # Both grids in this unit were retargeted on 2026-08-31 from assignment 77
    # (Core Assignment v4) to assignment 81 (v7), which is what the portal now
    # serves. The change is delivery, not substance. v4 collected two separate
    # video files and both grids said so -- "two videos that play", "both video
    # links open", "Video 1" and "Video 2" as named artefacts. v7 collects the
    # same two segments in ONE place: "Record both parts as a single continuous
    # video... Do not submit two separate files. The portal accepts exactly one
    # video submission per candidate", delivered as a shared folder link.
    #
    # So the wording below asks for the two PARTS and is indifferent to how
    # many files carry them -- one combined recording, or several inside the
    # one link submitted. That is the only phrasing that is fair to both
    # cohorts at once: a v4 candidate who sent two links still has both parts,
    # and a v7 candidate who was told not to send two files is no longer
    # failing a check they were instructed not to satisfy.
    #
    # Nothing here re-weights anything. Every row keeps its block and its
    # points, and the questions the anchors ask -- did they present rather than
    # read, did they answer Priya's interruption, did they say something real
    # about what they did not enjoy -- are unchanged. Editing anchor text does
    # move `grid_version`, so the twelve cards graded before this date show on
    # the dashboard as scored against the earlier grid. That flag is accurate
    # and worth leaving: they were.
    #
    # One thing that looks like drift and is not: the mechanics check still
    # requires the deck "attached as a file rather than a link". v7 kept that
    # ("Submit it as a PDF or PowerPoint file") -- the folder link is for the
    # video only. The linked decks failing that check today are real misses.
    {
        "key": "ai_strategy",
        "unit": "AI Strategy",
        "grid_name": "Grid A. Senior AI Strategist",
        "entity": "Ajaia",
        "slugs": ("ai-strategist",),
        # Both AI Strategist postings feed one portal assignment, so this slug
        # is claimed twice -- by this grid and by 13B -- and `tier` is what
        # tells them apart. `tier_default` marks the grid a candidate is marked
        # against when the posting they applied to cannot be resolved: the
        # senior one, because its background anchors are the stricter pair and
        # a wrong guess in that direction costs a second look rather than a
        # false advance. See `for_slug` and `config.JOB_TIERS`.
        "tier": "senior",
        "tier_default": True,
        "roles": ("Senior AI Strategist (218F45AD60)",),
        "assessment": "Ajaia AI Strategist Assessment, 90 minutes including "
                      "the deck, plus one continuous 12 to 13 minute video: a "
                      "10-minute recorded C-suite presentation, then a 2 "
                      "to 3 minute candidate introduction",
        "location": "New York, on-site, full-time, 4 to 7 years, $150,000 to "
                    "$250,000 base",
        "spike": "Executive nerve and claim discipline",
        # 40 / 40 / 6 / 7 / 7. Stated in full rather than as a diff, because a
        # reader who sees only "background: 40" cannot tell what paid for it.
        "block_points": {
            "work_product": 40,
            "background": 40,
            "ai_forwardness": 6,
            "communication": 7,
            "spike": 7,
        },
        # The pack's universal auto-fails are repealed by this rubric's own
        # section 7, which is why they are switched off rather than quietly
        # tolerated. "There are no caps in this version", a missing task
        # "scores that criterion 1, grade the rest normally", and the only
        # auto-fail is confirmed fraud. See `auto_fails_of`.
        "universal_auto_fails": False,
        "seat": "The seat runs client engagements from first audit to deployed "
                "system: \"Run AI audits inside client operations,\" \"Decide "
                "what to fix first, and sequence it,\" \"Write the build spec "
                "your engineering partner works from,\" \"Present to the "
                "C-suite,\" and \"Stay on the number after go-live.\" It owns "
                "the engagement rather than staffing to one -- scoping, "
                "directing forward deployed engineers and analysts, handling "
                "the hard client conversations, and growing the account.",
        "core_skill": "Work out what is actually happening inside a business, "
                      "decide what changes and in what order, and hold that "
                      "recommendation in front of a chief executive who wants "
                      "a different answer.",
        "competencies": (
            {"label": "Diagnostic judgment",
             "asks": "Reconstructs how the organization really runs from "
                     "contradictory inputs, and says which version is being "
                     "worked from.",
             "anchor": "\"Map how the business runs, not how the org chart says "
                       "it runs\" (Tasks 1 and 2)"},
            {"label": "Prioritization on a stated basis",
             "asks": "Declares a ranking basis and applies it to the last item "
                     "as strictly as the first.",
             "anchor": "\"Decide what to fix first\" (Task 1)"},
            {"label": "Automation restraint",
             "asks": "Knows when the answer is a field, a template, or an owner.",
             "anchor": "\"A required field, a template, or a named owner closes "
                       "more problems than a model does\" (Task 1)"},
            {"label": "Executive translation",
             "asks": "A deck a chief executive can decide from.",
             "anchor": "\"One recommendation, the tradeoffs stated, a decision "
                       "at the end of it\" (Task 3, the presentation)"},
            {"label": "Specification craft",
             "asks": "Converts a recommendation into something an engineer can "
                     "build and test.",
             "anchor": "\"the outcome, the scope, the systems and data, where a "
                       "human signs off, and how we know it is done\" (Task 4)"},
            {"label": "Outcome ownership",
             "asks": "Baselines before claiming, measures after shipping.",
             "anchor": "\"Get a baseline before anyone claims an improvement\" "
                       "(Tasks 1, 3, 4)"},
            {"label": "Track record",
             "asks": "Has done work where business judgment was the product, "
                     "carried a recommendation to people who could say no, and "
                     "taken AI into production.",
             "anchor": "Scored from the resume and the Workable profile"},
        ),
        "criteria": (
            {"key": "triage",
             "label": "Opportunity triage and prioritization (Task 1)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Basis stated before it is used and still applied at the "
                    "last item. Catches at least three of the six seeded "
                    "defects, including the records average or the "
                    "system-of-record gap. Tags are honest, with at least one "
                    "item correctly called a process fix rather than AI. "
                    "Something is explicitly deferred with its cost named. "
                    "Sizing is either computed or refused out loud with what is "
                    "missing.",
                 3: "Right opportunities found and workably ranked, but the "
                    "basis is asserted rather than applied, or one or two "
                    "defects are caught while the records average passes "
                    "unchallenged, or everything is tagged automation or AI.",
                 1: "The same four surface items listed with no ranking basis, "
                    "or an AI agent proposed for each complaint, or Marcus's "
                    "4.2 hour average used as the case for a records build.",
             },
             "note": "Do not withhold a 5 for a different first workflow than "
                     "you would have picked. Prior authorization, records "
                     "requests, referral follow-up and the duplicate-entry "
                     "problem are all defensible openings, and a candidate who "
                     "reframes the engagement entirely -- arguing the real "
                     "constraint is clinician documentation behavior rather "
                     "than any downstream workflow -- can earn a 5. Grade the "
                     "basis and whether it holds, never the pick."},

            {"key": "current_state",
             "label": "Current-state diagram and gaps (Task 2)",
             "block": "work_product", "weight": 6,
             "anchors": {
                 5: "A reader could follow it from trigger to close: actors, "
                    "systems, decision points, handoffs, and the specific leak "
                    "points marked. Inference is separated from what the "
                    "materials say. Questions target ownership, exceptions and "
                    "business rules, and each states what changes in the plan "
                    "depending on the answer.",
                 3: "A linear map with owners and systems but few branches or "
                    "leak points, or questions that are reasonable but do not "
                    "move the plan, or inference presented as fact.",
                 1: "A narrative retell with no step structure, or questions "
                    "already answered in the materials.",
             },
             "note": "Any format scores the same -- a text flow, Mermaid, "
                     "numbered steps with owner tags, a table or an image."},

            {"key": "ceo_deck", "label": "The CEO deck (Task 3)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Recommendation on slide one. Findings compressed rather "
                    "than replayed. The 90-day sequence has a stated reason for "
                    "its order. Cost includes her people's time, not only "
                    "money. The committed outcome is a number with a "
                    "measurement method behind it, and baseline capture is "
                    "inside the first two weeks with the consequence of "
                    "skipping it named. The last slide asks her one real "
                    "decision. As an artifact it is client-ready: clean and "
                    "legible, one idea per slide, text sized to be read on a "
                    "screen across a room, and nothing on it you would have to "
                    "apologize for.",
                 3: "Slides carry the content but the recommendation is buried "
                    "behind context, or the outcome is asserted without a "
                    "measurement method, or baseline is mentioned without being "
                    "scheduled, or nothing is asked of her. Or the substance is "
                    "there and the craft is not: dense paragraphs on slides, "
                    "inconsistent formatting, a chart that has to be explained "
                    "before it can be read.",
                 1: "A findings readout rather than a recommendation, or "
                    "Priya's 20 percent adopted with no baseline anywhere, or "
                    "an artifact you could not put in front of a client: "
                    "unformatted text dumps, unreadable at presentation size, "
                    "or visibly unfinished.",
             },
             "note": "Craft must never rescue a submission. A beautiful deck "
                     "that adopts the 20 percent, proposes an AI records agent "
                     "and never mentions the attestation requirement is weak. "
                     "Substance and craft are scored together here and neither "
                     "carries a submission alone. Equally, do not punish a "
                     "plain deck that is clean, legible and correctly "
                     "sequenced: client-ready is the bar, not designed. An "
                     "unfinished deck is common at 90 minutes and is the "
                     "time-allocation judgment this version tests, so it is "
                     "scored here rather than excused."},

            {"key": "build_spec",
             "label": "Engineering build specification (Task 4)",
             "block": "work_product", "weight": 10,
             "anchors": {
                 5: "Describes the same system as the deck. Problem stated as "
                    "an outcome rather than a feature. Out of scope is "
                    "explicit. The clinician attestation gate, or another "
                    "defended human checkpoint, is designed in with a reason. "
                    "Acceptance criteria are testable by someone who was not in "
                    "the room. Two or three named failure modes each carry "
                    "something built to catch them. A kill condition is stated.",
                 3: "Most elements present but thin: acceptance criteria that "
                    "restate the feature, failure modes without detection, "
                    "human review asserted with no checkpoint, or a spec that "
                    "has quietly drifted from the deck.",
                 1: "A feature list with no scope boundary and no acceptance "
                    "criteria, or a fully automated medical necessity "
                    "determination, which the compliance note rules out.",
             },
             "note": "Read this immediately after the deck. The deck and the "
                     "spec have to be the same recommendation written twice; "
                     "when they drift apart the candidate is producing "
                     "documents rather than owning a solution."},

            {"key": "track_record", "label": "Track record for this seat",
             "block": "background", "weight": 40,
             "anchors": {
                 5: "All three, at scale. Four to seven years where business "
                    "judgment was the product, at an employer with real scope: "
                    "management consulting, corporate strategy, private equity "
                    "or venture capital operating work, product management, "
                    "senior operating roles, or a business they founded and ran "
                    "with something to show for it. And they have carried a "
                    "recommendation to people who could say no, with evidence "
                    "of ownership rather than staffing: they scoped the work, "
                    "ran the room, or owned the account. And AI systems they "
                    "took into or near production that somebody other than them "
                    "used. A well-regarded firm, a notable employer, or a track "
                    "record that would make you take the call regardless also "
                    "reads 5.",
                 3: "Adjacent and credible, or unknown. Right function and "
                    "right years but no evidence they ran the room, or "
                    "executive exposure with AI work that stops at pilots and "
                    "demos, or the right scale in a different domain with an "
                    "obvious bridge into this seat. Also where an "
                    "under-experienced candidate lands whose work is otherwise "
                    "strong. Background not stated anywhere also scores 3, with "
                    "a note. Never 1 for absence of information.",
                 1: "Nothing connects. No judgment work, no client exposure, "
                    "nothing built, and nothing in the history suggesting a "
                    "fast ramp.",
             },
             "note": "This row is worth 40 points, so it has five anchors, not "
                     "three. 4 = all three of judgment work, executive exposure "
                     "and AI in production, with one of them lighter: the full "
                     "picture at a smaller or less known employer, or strong on "
                     "two and credible on the third. 2 = one of the three, and "
                     "thin: relevant work history with no client-facing "
                     "evidence and nothing built, or heavy building with "
                     "nothing suggesting they can sit in front of a chief "
                     "executive and hold a position. On years, four to seven is "
                     "the target and not a filter -- three years owning "
                     "engagements can score 4 or 5, nine years always staffing "
                     "to other people's is a 3. Score the shape of the track "
                     "record, not the date arithmetic. Do not screen on the "
                     "bachelor's degree, which the JD lists as a preference, "
                     "and do not assess whether a claim is inflated -- that is "
                     "a probe, not a score. Because this row carries 40 points, "
                     "resolve genuine ambiguity upward and note it."},

            {"key": "ai_note",
             "label": "AI leverage with judgment (Task 5 and the work)",
             "block": "ai_forwardness", "weight": 6,
             "anchors": {
                 5: "Judgment rather than volume. Names one specific thing "
                    "checked, corrected or rejected during this sitting, or a "
                    "defended call about what stayed human. Tools tied to tasks.",
                 3: "Partial or informal: tools and tasks named without "
                    "verification, a boundary asserted with no instance, or "
                    "work visibly AI-assisted and better for it. Heavy AI use "
                    "with no checking sits here.",
                 1: "No evidence of AI use anywhere in the submission.",
             },
             "note": "A missing Task 5 is a real miss but not an auto-fail "
                     "here. Grade the row from whatever the work reveals, and "
                     "score 1 only when nothing anywhere shows AI use."},

            {"key": "delivery",
             "label": "Presence, length judgment, and submission craft",
             "block": "communication", "weight": 7,
             "anchors": {
                 5: "Presents rather than reads. Talks to the room in Part 1, "
                    "uses the slides as support, holds roughly to 10 minutes, "
                    "and both screen and speaker are visible and audible "
                    "throughout. Length is judged, not padded: a reader finds "
                    "the recommendation immediately and an engineer finds the "
                    "acceptance criteria immediately. Written work sectioned by "
                    "task number. Deck submitted as a file. Both parts "
                    "present in what was submitted, Ajaia in the caption if "
                    "hosted on YouTube, ajaia.ai referenced in the "
                    "materials, every link opens. "
                    "Part 2 answers the enjoyed and did not enjoy question "
                    "with something real.",
                 3: "Delivery is competent but flat or over-rehearsed, or the "
                    "presentation runs well past or short of 10 minutes, or "
                    "only the screen is captured with no speaker on camera. Or "
                    "the written work is readable but bloated, or so terse that "
                    "a required element is missing. One or two submission "
                    "misses: a slide link instead of a file, no ajaia.ai "
                    "reference, salary buried, Part 2 answered with "
                    "pleasantries.",
                 1: "Reads the slides aloud with no audience in mind, or a link "
                    "that does not open, or a video missing entirely, or the "
                    "written work is one undifferentiated block, or nothing was "
                    "recorded of the speaker at all.",
             },
             "note": "There are no length caps in this version, so bloat that "
                     "buries the recommendation and terseness that drops the "
                     "acceptance criteria are both scored here as judgment "
                     "failures, and again in the criterion where the damage "
                     "lands. A deck submitted as a link deducts here and is "
                     "never a reject; grade its content normally."},

            {"key": "claim_discipline",
             "label": "Holding the position under pressure",
             "block": "spike", "weight": 7,
             "anchors": {
                 5: "Declines to commit to a number the evidence does not yet "
                    "support, and puts that on a slide rather than in a "
                    "footnote. Names the attestation requirement as a cap on "
                    "the most attractive automation instead of routing around "
                    "it. Answers the \"why not just buy a platform\" "
                    "interruption on its own terms, with a reason rather than a "
                    "vendor comparison. Steady, unhurried, no hedging.",
                 3: "The limitation appears somewhere in the written work but "
                    "the deck or the presentation softens it, or the platform "
                    "question is answered with feature comparison, or the "
                    "attestation requirement is noted without being designed "
                    "for.",
                 1: "Commits to the 20 percent with no baseline, or presents "
                    "every item as certain, or Part 1 never engages the "
                    "interruption.",
             },
             "note": "Priya interrupts around minute two of Part 1. If nothing "
                     "is on camera anywhere, score this from the deck and the "
                     "written work alone and carry the missing recording to the "
                     "screen as the first question."},
        ),
        # One auto-fail, and it is the pack's fraud rule rather than a scoring
        # rule. Everything else this rubric could have rejected on -- a missing
        # task, a linked deck, a dead video, an invented number -- it scores
        # instead. See `do_not_penalize` and `universal_auto_fails` above.
        "auto_fails": (
            "Confirmed fraud or misrepresentation, visible on the face of the "
            "submission: fake or duplicated identity, another person's work "
            "submitted as their own, a visibly different person across the two "
            "parts of the video, burner-domain or automated-apply patterns, "
            "or materials "
            "that hand the JD's own phrasing back as analysis. This routes to "
            "the fraud log, not to grading.",
        ),
        "red_flags": (
            "Patient data proposed for a consumer AI tool, or no sign the "
            "candidate registered that Alicia's compliance note raises a "
            "question about which vendors hold a signed business associate "
            "agreement. This is a serious problem for a seat that works in "
            "regulated environments: score the criterion down and carry it to "
            "the screen as a hard probe.",
            "A clean, confident 90-day plan in which nothing is uncertain and "
            "no baseline is required.",
            "Generic or off-scenario content: a submission that never names the "
            "records log, the tracking spreadsheet, the attestation requirement "
            "or the clinicians working outside the system scores 1 on the "
            "criteria it fails to address.",
        ),
        "do_not_penalize": (
            "A missing task. Score that criterion 1, grade the rest normally, "
            "and note it as a screen question. Five deliverables including a "
            "client-ready deck inside 90 minutes means the usual cause is the "
            "timer.",
            "A deck submitted as a link, or as a document rather than slides. "
            "Deduct inside communication and delivery and grade the content "
            "normally.",
            "A dead video link. Ask once; if it never opens, grade what is "
            "reachable and score the dependent criteria from the written work "
            "and the deck. Nobody is closed out on a permissions error.",
            "A figure labelled as an assumption. The pack supplies no clinician "
            "time data, no cost per records request and no referral conversion "
            "rate, so assuming out loud is the right behaviour and costs "
            "nothing. Asserted as fact, it costs a point on the criterion it "
            "sits under and becomes a probe.",
            "Re-recorded video, an inflated-looking resume, or prose that reads "
            "as AI-written. Reviewers run no forensics; anything needing "
            "verification becomes an interview probe, never a mark.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, JD phrasing returned, identity "
                                      "inconsistent between the "
                                      "presentation and the introduction."},
            {"key": "complete", "label": "Substantially complete: five tasks, a "
                                         "deck, and a video that plays and "
                                         "carries both parts -- one "
                                         "combined file or several, in the "
                                         "one place submitted."},
            {"key": "mechanics", "label": "Submission mechanics: deck attached "
                                          "as a file rather than a link, the "
                                          "submitted video link opens and "
                                          "everything in it plays, Ajaia in "
                                          "the "
                                          "caption if on YouTube, ajaia.ai "
                                          "reference present, Markdown "
                                          "sectioned by task."},
            {"key": "scenario", "label": "Engages the scenario: names the "
                                         "records log, the tracking "
                                         "spreadsheet, the attestation "
                                         "requirement, or the clinicians "
                                         "working outside the system."},
            {"key": "number", "label": "One checkable claim: a median, a stated "
                                       "ranking basis, a baseline proposal, or "
                                       "a labelled assumption."},
            {"key": "ai_note", "label": "AI note present and specific."},
        ),
        "tells": {
            "strong": "The word \"median\", or any recomputation of the 4.2 "
                      "hour average.",
            "weak": "A clean, confident 90-day plan in which nothing is "
                    "uncertain and no baseline is required.",
        },
        "gia": {
            "primary": ("Reasoning", "Word Meaning"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "Reasoning because the process has to be deduced from hedged "
                   "and conflicting narrative and then sequenced. Word Meaning "
                   "because the same finding must be built for a chief "
                   "executive and written for an engineer in the same sitting. "
                   "Number Speed because the sharpest defect in the pack is "
                   "arithmetic: a mean standing in for a distribution.",
            "proxies": (
                "Whether the records log is read as a distribution rather than "
                "an average.",
                "Whether the Slack thread is recognized as contradicting the "
                "chief operating officer rather than supplementing her.",
                "Whether the deck and the specification still describe the same "
                "system.",
                "How the 90 minutes were allocated across four written "
                "deliverables and a client-ready deck, which with no caps in "
                "place is a pure judgment call the candidate makes unaided.",
            ),
        },
        "reviewer": {
            "path": (
                "Triage (0:00).",
                "Resume and profile (1:00). Score background now, before the "
                "work product can color it.",
                "Task 1 (3:30). The ranking basis and the defect count separate "
                "the field faster than anything else here.",
                "The deck (6:30). Open it full screen at presentation size, not "
                "in a preview pane. Slide one, the outcome commitment, whether "
                "baseline capture is scheduled, and the ask on the last slide.",
                "Task 4 (9:00), checking it describes the same system and that "
                "a human gate exists.",
                "Task 2 (11:00), scanning for leak points and whether the "
                "questions move the plan.",
                "Part 1 at 1.5x (12:00), slowing to normal speed for the "
                "interruption at roughly minute two. Score the spike and "
                "presence here.",
                "Part 2 (15:00) for the enjoyed and did not enjoy answer and "
                "salary expectations. Then Task 5 and total.",
            ),
            "calibration": "Craft is a criterion in this version, which makes "
                           "the halo risk real. Score the triage before you "
                           "open the deck and do not revise it afterward. A "
                           "clean deck that adopts Priya's 20 percent, proposes "
                           "an AI records agent and never mentions the "
                           "attestation requirement is a 1 on both triage and "
                           "the spike, and the deck row alone will not save it. "
                           "Part 2 is worth actually watching: it is the only "
                           "unstructured thing in the pack, and the answer to "
                           "what they did not enjoy tells you whether they will "
                           "say an uncomfortable thing to a client.",
            "probes": (
                "\"Dana told you every order goes through the electronic health "
                "record. Rick says eight clinicians work around it, and they "
                "are your highest volume. You get one conversation with Dana "
                "about it. What do you say?\"",
                "\"Priya asks you to commit to the 20 percent in the room, "
                "today, in front of her board chair. What is your answer?\"",
                "\"Your first build ships and adoption is 30 percent after six "
                "weeks. Walk me through your next two weeks.\"",
                "\"Which item on your triage do you drop if the budget is "
                "halved, and what breaks?\"",
                "\"Walk me through something you built with AI end to end. What "
                "did you get wrong the first time?\" Use this on every "
                "advancing candidate, and carry anything on the resume you "
                "could not settle at the desk.",
                "Take whatever they said they did not enjoy in Part 2 and ask "
                "what they would have changed about it. You are testing whether "
                "the critique holds up under one follow-up question or "
                "evaporates.",
            ),
        },
        "notes": (
            "The six seeded defects, which are the answer key for Task 1. "
            "(1) The system of record is not the system of record: roughly "
            "eight of fifty-five clinicians document outside the EHR and batch "
            "their charts later, and they are the highest-volume clinicians, so "
            "every metric out of that system is partial and lagged. Recognizing "
            "that it makes other items unsizable until fixed, and that Dana's "
            "unwillingness to break what works for her top clinicians is the "
            "actual obstacle, is a 5. (2) The 4.2 hour average is a mean hiding "
            "a tail -- ten of twelve requests close in 5 to 13 minutes. Missing "
            "this and proposing a records build on the strength of the average "
            "caps triage at 1; naming the median explicitly is the cleanest "
            "single tell of a strong candidate in the whole pack. (3) The "
            "referral problem has no measurement: \"it happens\", with no "
            "number, and the first move is a report and an owner, not a model. "
            "(4) Clinical attestation caps the most attractive automation -- "
            "drafting and assembly are available, attesting is not -- and "
            "Alicia's unanswered question about which vendors hold a signed "
            "business associate agreement is a live blocker that belongs on a "
            "slide. (5) Two complaints, one root cause: duplicate entry across "
            "the EHR, a dozen payer portals and the tracking spreadsheet is one "
            "problem wearing three faces, and the spreadsheet survives only "
            "because of its notes column. (6) The chief executive's goal has no "
            "denominator, because nobody tracks clinician non-clinical time.",
            "Catching three or more of the six, including the records average "
            "or the system-of-record gap, is the practical floor for a 4 on "
            "triage. Catching five or six with the reasoning attached is a 5.",
            "What the seniority changes for grading, and it is exactly two "
            "things: the background row, and your tolerance for a shaky "
            "presentation. The work product bar does not move, because both "
            "postings sit the same assessment. At 4 to 7 years a candidate "
            "should already have stood in front of people who could say no, so "
            "a presentation that reads as a first attempt at executive delivery "
            "is a real signal here in a way it is not at associate tier, where "
            "13B says in as many words that nerves are not scored.",
            "Section 10, what the 40-point background weighting changes. With "
            "background at 1, a candidate who scores a perfect 5 on every other "
            "criterion tops out at 68 points, below the 75 advance bar. At "
            "background 2 the same perfect submission reaches 76, barely "
            "clearing. At background 3 it reaches 84. So a 1 on background will "
            "not advance regardless of how good the work is, and a 2 must be "
            "near-perfect everywhere else. That is the opposite of the pack's "
            "standing rule that background adds and never blocks; here it "
            "blocks, which for a client-facing New York seat that walks into "
            "rooms with chief executives is a defensible position. If it is not "
            "the intent, drop the row to 25 and put the freed 15 points back "
            "into triage and the deck.",
            "Three guardrails, given the weighting. The \"no information scores "
            "3\" rule is load-bearing: a missing resume link or an unreadable "
            "profile must never produce a 1, or a candidate is silently "
            "rejected for a file-sharing failure. The second look runs the "
            "other way round from the rest of the pack: any candidate who "
            "scores 4 or 5 on triage and the deck but lands below 75 because of "
            "the background row gets a human read before any reject -- it bites "
            "on career changers, operators moving out of industry, and strong "
            "builders whose consulting pedigree is thin. And score background "
            "before the work product, without revisiting it: at 40 points an "
            "impressive deck will pull an ambivalent background score upward if "
            "you grade in the other order, and that is halo, not evidence.",
            "Reconciliation with the candidate-facing weights (triage 25, "
            "diagram 15, deck 25, specification 20, communication 10, AI 5). "
            "Those weights describe the work the candidate submits and are "
            "correct as published: they sum across the 60 points of this grid "
            "that are not background, and rank order holds inside that 60. "
            "Candidates are told plainly on the cover that background is "
            "assessed separately, so nothing here is hidden from them.",
        ),
        "gaps": (
            "Both AI Strategist postings sit this same 90-minute assessment "
            "and this grid marks the senior one only -- background anchors at "
            "four to seven years, and a presentation that reads as a first "
            "attempt scored as a real signal. The associate posting is zero to "
            "three years and has had its own grid since 2026-08-22: "
            "`ai_strategy_associate`, 13B, selected by which posting the "
            "candidate applied to. What still needs a human is the crossover "
            "the source document names in its section 10 -- a new graduate who "
            "applies to the senior posting and submits genuinely strong work "
            "should be moved to 13B rather than rejected on these background "
            "anchors, and an applicant to the associate posting with six years "
            "behind them moved here. Nothing detects that automatically; the "
            "tier follows the posting, and a reviewer swaps it and notes the "
            "swap on the file.",
            "The assessment tests no account growth, no directing of forward "
            "deployed engineers, and no handling of a live client conflict, all "
            "of which the JD names as the seat's own work.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
    # -- 13B. AI Strategy, associate tier ----------------------------------
    #
    # The companion grid to 13A, and the second grid in the pack to open the
    # background block. Same 100-point architecture, same 40/40/6/7/7 split,
    # same decision bands, same six seeded defects, same answer key. Its own
    # source document is explicit that only two things differ, so only two
    # things do here:
    #
    #   * The background row is scored on raw material and self-direction
    #     rather than accomplishment, and a candidate with no full-time work
    #     history can reach a 5. That row is the whole reason this grid exists.
    #   * Polish is graded more gently. Diagnosis is not: triage, the build
    #     specification and every seeded defect carry 13A's weights and 13A's
    #     anchors, word for word where the bar is the same, because the thing
    #     this seat is hired for does not require experience to demonstrate.
    #
    # It claims the same slug as 13A, which no other pair of grids does. Both
    # postings sit the identical 90-minute exercise and the portal carries one
    # assignment for the pair, so the slug cannot separate them; `tier` does.
    # See `for_slug`, and `config.JOB_TIERS` for which posting is which.
    {
        "key": "ai_strategy_associate",
        "unit": "AI Strategy",
        "grid_name": "Grid B. AI Strategist, associate tier",
        "entity": "Ajaia",
        "slugs": ("ai-strategist",),
        "tier": "associate",
        "roles": ("AI Strategist (32DBC63865)",),
        "assessment": "Ajaia AI Strategist Assessment, 90 minutes including "
                      "the deck, plus one continuous 12 to 13 minute video: a "
                      "10-minute recorded C-suite presentation, then a 2 "
                      "to 3 minute candidate introduction",
        "location": "New York, on-site, full-time, 0 to 3 years, $100,000 to "
                    "$175,000 base",
        # Renamed from 13A's "Executive nerve and claim discipline", and the
        # anchors below drop the nerve half. Holding a room is a senior skill;
        # refusing a number you cannot support is not, so only the second half
        # of the senior spike survives into this grid.
        "spike": "Claim discipline",
        "block_points": {
            "work_product": 40,
            "background": 40,
            "ai_forwardness": 6,
            "communication": 7,
            "spike": 7,
        },
        # Repealed by this rubric's section 7, exactly as in 13A: no caps, a
        # missing task scores that criterion 1 and the rest grades normally,
        # and the only auto-fail is confirmed fraud.
        "universal_auto_fails": False,
        "seat": "Runs the audit and the analysis inside an engagement a Senior "
                "AI Strategist owns, and grows into owning one. From the JD: "
                "\"Run AI audits inside client operations,\" \"Decide what to "
                "fix first,\" \"Write the build spec your engineering partner "
                "works from,\" \"Present to the C-suite,\" and \"Stay on the "
                "number after go-live.\" Zero to three years, aimed at partner.",
        "core_skill": "Work out what is actually happening inside a business, "
                      "decide what changes and in what order, and say so to a "
                      "chief executive who wants a different answer -- with "
                      "less practice at the last part than the senior seat has.",
        # Identical to 13A's competency model, which the source document states
        # rather than implies: "The competency model is identical to the senior
        # rubric." Only the track record entry is reworded, because what it
        # reads for is raw material rather than accomplishment.
        "competencies": (
            {"label": "Diagnostic judgment",
             "asks": "Reconstructs how the organization really runs from "
                     "contradictory inputs, and says which version is being "
                     "worked from.",
             "anchor": "\"Map how the business runs, not how the org chart says "
                       "it runs\" (Tasks 1 and 2)"},
            {"label": "Prioritization on a stated basis",
             "asks": "Declares a ranking basis and applies it to the last item "
                     "as strictly as the first.",
             "anchor": "\"Decide what to fix first\" (Task 1)"},
            {"label": "Automation restraint",
             "asks": "Knows when the answer is a field, a template, or an "
                     "owner. Rarer in this pool than at senior level, and "
                     "scored just as firmly.",
             "anchor": "\"A required field, a template, or a named owner closes "
                       "more problems than a model does\" (Task 1)"},
            {"label": "Executive translation",
             "asks": "A deck a chief executive can decide from.",
             "anchor": "\"One recommendation, the tradeoffs stated, a decision "
                       "at the end of it\" (Task 3, the presentation)"},
            {"label": "Specification craft",
             "asks": "Converts a recommendation into something an engineer can "
                     "build and test.",
             "anchor": "\"the outcome, the scope, the systems and data, where a "
                       "human signs off, and how we know it is done\" (Task 4)"},
            {"label": "Outcome ownership",
             "asks": "Baselines before claiming, measures after shipping.",
             "anchor": "\"Get a baseline before anyone claims an improvement\" "
                       "(Tasks 1, 3, 4)"},
            {"label": "Raw material and self-direction",
             "asks": "Is this person unusually capable, and do they start "
                     "things without being told to. Predicting, not verifying: "
                     "there may be no track record to read.",
             "anchor": "Scored from the resume and the Workable profile"},
        ),
        "criteria": (
            # Unchanged from 13A, deliberately. "Did not move: triage, the
            # build specification, and every seeded defect. Those weights and
            # anchors are identical to the senior grid on purpose."
            {"key": "triage",
             "label": "Opportunity triage and prioritization (Task 1)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Basis stated before it is used and still applied at the "
                    "last item. Catches at least three of the six seeded "
                    "defects, including the records average or the "
                    "system-of-record gap. Tags are honest, with at least one "
                    "item correctly called a process fix rather than AI. "
                    "Something is explicitly deferred with its cost named. "
                    "Sizing is either computed or refused out loud with what is "
                    "missing.",
                 3: "Right opportunities found and workably ranked, but the "
                    "basis is asserted rather than applied, or one or two "
                    "defects are caught while the records average passes "
                    "unchallenged, or everything is tagged automation or AI.",
                 1: "The same four surface items listed with no ranking basis, "
                    "or an AI agent proposed for each complaint, or Marcus's "
                    "4.2 hour average used as the case for a records build.",
             },
             "note": "This bar does not move for juniors. A sharp twenty-two "
                     "year old catches the median problem as readily as an "
                     "engagement manager does, and the ones who do not are not "
                     "going to grow into it quickly -- it is the single most "
                     "predictive thing in the assessment at this tier. As at "
                     "senior, do not withhold a 5 for a different first "
                     "workflow than you would have picked: grade the basis and "
                     "whether it holds, never the pick."},

            {"key": "current_state",
             "label": "Current-state diagram and gaps (Task 2)",
             "block": "work_product", "weight": 6,
             "anchors": {
                 5: "A reader could follow it from trigger to close: actors, "
                    "systems, decision points, handoffs, and the specific leak "
                    "points marked. Inference is separated from what the "
                    "materials say. Questions target ownership, exceptions and "
                    "business rules, and each states what changes in the plan "
                    "depending on the answer.",
                 3: "A linear map with owners and systems but few branches or "
                    "leak points, or questions that are reasonable but do not "
                    "move the plan, or inference presented as fact.",
                 1: "A narrative retell with no step structure, or questions "
                    "already answered in the materials.",
             },
             "note": "Any format scores the same -- a text flow, Mermaid, "
                     "numbered steps with owner tags, a table or an image."},

            # Moved. 13A asks for an artifact you would carry into a real
            # client meeting; this asks only for one that is clean, legible and
            # sequenced. Every substance clause is 13A's; only the craft
            # clauses are relaxed.
            {"key": "ceo_deck", "label": "The CEO deck (Task 3)",
             "block": "work_product", "weight": 12,
             "anchors": {
                 5: "Recommendation on slide one. Findings compressed rather "
                    "than replayed. The 90-day sequence has a stated reason for "
                    "its order. Cost includes her people's time, not only "
                    "money. The committed outcome is a number with a "
                    "measurement method behind it, and baseline capture is "
                    "inside the first two weeks with the consequence of "
                    "skipping it named. The last slide asks her one real "
                    "decision. Clean, legible and sequenced. Plain is fine; "
                    "unfinished is not.",
                 3: "The content is there but the recommendation is buried "
                    "behind context, or the outcome is asserted without a "
                    "measurement method, or baseline is mentioned without being "
                    "scheduled, or nothing is asked of her. Or the substance "
                    "holds and the slides are cluttered and hard to read.",
                 1: "A findings readout rather than a recommendation, or "
                    "Priya's 20 percent adopted with no baseline anywhere, or "
                    "an artifact nobody could present from.",
             },
             "note": "Client-ready is not the bar here; presentable is. Do not "
                     "pay for polish and do not charge for a template -- this "
                     "posting attracts people trained to produce consulting "
                     "artifacts, and a tidy deck that adopts Priya's 20 "
                     "percent, proposes an AI records agent and never mentions "
                     "attestation is a 1 on triage and a 1 on the spike "
                     "whatever it looks like. An unfinished deck at 90 minutes "
                     "is still scored here: the time allocation is the judgment "
                     "being tested."},

            # Unchanged from 13A.
            {"key": "build_spec",
             "label": "Engineering build specification (Task 4)",
             "block": "work_product", "weight": 10,
             "anchors": {
                 5: "Describes the same system as the deck. Problem stated as "
                    "an outcome rather than a feature. Out of scope is "
                    "explicit. The clinician attestation gate, or another "
                    "defended human checkpoint, is designed in with a reason. "
                    "Acceptance criteria are testable by someone who was not in "
                    "the room. Two or three named failure modes each carry "
                    "something built to catch them. A kill condition is stated.",
                 3: "Most elements present but thin: acceptance criteria that "
                    "restate the feature, failure modes without detection, "
                    "human review asserted with no checkpoint, or a spec that "
                    "has quietly drifted from the deck.",
                 1: "A feature list with no scope boundary and no acceptance "
                    "criteria, or a fully automated medical necessity "
                    "determination, which the compliance note rules out.",
             },
             "note": "Read this immediately after the deck. Drift between the "
                     "two means the candidate is producing documents rather "
                     "than owning a problem, and that reads the same at zero "
                     "years as at seven."},

            # The row this grid exists for. Section 5 of the source document,
            # in full: four signals, anchors at 5 through 1, and five rules.
            #
            # It keeps 13A's key rather than taking one of its own, and the
            # label carries the difference instead. Both grids put the same
            # 40 points on the same block in the same position; what a tier
            # changes is the anchors, exactly as it does for the deck and the
            # delivery rows. A different key here would break the one thing
            # the shared role page needs -- a criterion column that lines up
            # for every candidate on the assignment, whichever tier marked
            # them.
            {"key": "track_record", "label": "Raw material and self-direction",
             "block": "background", "weight": 40,
             "anchors": {
                 5: "Exceptional raw material with clear self-direction. Strong "
                    "on two or more of the four signals, or overwhelming on "
                    "one. Typical shapes: a selective school plus a shipped AI "
                    "product with real users; two serious internships plus a "
                    "founded organization; a mediocre transcript alongside a "
                    "business they built that has customers; a top program plus "
                    "research and a competitive win. Also a 5 for any "
                    "background so distinctive you would take the call "
                    "regardless of what the rest of the file says.",
                 3: "Credible but undifferentiated, or unknown. A solid degree "
                    "and ordinary internships, nothing built, nothing founded, "
                    "nothing competitive. Also where a genuinely strong "
                    "candidate lands when the resume is thin on detail rather "
                    "than thin on substance. Background not stated anywhere "
                    "also scores 3, with a note. Never 1 for absence of "
                    "information.",
                 1: "Nothing connects and nothing suggests a fast ramp. Reserve "
                    "this. At 40 points it is close to an automatic reject, so "
                    "use it only when the file genuinely offers nothing.",
             },
             "note": "You are predicting, not verifying. A candidate for this "
                     "seat may have graduated eight weeks ago and never held a "
                     "full-time job; that is expected and it is not a "
                     "deficiency. Score across four signals, and strength in "
                     "any two -- or exceptional strength in one -- reaches a 5. "
                     "(A) Academic: a selective institution or program, a "
                     "rigorous quantitative or argumentative course of study, "
                     "honors, a thesis, a named scholarship, a research or "
                     "teaching assistantship, a demanding double major, or a "
                     "degree earned while working full-time or supporting a "
                     "family. (B) Work exposure: internships, co-ops, part-time "
                     "roles or up to three years full-time in consulting, "
                     "banking, private equity or venture capital, corporate "
                     "strategy, product, operations, or a startup where they "
                     "did whatever needed doing -- the nature of it, not the "
                     "duration. (C) Things they built: the strongest single "
                     "signal in this pool and the one most often missed by a "
                     "reviewer reading quickly. An AI tool, automation, agent "
                     "or app other people actually used; a freelance practice, "
                     "however small; a business they started, whether or not it "
                     "worked; open-source work with users; a product with "
                     "paying customers. Weight what shipped and got used over "
                     "what was demoed. (D) Agency and drive: founding or "
                     "leading a club, team, publication or organization with "
                     "real membership; winning or placing in something "
                     "competitive; working through school; an unusual "
                     "self-directed path; a public body of work. "
                     "This row is worth 40 points, so it has five anchors, not "
                     "three. 4 = clearly strong on one signal and credible on "
                     "another: a good school and a relevant internship with "
                     "nothing built, a serious builder from a school nobody has "
                     "heard of, a strong operator with two years in a startup "
                     "and no academic distinction. This should be a common "
                     "score for good candidates. 2 = little of any of the four: "
                     "a degree with no evidence of anything done alongside it, "
                     "no work exposure of consequence, nothing built, nothing "
                     "led. "
                     "Five rules. Do not run a pedigree screen -- a "
                     "state-school graduate who shipped an AI product people "
                     "use outranks an Ivy graduate with a clean transcript and "
                     "nothing built, and signal C beats signal A when they "
                     "conflict. Read the whole resume including the bottom: at "
                     "this tier the projects section, the personal site and the "
                     "GitHub link are usually where the real signal is, and "
                     "they are usually last, so open the links. A gap or an "
                     "unusual path is not a penalty; score what they did, not "
                     "the shape of the timeline. Do not screen on the degree "
                     "itself, which the JD lists as preferred and not required "
                     "-- no degree with signals B, C and D can score a 5. And "
                     "resolve genuine ambiguity upward and flag it for the "
                     "screen: the screen costs twenty minutes and a false "
                     "reject costs a hire."},

            # Unchanged from 13A but for the anchors' length; the judgment
            # being read is the same one.
            {"key": "ai_note",
             "label": "AI leverage with judgment (Task 5 and the work)",
             "block": "ai_forwardness", "weight": 6,
             "anchors": {
                 5: "Judgment rather than volume. Names one specific thing "
                    "checked, corrected or rejected during this sitting, or a "
                    "defended call about what stayed human. Tools tied to tasks.",
                 3: "Partial or informal: tools and tasks named without "
                    "verification, or work visibly AI-assisted and better for "
                    "it with no checking described.",
                 1: "No evidence of AI use anywhere in the submission.",
             },
             "note": "A missing Task 5 is a real miss but not an auto-fail. "
                     "Grade the row from whatever the work reveals, and score 1 "
                     "only when nothing anywhere shows AI use."},

            # Moved. 13A's row is "Presence, length judgment, and submission
            # craft" and scores presence; this one does not. "Nerves are not
            # scored" sits in the anchor rather than only the note, because it
            # is the clause a grader most needs in front of them while watching
            # a nervous video.
            {"key": "delivery",
             "label": "Clarity, length judgment, and submission mechanics",
             "block": "communication", "weight": 7,
             "anchors": {
                 5: "Talks to the room in Part 1 rather than reading the "
                    "slides, holds roughly to 10 minutes, and both screen and "
                    "speaker are visible and audible. A reader finds the "
                    "recommendation immediately and an engineer finds the "
                    "acceptance criteria immediately. Written work sectioned by "
                    "task. Deck submitted as a file. Both parts present "
                    "in what was submitted, Ajaia in the caption if "
                    "hosted on YouTube, ajaia.ai referenced, every link "
                    "opens. Nerves are not scored.",
                 3: "Delivery is stiff, rushed or over-rehearsed but the "
                    "argument still lands. Or the written work is bloated, or "
                    "so terse that a required element is missing. One or two "
                    "submission misses: a slide link instead of a file, no "
                    "ajaia.ai reference, salary buried.",
                 1: "Reads the slides aloud with no audience in mind, or a link "
                    "that does not open, or a video missing entirely, or the "
                    "written work is one undifferentiated block.",
             },
             "note": "Expect submissions that are visibly a first attempt at "
                     "professional work: a deck built in a template, a spec "
                     "written like a homework answer, a video recorded in a "
                     "dorm room. None of that is scored -- grade the thinking "
                     "underneath it. What is still scored is judgment about "
                     "length and care about mechanics, and a deck submitted as "
                     "a link deducts here and is never a reject."},

            # Moved in name and in framing. 13A scores holding the position
            # under pressure; this scores only the refusal itself, which is
            # what a junior can be expected to have.
            {"key": "claim_discipline",
             "label": "Refusing the number they cannot support",
             "block": "spike", "weight": 7,
             "anchors": {
                 5: "Declines to commit to a number the evidence does not "
                    "support, and puts that on a slide rather than in a "
                    "footnote. Names the attestation requirement as a cap on "
                    "the most attractive automation. Engages the \"why not just "
                    "buy a platform\" interruption with a reason rather than a "
                    "vendor comparison.",
                 3: "The limitation appears in the written work but the deck or "
                    "the presentation softens it, or the platform question is "
                    "answered with feature comparison, or the attestation "
                    "requirement is noted without being designed for.",
                 1: "Commits to the 20 percent with no baseline, or presents "
                    "everything as certain, or Part 1 never engages the "
                    "interruption.",
             },
             "note": "Steadiness is not in these anchors and holding a room is "
                     "not either; both are senior skills. What is here is the "
                     "refusal, and it is worth the same 7 points because a "
                     "junior candidate who tells the CEO what she wants to hear "
                     "will do the same thing to a real client in month two, "
                     "when it costs more. Priya interrupts around minute two of "
                     "Part 1; if nothing is on camera anywhere, score this "
                     "from the deck and the written work and carry the missing "
                     "recording to the screen as the first question."},
        ),
        "auto_fails": (
            "Confirmed fraud or misrepresentation, visible on the face of the "
            "submission: fake or duplicated identity, another person's work "
            "submitted as their own, a visibly different person across the two "
            "parts of the video, burner-domain or automated-apply patterns, "
            "or materials "
            "that hand the JD's own phrasing back as analysis. This routes to "
            "the fraud log, not to grading. Reviewers run no forensics.",
        ),
        "red_flags": (
            "Patient data proposed for a consumer AI tool, or no sign the "
            "candidate registered the compliance question about which vendors "
            "hold a signed business associate agreement. This one flag "
            "overrides everything else: inexperience is not an excuse, because "
            "the constraint is stated plainly in the materials. Score it down "
            "and probe hard at screen.",
            "An AI agent proposed for every complaint. Restraint outranks "
            "imagination and is rarer in junior candidates than in senior ones, "
            "so this pool produces more of these. Score them down as firmly as "
            "you would at senior level.",
            "A clean, confident 90-day plan in which nothing is uncertain and "
            "no baseline is required.",
            "Generic or off-scenario content: a submission that never names the "
            "records log, the tracking spreadsheet, the attestation requirement "
            "or the clinicians working outside the system scores 1 on the "
            "criteria it fails to address.",
        ),
        "do_not_penalize": (
            "Rawness. A deck built in a template, a spec written like a "
            "homework answer, a video recorded in a dorm room, and nerves on "
            "camera. None of it is scored. Grade the thinking underneath it.",
            "A missing task. Score that criterion 1, grade the rest normally, "
            "and note it as a screen question.",
            "A deck submitted as a link, or as a document rather than slides. "
            "Deduct inside communication and delivery and grade the content "
            "normally.",
            "A dead video link. Ask once; if it never opens, grade what is "
            "reachable and score the dependent criteria from the written work "
            "and the deck.",
            "A figure labelled as an assumption. The pack supplies no clinician "
            "time data, no cost per records request and no referral conversion "
            "rate, so assuming out loud is the right behaviour and costs "
            "nothing. Asserted as fact, it costs a point on the criterion it "
            "sits under and becomes a probe.",
            "Deck decoration, vocabulary, fluency and consulting-register "
            "writing, none of which is the thing being bought. This pool "
            "contains a lot of people who have been taught to sound like "
            "consultants and have not yet been taught to think like one.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, automated "
                                      "apply, JD phrasing returned, identity "
                                      "inconsistent between the "
                                      "presentation and the introduction."},
            {"key": "complete", "label": "Substantially complete: five tasks, a "
                                         "deck, and a video that plays and "
                                         "carries both parts -- one "
                                         "combined file or several, in the "
                                         "one place submitted."},
            {"key": "mechanics", "label": "Submission mechanics: deck attached "
                                          "as a file rather than a link, the "
                                          "submitted video link opens and "
                                          "everything in it plays, Ajaia in "
                                          "the "
                                          "caption if on YouTube, ajaia.ai "
                                          "reference present, Markdown "
                                          "sectioned by task."},
            {"key": "scenario", "label": "Engages the scenario by name: the "
                                         "records log, the tracking "
                                         "spreadsheet, the attestation "
                                         "requirement, or the clinicians "
                                         "working outside the system."},
            {"key": "number", "label": "One checkable claim: a median, a stated "
                                       "ranking basis, a baseline proposal, or "
                                       "a labelled assumption."},
            {"key": "ai_note", "label": "AI note present and specific."},
        ),
        "tells": {
            "strong": "The word \"median\", or any recomputation of the 4.2 "
                      "hour average.",
            "weak": "A confident 90-day plan in which nothing is uncertain.",
        },
        "gia": {
            "primary": ("Reasoning", "Word Meaning"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "Unchanged from 13A, and it matters more here: cognitive "
                   "ability paired with a work sample is the strongest "
                   "predictor pair available, and at zero to three years there "
                   "is less track record to read. If Ajaia ever adopts a formal "
                   "instrument, this is the role family to start with.",
            "proxies": (
                "Whether the records log is read as a distribution rather than "
                "an average.",
                "Whether the Slack thread is recognized as contradicting the "
                "chief operating officer rather than supplementing her.",
                "How the 90 minutes were allocated with no caps to guide them.",
            ),
        },
        "reviewer": {
            "path": (
                "Triage (0:00).",
                "Resume and profile (1:00), including the projects section and "
                "any links at the bottom. Open the links. Score background now, "
                "before the work product colors it.",
                "Task 1 (4:00). The ranking basis and the defect count separate "
                "this field faster than anything else.",
                "The deck (7:00). Slide one, the outcome commitment, whether "
                "baseline capture is scheduled, and the ask on the last slide.",
                "Task 4 (9:00), checking it describes the same system and that "
                "a human gate exists.",
                "Task 2 (10:30), briefly.",
                "Part 1 at 1.5x (11:30), slowing to normal speed for the "
                "interruption around minute two. Score the spike.",
                "Part 2 (14:00), then Task 5 and total.",
            ),
            "calibration": "Two failure modes will cost you good hires in this "
                           "pool. The first is paying for polish: a tidy deck "
                           "with a clean 90-day plan that adopts Priya's 20 "
                           "percent, proposes an AI records agent and never "
                           "mentions attestation is a 1 on triage and a 1 on "
                           "the spike, whatever it looks like. The second is "
                           "penalizing rawness: a candidate who catches the "
                           "median problem, refuses the 20 percent, designs the "
                           "clinician gate and presents it nervously off a "
                           "plain deck is exactly who you want. Nerves are not "
                           "scored, and neither is a template. One check to run "
                           "on yourself: once triage is scored, ask whether you "
                           "would have scored it the same coming from a "
                           "candidate with two internships at good firms. If "
                           "the answer is no, regrade the triage.",
            "probes": (
                "\"Dana told you every order goes through the electronic health "
                "record. Rick says eight clinicians work around it, and they "
                "are your highest volume. You get one conversation with Dana "
                "about it. What do you say?\"",
                "\"Priya asks you to commit to the 20 percent in the room, "
                "today, in front of her board chair. What is your answer?\"",
                "\"Walk me through something you built end to end. What did you "
                "get wrong the first time, and how did you find out?\" Ask this "
                "of every advancing candidate; at this tier it is the "
                "highest-yield question in the loop.",
                "\"Which item on your triage do you drop if the budget is "
                "halved, and what breaks?\"",
                "Take whatever they said they did not enjoy in Part 2 and ask "
                "what they would have changed. You are testing whether the "
                "critique survives one follow-up.",
            ),
        },
        "notes": (
            "The six seeded defects are identical to 13A's and the answer key "
            "does not change. (1) The system of record is not the system of "
            "record: eight of fifty-five clinicians document outside the EHR "
            "and they are the highest-volume ones, so every metric out of it is "
            "partial and lagged. (2) The 4.2 hour average is a mean hiding a "
            "tail -- ten of twelve requests close in 5 to 13 minutes -- and it "
            "is the single cleanest tell of a strong candidate in this pool; "
            "missing it and proposing a records build on the average caps "
            "triage at 1. (3) The referral problem has no measurement: no "
            "number anywhere, and nobody has connected inbound referrals to "
            "scheduled visits, so the first move is a report and an owner, not "
            "a model. (4) Clinical attestation caps the most attractive "
            "automation: a licensed clinician must sign anything constituting a "
            "clinical judgment, so drafting is available and attesting is not. "
            "(5) Two complaints, one root cause: duplicate entry across the "
            "record system, the payer portals and the tracking spreadsheet is "
            "one problem wearing three faces. (6) The chief executive's goal "
            "has no denominator, because nobody tracks clinician non-clinical "
            "time, so the 20 percent cannot be committed to yet.",
            "Catching three or more, including defect 1 or defect 2, is the "
            "floor for a 4 on triage. Five or six with reasoning attached is a "
            "5. What to expect in this pool: defects 2 and 6 are the ones "
            "strong junior candidates catch most often, because they are "
            "arithmetic and logic rather than pattern recognition from "
            "experience. Defects 1 and 4 are caught less often at this tier, "
            "since they reward having watched an organization work around its "
            "own rules. Do not treat a miss on 1 or 4 as harshly as a miss on "
            "2.",
            "What to look for, in priority order. (1) Do they catch that the "
            "numbers are lying. (2) Will they refuse to commit to a number they "
            "cannot support. (3) Do they know what is not an AI problem. (4) Do "
            "the deck and the specification describe the same system. (5) Is "
            "there evidence they have built something real -- read the "
            "background row and the AI note together, because someone who has "
            "shipped a thing other people used is telling you more about their "
            "next two years than their transcript is. (6) Presence and craft, "
            "graded gently.",
            "Section 10, what the 40-point background weighting changes. The "
            "arithmetic is 13A's and so is the consequence: with background at "
            "1 a candidate who scores a perfect 5 on every other criterion tops "
            "out at 68, below the 75 advance bar; at 2 the same submission "
            "reaches 76; at 3 it reaches 84. So a 1 will not advance regardless "
            "of the work, and a 2 must be near-perfect everywhere else. That is "
            "why the anchors say to reserve the 1, why absence of information "
            "scores 3 and never 1, and why ambiguity resolves upward.",
            "The second look, which fires more often here than at senior tier "
            "and is meant to. Any candidate who scores 4 or 5 on triage but "
            "lands below 75 because of the background row gets a human read "
            "before any reject. At zero to three years the strongest thinkers "
            "frequently have the thinnest files. Default action is a "
            "twenty-minute screen, not a rejection.",
            "Using this alongside 13A. Grade every candidate against the tier "
            "they applied to. Someone who applied to the associate posting with "
            "six years of consulting behind them moves to the senior grid, and "
            "you tell them you did. The reverse matters more: a new graduate "
            "who applies to the senior posting and produces a genuinely strong "
            "submission is graded here rather than rejected on the senior "
            "background anchors. Note the tier swap on the file either way. "
            "Nothing in the candidate-facing material differs by tier, which is "
            "deliberate -- it keeps the work sample comparable and means a "
            "strong associate submission and a strong senior submission can be "
            "read side by side.",
            "If you only remember one thing: the background row is the only "
            "place experience is supposed to matter. Scoring it first stops a "
            "polished deck inflating it, but that order carries its own risk in "
            "this pool, which is letting a thin resume quietly drag the triage "
            "score down because the candidate seems junior.",
        ),
        "gaps": (
            "Grade every candidate against the tier they applied to, and this "
            "grid is selected by which Workable posting they applied to -- see "
            "config.JOB_TIERS. A candidate whose posting cannot be resolved "
            "falls back to 13A, the senior grid, which is the stricter of the "
            "two on background; being wrong in that direction costs a second "
            "look rather than a false advance.",
            "The assessment tests no account growth, no directing of forward "
            "deployed engineers, and no handling of a live client conflict.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live -- and this is the role family the "
            "source document names as the place to start if one is ever "
            "adopted.",
        ),
    },
    # -- 14. Social Media and Marketing Intern -----------------------------
    #
    # The second grid in the pack to open the `background` block, and the only
    # one to open it at 10 rather than 40. That is a smaller departure than the
    # AI Strategist pair's and it is made for the opposite reason: not because
    # the record decides the seat, but because in an intern pool the record is
    # usually thin, sometimes absent, and would otherwise decide the seat by
    # accident. Scored inside the grid it is capped at 10 points, it adds, and
    # `config.CV_WEIGHT_BY_SEAT` pins this seat's external CV weight to 0.0 so
    # the portfolio is not also paid for a second time in the blend -- the
    # pairing BLOCKS' `background` note requires of any grid that opens it.
    #
    # The other departure is `universal_auto_fails: False`. Three of the four
    # universal rules are cap-and-completeness rules, and this rubric turns
    # exactly those into scored rows: format and scope compliance is worth 5
    # points, a written piece under 250 words scores 1 rather than ending the
    # grading, and the source says in as many words to "Grade what is in front
    # of you" and never to judge whether a candidate went over time. Leaving
    # the universal list on would have the grader close candidacies on rules
    # this rubric repeals. FRAUD_TELLS is separate and still reaches every
    # grader.
    {
        "key": "social_marketing_intern",
        "unit": "Social Media and Marketing Intern",
        "grid_name": "Social Media and Marketing Intern",
        "entity": "Ajaia",
        "slugs": ("social-marketing-intern",),
        "roles": ("Social Media and Marketing Intern (9AB42204CE)",),
        "assessment": "Build Ajaia a Week of Content, 120 minutes, timed, hard "
                      "stop",
        "location": "Remote, internship, $20 to $40 hourly",
        "spike": "Can they say why it works",
        # 55 / 10 / 10 / 13 / 12. Stated in full rather than as a diff, because
        # a reader who sees only "background: 10" cannot tell what paid for it:
        # work product gives up 15 of the pack's 70, and communication and the
        # spike buy back 3 and 2 on top of their usual 10.
        "block_points": {
            "work_product": 55,
            "background": 10,
            "ai_forwardness": 10,
            "communication": 13,
            "spike": 12,
        },
        "universal_auto_fails": False,
        "seat": "The seat makes Ajaia's content: short-form video, an Instagram "
                "presence, and writing with a point of view in it. The "
                "assignment is the job in miniature -- \"This is the actual "
                "work\" -- and it is scoped as an internship, hourly, at 20 to "
                "40 dollars against the rubric score and the working session.",
        "core_skill": "Fast, defensible choices, explained. \"An unfinished "
                      "piece with a clear reason behind every decision beats a "
                      "finished piece with none.\"",
        "competencies": (
            {"label": "Short-form video judgment",
             "asks": "Cuts for the platform named, and for the first three "
                     "seconds.",
             "anchor": "\"Pick the platform first... cut for that platform's "
                       "format and behavior\" (Task 1, weighted heaviest with "
                       "Task 3)"},
            {"label": "Visual system",
             "asks": "Three assets that look like one brand, not three files.",
             "anchor": "\"Build a look that could plausibly be ours and hold it "
                       "across all the assets\" (Task 2)"},
            {"label": "A position, argued",
             "asks": "A view someone could disagree with, and support under it.",
             "anchor": "\"Take a position someone could disagree with... If we "
                       "can predict your third sentence from your first, it is "
                       "not working\" (Task 3)"},
            {"label": "Explaining the choice",
             "asks": "Reasons stated in terms of the viewer, not the tool.",
             "anchor": "\"We will ask you what you used, what it got right, and "
                       "what you threw away\" (Task 4 and the AI Workflow "
                       "Note)"},
        ),
        "criteria": (
            {"key": "video_edit", "label": "Video edit (Task 1)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Opening earns the next three seconds. Pacing has a reason. "
                    "Captions and on-screen text are legible and serve the "
                    "point. Inside 30 to 60 seconds and cut for the platform "
                    "they named. The five bullets show real decisions, "
                    "including something they cut.",
                 3: "Watchable, in spec, but the hook is slow or the text is "
                    "unfinished. Bullets restate the video instead of "
                    "explaining it.",
                 1: "Source lightly trimmed, no captions, wrong aspect ratio, "
                    "or bullets with no decisions in them.",
             }},
            {"key": "instagram_set", "label": "Instagram set (Task 2)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "A visual system, not three unrelated files. Type, color "
                    "and spacing hold across carousel, story and caption. "
                    "Slide one gives a reason to swipe. Caption is written for "
                    "Instagram, not repurposed from LinkedIn.",
                 3: "Clean and on-format, but generic template output, or the "
                    "story frame does not match the carousel.",
                 1: "Off-spec dimensions, illegible type, or a caption that is "
                    "the carousel text pasted in.",
             }},
            {"key": "written_piece", "label": "Written piece (Task 3)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "A real position, argued, that an enterprise buyer who has "
                    "already sat through a failed AI pilot would stop for. "
                    "Specific, with something concrete behind each claim. "
                    "Sustains itself at whatever length it runs, and the last "
                    "paragraph is as strong as the first.",
                 3: "Competent and readable, but the position is safe and the "
                    "examples are generic. Or a good argument padded past its "
                    "natural end.",
                 1: "Definitional summary of AI, hype language, invented "
                    "statistics, or under 250 words.",
             }},
            {"key": "walkthrough", "label": "Walkthrough video (Task 4)",
             "block": "communication", "weight": 8,
             "anchors": {
                 5: "Answers all five prompts, in 3 to 5 minutes, and names a "
                    "decision they are genuinely unsure about instead of "
                    "defending everything. Talks about the work, not about "
                    "themselves.",
                 3: "Covers most prompts, but skips the uncertainty question or "
                    "the rate question, or runs long.",
                 1: "Reads a script, misses several prompts, or never explains "
                    "a single choice.",
             }},
            {"key": "compliance", "label": "Format and scope compliance",
             "block": "communication", "weight": 5,
             # A scored row rather than an auto-fail, which is the whole reason
             # this grid switches the universal list off. Missing pieces cost
             # 5 points here at most; they do not end the grading.
             "note": "Check every link before scoring this row, and ask once "
                     "for a working link before scoring it down.",
             "anchors": {
                 5: "Everything delivered, every link opens, every cap "
                    "respected.",
                 3: "One cap missed or one link needs chasing.",
                 1: "Multiple pieces missing or nothing opens.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names specific tools and specific uses, and names something "
                    "AI produced that they rejected, with a reason. Shows AI as "
                    "speed with judgment on top.",
                 3: "Lists tools and uses, but everything AI gave them was "
                    "apparently fine.",
                 1: "No note, or \"I used ChatGPT for ideas,\" or heavy visible "
                    "AI output with no evidence of a filter.",
             }},
            {"key": "why_it_works", "label": "Can they say why it works",
             "block": "spike", "weight": 12,
             "anchors": {
                 5: "Across the submission, choices are explained in terms of "
                    "the viewer: what stops the scroll, what a specific "
                    "audience already believes, why this format for this idea. "
                    "They reference work they admire or a format they are "
                    "borrowing, deliberately.",
                 3: "Explanations exist but stay at the level of \"it looks "
                    "clean\" or \"this performs well.\"",
                 1: "No reasoning offered anywhere, or reasoning that "
                    "contradicts what they actually made.",
             }},
            {"key": "prior_work",
             "label": "Evidence they have done this before",
             "block": "background", "weight": 10,
             # The neutral-3 rule, on the row it governs, because this is the
             # row a grader is reading when the temptation to mark an empty
             # field as a 1 arises. It is stated again in `notes` with the
             # arithmetic behind it.
             "note": "Adds, never blocks. An empty field scores 3, never 1. "
                     "The 1 is reserved for claims that are contradicted or "
                     "unverifiable, not for absence.",
             "anchors": {
                 5: "Real work you can open and evaluate: an account they grew "
                    "with numbers attached, videos with view counts, a design "
                    "or writing body of work, a brand or client they made "
                    "content for. Quality holds up on its own and the range "
                    "matches the seat.",
                 3: "Some relevant evidence: coursework, a club or campus "
                    "account, freelance one-offs, a personal account with a "
                    "consistent style. Also score 3 when there is nothing to "
                    "read at all.",
                 1: "Links provided but they do not open, or the shown work "
                    "directly contradicts the assessment quality. Score 1 only "
                    "for contradicted or unverifiable claims, never for an "
                    "empty field.",
             }},
        ),
        # Nothing here ends the grading except fraud, which is this rubric's
        # own position rather than a leniency this file added. See the header
        # comment above and `do_not_penalize` below.
        "auto_fails": (
            "Confirmed fraud or misrepresentation, visible on the face of the "
            "submission: another person's work submitted as their own, a "
            "visibly different person across the two videos, burner-domain or "
            "automated-apply patterns, or materials that hand the posting's "
            "own phrasing back as content. This routes to the fraud log, not "
            "to grading.",
        ),
        "red_flags": (
            "Invented clients, metrics, testimonials or results. The "
            "assignment restricts the candidate to publicly available material "
            "from ajaia.ai and says so; a fabricated number is a red flag on "
            "the row it sits under, and a probe.",
            "Polish with nothing under it. \"A candidate with After Effects "
            "skills and nothing to say scores lower than a candidate with a "
            "rough CapCut cut and a sharp read on why the hook works.\"",
            "A beautiful portfolio against a thin submission. The submission "
            "is the evidence; the portfolio adds at most 10 points.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: posting phrasing "
                                      "parroted back, block-capital "
                                      "salutations, burner domains."},
            {"key": "complete", "label": "Complete: video plus five bullets, "
                                         "carousel, story frame, caption, "
                                         "written piece, walkthrough, AI "
                                         "note."},
            {"key": "opens", "label": "Every link opens. Ask once before "
                                      "counting this against them."},
            {"key": "platform", "label": "Names the platform the video was cut "
                                         "for, and the cut matches it."},
            {"key": "position", "label": "The written piece takes a position "
                                         "someone could disagree with, and "
                                         "clears 250 words."},
            {"key": "rejection", "label": "The AI note names something AI "
                                          "produced that they threw away."},
        ),
        "tells": {
            "strong": "Something they cut, named, with the reason they cut it.",
            "weak": "A carousel and a story frame that share no type, colour "
                    "or spacing.",
        },
        "do_not_penalize": (
            "A re-recorded video. Reviewers run no forensics, and the "
            "assignment never asked for one take.",
            "Prose that reads as AI-written. AI use is expected and is a point "
            "in the candidate's favour; what is graded is the AI Workflow Note "
            "and whether the output shows a filter on top, not whether a model "
            "was involved.",
            "Going over the 120 minutes. \"Grade what is in front of you.\" "
            "The timer is the candidate's problem, not a criterion.",
            "Rough or unfinished work. \"Rough is fine. Unfinished is fine. "
            "Unexplained is not.\" An unfinished piece with a reason behind "
            "every decision outscores a finished one with none.",
            "An empty optional prior-work section. It is optional, it adds, "
            "and the background row scores 3 when there is nothing to read.",
            "A dead link, on first sight. Ask once for a working one; grade "
            "what is reachable in the meantime and score `compliance` and "
            "`prior_work` only after the ask has gone unanswered.",
        ),
        "gia": {
            "primary": ("Word Meaning", "Perceptual Speed"),
            "secondary": (),
            "why": "Word Meaning fits a 250-word position with an argument "
                   "under it and a caption written for one platform; "
                   "Perceptual Speed fits caption timing, aspect ratios and "
                   "holding a visual system across three assets. Ajaia "
                   "administers no instrument, so only the proxies below are "
                   "live. At this seniority the GIA layer's caution rule "
                   "matters more than its growth signal: taste in an intern "
                   "pool is not a cognitive score.",
            "proxies": (
                "Which of the four pieces got the time, and whether that was "
                "the right call.",
                "What they cut, and whether they can say why.",
                "Whether the hook is stated as a claim about the viewer.",
            ),
        },
        "reviewer": {
            "path": (
                "Walkthrough first. \"This is the piece we watch first. It is "
                "where taste is easiest to hear\" (4 min).",
                "Task 1 video cold, judging the first three seconds and the "
                "captions, then the five bullets (4).",
                "Instagram set at full size, carousel and story side by side "
                "(3).",
                "Written piece, whole (4).",
                "AI Workflow Note (1).",
                "Optional prior work, links opened (2).",
            ),
            "calibration": "The failure mode in an intern pool is rewarding "
                           "polish and pedigree. A candidate with no portfolio "
                           "and an excellent submission is a hire; a candidate "
                           "with a beautiful portfolio and a thin submission "
                           "is not. Taste is trainable slower than tools are.",
            "probes": (
                "\"Show us an account you follow that you think is doing this "
                "badly, and tell us what you would change.\"",
                "\"Which of your four pieces would you delete, and what would "
                "you make instead?\"",
                "\"Walk us through the last thing you posted anywhere that did "
                "better than you expected. Why do you think it did?\"",
            ),
        },
        "notes": (
            "The loop this grid feeds. First read, 10 minutes async: screening "
            "question 5 and any links given, and no portfolio is not a "
            "rejection at this stage -- everyone who clears the tool and "
            "availability questions goes to the assessment. Assessment, 120 "
            "minutes, scored here, advance at 75. Screen, 20 minutes with "
            "Jordan or the content lead: the three probes above plus schedule, "
            "location and rate. Live working session, 45 minutes: a raw asset, "
            "25 minutes to produce one post with AI, 20 minutes talking "
            "through it -- the check on whether take-home quality holds under "
            "observation, which is the standing risk with any content "
            "take-home. Then the offer, hourly, inside 20 to 40 against the "
            "rubric score and the working session; demonstrated prior work and "
            "a strong background row are the main reasons to go above the "
            "midpoint.",
            "What the 10-point background weighting changes, with the "
            "arithmetic. A candidate who scores 3 on `prior_work` and a "
            "perfect 5 everywhere else lands at 94, so an empty portfolio "
            "costs 4 points and nothing else; at 1 the same submission still "
            "reaches 86. Work product alone is 55, and a candidate can clear "
            "75 on work product, the spike and the AI note without a portfolio "
            "at all. That is what \"adds, never blocks\" means here, and it is "
            "why the row's own note reserves the 1 for claims that are "
            "contradicted rather than absent. The same neutral-3 rule applies "
            "to any other row where the signal is simply unavailable.",
            "Portfolio verification, which is an operational flag rather than "
            "a scoring rule. Three of the top seven candidates in the analyst "
            "pool had resume or portfolio links Ajaia could not open, one "
            "already in the owner's trash. Check every link before scoring "
            "`prior_work` or `compliance`, and ask once for a working link "
            "before scoring either down.",
            "Use 4 and 2 often. A 5 is clearly strong and advanceable, not "
            "perfect; a 3 is solid; a 1 is missing or off target. A grid this "
            "small rounds hard -- there are only eight rows -- so a reviewer "
            "who marks everything 3 or 5 is compressing the pool rather than "
            "ranking it.",
            "Read the background row last, not first, which is the opposite of "
            "the instruction on the AI Strategist pair. There the row is worth "
            "40 and reading it first stops a polished deck inflating it. Here "
            "it is worth 10 and the risk runs the other way: reading a thin "
            "file first is what makes a grader mark a rough CapCut cut down "
            "for being rough. Grade the work, then open the links.",
        ),
        "gaps": (
            "The assessment tests no posting cadence, no community management, "
            "no analytics read and no email or newsletter work, all of which a "
            "content seat eventually owns. The live working session is where "
            "the first of those gets sampled.",
            "There is no numbers task. This rubric asks whether a candidate "
            "can say why something works, not whether they can read what did "
            "-- the third probe is the only place performance literacy is "
            "examined at all. Contrast grid 12B, which spends 16 points on a "
            "KPI plan for the seat one level up.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
    # -- 15. General Management and Growth (CV only) -----------------------
    #
    # The first grid in the pack with no assessment behind it.
    #
    # Ethos Intelligence's General Manager & Head of Growth posting runs no
    # work sample. There is nothing to mark but the record, so the CV is not
    # the background to this decision the way it is everywhere else -- it is
    # the decision. That one fact explains every unusual thing below.
    #
    # `block_points` puts all 100 in `background`, which is what routes every
    # row's quote to the CV instead of to an answer that does not exist; see
    # the corpus split in evaluator._parse_verdict. The universal auto-fails
    # are off because they police word caps, required sections and supplied
    # data, and a resume has none of those. `config.CV_WEIGHT_BY_SEAT` pins
    # this seat to 0.0 for the same reason grid 13 is pinned there: the record
    # is already paid for inside the 100, and the blend would charge for it a
    # second time.
    #
    # Marked by cv_evaluator.evaluate_cv rather than evaluator.evaluate. The
    # rows, weights, bands, triage routes and verdict shape are all the pack's,
    # so a card from this seat still reads against a card from any other.
    #
    # Written from the posting of 2026-08-20 (shortcode EA7059EA8E). The
    # weights follow the JD's own emphasis: "turn an emerging product and
    # strong market thesis into a repeatable growth engine" is the sentence the
    # seat is built around, so that row is the heaviest, and the edtech
    # background the posting files under "Strongly Preferred" is priced as a
    # preference at 10 rather than as the requirement it is often taken for.
    {
        "key": "gm_growth",
        "unit": "General Management and Growth",
        "grid_name": "Grid 15. General Manager & Head of Growth (CV only)",
        "entity": "Ethos Intelligence",
        "slugs": ("gm-head-of-growth",),
        "roles": ("General Manager & Head of Growth (EA7059EA8E)",),
        "assessment": "None. This seat runs no work sample, so the CV is the "
                      "whole submission and the whole 100 marks it.",
        "location": "Hybrid, New York. Regular travel to RDI/CSUSA corporate "
                    "offices and school sites, later to accounts and "
                    "conferences.",
        "spike": "A repeatable growth engine, built once already",
        # All of it in background. Stated in full, as block_points requires,
        # so a reader can see that nothing else is marked rather than infer it.
        "block_points": {
            "work_product": 0,
            "background": 100,
            "ai_forwardness": 0,
            "communication": 0,
            "spike": 0,
        },
        # Repealed rather than tolerated. Every universal rule is about an
        # assessment: a length cap, a required section, data supplied with the
        # task, an AI process disclosure. A CV is subject to none of them, and
        # a grader handed that list would end candidacies on rules that do not
        # exist here. FRAUD_TELLS is separate and still reaches this grader.
        "universal_auto_fails": False,
        "seat": "The seat owns Ethos's commercial and operating plan and its "
                "next stage of growth: \"go-to-market execution, customer "
                "acquisition, implementation, customer success, marketing, "
                "growth strategy, and company operating cadence.\" The company "
                "is live in early school deployments and is moving \"from "
                "founder-led product development and pilot relationships "
                "toward repeatable customer acquisition, implementation, "
                "adoption, and growth.\" The founders keep the mission, "
                "product vision and major strategic direction; this leader has "
                "\"clear decision authority over agreed commercial priorities, "
                "budgets, team design, and execution\" and is expected to "
                "\"help the company mature from founder-led execution into a "
                "durable operating system.\"",
        "core_skill": "Turning an emerging product and a market thesis into a "
                      "repeatable growth engine, and installing the operating "
                      "system that keeps it running once the founders step "
                      "back from it.",
        "competencies": (
            {"label": "Repeatable growth engine",
             "asks": "Has already taken a company from founder-led selling to "
                     "a motion that runs without the founder.",
             "anchor": "\"Track record of building or scaling a repeatable "
                       "growth engine in an early-stage or growth-stage "
                       "company\" (Requirements, Experience)"},
            {"label": "General management scope",
             "asks": "Owned a number, a budget and the team design under it.",
             "anchor": "\"has likely served as a GM, Head of Growth, "
                       "President, or senior business leader\" (Requirements)"},
            {"label": "Commercial breadth",
             "asks": "Ran more than one commercial function, not one deeply.",
             "anchor": "\"Experience leading across sales, customer success, "
                       "implementation, and marketing\" (Requirements)"},
            {"label": "SaaS and stage fit",
             "asks": "Software, at the part of the curve this company is on.",
             "anchor": "\"ideal for a SaaS operator\" (Description); \"in an "
                       "early-stage or scaling software company\""},
            {"label": "Trust-heavy market fit",
             "asks": "Has sold where a third party's safety or oversight "
                     "governed the sale.",
             "anchor": "\"ideally in edtech or another trust-heavy, "
                       "stakeholder-heavy environment\" (Requirements); filed "
                       "under \"Strongly Preferred\""},
            {"label": "Operating system",
             "asks": "Built the cadence and the sales machinery, not just ran "
                     "inside somebody else's.",
             "anchor": "\"Stand up and manage the sales operating system, "
                       "including pipeline management, forecasting, CRM "
                       "discipline, and sales process\" (Sales & Market "
                       "Development)"},
            {"label": "Team building",
             "asks": "Hired and kept commercial people, and aligned functions "
                     "that do not report to each other.",
             "anchor": "\"Demonstrated ability to hire, manage, and align "
                       "cross-functional teams\" (Requirements)"},
        ),
        "criteria": (
            {"key": "growth_engine",
             "label": "Repeatable growth engine, built or scaled",
             "block": "background", "weight": 25,
             "anchors": {
                 5: "Took a software company out of founder-led selling or "
                    "pilots and into a motion that repeated, and the CV shows "
                    "it moved: channels stood up by name, a funnel with "
                    "conversion or cycle figures, revenue or account growth "
                    "over a stated period. The engine was theirs to build, not "
                    "a channel inside one somebody else had already built. An "
                    "adoption-led or product-led motion, rather than "
                    "contract-led enterprise selling alone, reads 5 here on "
                    "its own -- it is what a school-facing product needs.",
                 3: "Ran growth inside a machine that already existed, or "
                    "built one channel well -- paid, outbound, partnerships, "
                    "content -- with nothing showing the whole motion was "
                    "theirs. Also where the right work lands when no number is "
                    "attached to any of it. This is the commonest mark on this "
                    "row and the correct one for a CV that describes the seat "
                    "without evidencing it.",
                 1: "No acquisition ownership anywhere. Delivery, account "
                    "management or marketing execution with nothing said about "
                    "how customers were won, or a career carrying quota inside "
                    "a mature sales organisation.",
             },
             "note": "The heaviest row in the grid, so 4 and 2 are live marks "
                     "and not roundings. 4 = built the motion, but the numbers "
                     "are thin or the stage was later than founder-led. 2 = "
                     "one channel, no ownership of the whole. Discount growth "
                     "claimed at a company whose growth plainly came from a "
                     "funding round or a brand the candidate did not build; "
                     "the question this row asks is what THEY moved. Score the "
                     "shape of the record, not the size of the logo."},

            {"key": "gm_scope",
             "label": "General management scope and accountability",
             "block": "background", "weight": 20,
             "anchors": {
                 5: "Has held GM, Head of Growth, President, VP of Revenue or "
                    "equivalent, owning a number with the budget, the team "
                    "design and the operating plan underneath it, reporting to "
                    "founders, a chief executive or a board. The decisions "
                    "were theirs to make. Experience positioning a company for "
                    "strategic investment, partnership or acquisition reads "
                    "here too.",
                 3: "Senior functional leadership -- VP Sales, VP Marketing, "
                    "Head of Customer Success -- carrying a number but not the "
                    "operating plan around it. Or genuine GM scope at a scale "
                    "well below this seat.",
                 1: "Individual contributor, or management with no commercial "
                    "accountability attached to it.",
             },
             "note": "20 points, so 4 and 2 count. 4 = the scope with one "
                     "piece missing, most often budget or team design. 2 = a "
                     "commercial title with no evidence of authority under it. "
                     "Title inflation is heavy in this pool: read scope, "
                     "budget, headcount and who they reported to, never the "
                     "words on the line. A founder is scored on what the "
                     "business actually did, not on the title they gave "
                     "themselves."},

            {"key": "commercial_breadth",
             "label": "Breadth across sales, success, implementation and "
                      "marketing",
             "block": "background", "weight": 15,
             "anchors": {
                 5: "Has led at least three of the four -- sales, customer "
                    "success, implementation, marketing -- together or in "
                    "sequence, with something evidencing each rather than one "
                    "title that spans them all.",
                 3: "Two of the four evidenced. Also where a title claiming "
                    "all four lands when only one of them shows up in what the "
                    "candidate actually did.",
                 1: "One function throughout, and nothing in the record "
                    "suggesting exposure to the others.",
             },
             "note": "The JD asks for all four and this row prices three, "
                     "deliberately: an operator who has run sales, success and "
                     "implementation and hired a marketer is the profile, not "
                     "a rarity who has personally owned every function. "
                     "Implementation counts when the CV shows onboarding or "
                     "deployment owned, whatever it was called there."},

            {"key": "saas_stage_fit",
             "label": "SaaS and stage fit",
             "block": "background", "weight": 15,
             "anchors": {
                 5: "Software as a service, at early or growth stage, through "
                    "the part of the curve this seat sits on: past pilots, not "
                    "yet repeatable. Comfort with founders and with ambiguity "
                    "visible in the moves themselves rather than asserted in a "
                    "summary line.",
                 3: "SaaS, but only at a scale where the systems were already "
                    "built. Or the right stage in a business that is not "
                    "software -- agency, services, hardware, retail -- with an "
                    "obvious bridge into this one.",
                 1: "Neither. A large-enterprise or non-software career "
                    "throughout, with nothing that transfers to a company "
                    "still finding its motion.",
             }},

            {"key": "market_fit",
             "label": "Edtech and trust-heavy market fit",
             "block": "background", "weight": 10,
             "anchors": {
                 5: "Sold into or ran a business serving K-12, schools, "
                    "districts, charter networks, private school systems or "
                    "education organisations. Or a market where trust, safety, "
                    "compliance or public scrutiny governed the sale: health, "
                    "public sector, financial services, products used by "
                    "children.",
                 3: "Adjacent. Higher education, corporate learning, HR tech, "
                    "or any enterprise sale with several stakeholders who each "
                    "had to be satisfied separately.",
                 1: "Consumer or transactional B2B only, with no exposure to a "
                    "sale where somebody else's safety or oversight was at "
                    "stake.",
             },
             "note": "The posting files this under Strongly Preferred, not "
                     "under Experience, and 10 points is what that is worth. "
                     "It should separate two otherwise equal candidates; it "
                     "must not sink a strong operator who has never sold to a "
                     "school. Never mark it 1 for silence -- a 1 means the "
                     "record positively shows a career that does not touch "
                     "this, not that the CV failed to mention it."},

            {"key": "operating_system",
             "label": "Operating cadence and sales system",
             "block": "background", "weight": 10,
             "anchors": {
                 5: "Built the machinery rather than ran inside it: a business "
                    "review cadence they installed, forecasting and pipeline "
                    "discipline, a CRM adopted and actually used, pricing or "
                    "packaging they set, growth KPIs they defined.",
                 3: "Ran a cadence somebody else built, or owned one piece of "
                    "it -- a CRM migration, a pricing change -- with no "
                    "picture of the rest.",
                 1: "Nothing on operating rhythm, forecasting, pricing or "
                    "sales process anywhere in the record.",
             },
             "note": "This is where \"help the company mature from founder-led "
                     "execution into a durable operating system\" is scored. "
                     "A CV rarely leads with it and a real operator's often "
                     "mentions it in passing -- a forecast accuracy figure, a "
                     "named CRM, a QBR, a pricing tier they introduced. Read "
                     "the whole document for it before marking low."},

            {"key": "team_building",
             "label": "Hiring and cross-functional leadership",
             "block": "background", "weight": 5,
             "anchors": {
                 5: "Recruited and developed commercial people -- roles named, "
                    "a team grown from a stated size, evidence they stayed -- "
                    "and aligned functions that did not report to them.",
                 3: "Managed a team they inherited, or hired inside one "
                    "function.",
                 1: "No people leadership in the record.",
             }},
        ),
        # Two lines, and short on purpose. An auto-fail ends a candidacy, and
        # on a CV-only seat there is no missing deliverable, no breached cap
        # and no ignored scenario to end one with. Weak experience is a low
        # score on the rows above, which is what those rows are for.
        "auto_fails": (
            "The document is not a CV: a cover letter with no employment "
            "history, a portfolio index, or a page that lists nothing the "
            "candidate did anywhere.",
            "No commercial, growth or general management work anywhere in the "
            "record -- a CV for a different profession entirely.",
        ),
        "red_flags": (
            "A title that outruns the scope beneath it: \"Head of Growth\" at "
            "a company of four, with no number attached to the tenure.",
            "Growth claimed at a company whose growth came from a funding "
            "round, a parent brand or a market moment the candidate did not "
            "build.",
            "A skills matrix standing in for a record -- \"GTM strategy, PLG, "
            "ARR growth\" as a list, with no employer where any of it "
            "happened.",
            "Advisory or fractional work written in the language of ownership. "
            "Advising on a growth plan is not carrying the number.",
            "Consecutive commercial leadership tenures under a year. A "
            "question for interview, never a deduction on its own.",
        ),
        "do_not_penalize": (
            "Extraction noise. This text is machine-read out of a PDF or DOCX, "
            "so broken columns, merged lines, lost bullets and stray "
            "characters are ours and not the candidate's.",
            "A short CV. Senior operators routinely run to one page, and "
            "length is not evidence of anything.",
            "Date gaps, which bear on nothing this rubric asks.",
            "No degree. The posting names none, and neither does this grid.",
            "A career changer whose most relevant work is the most recent. "
            "Score the shape of the record, not arithmetic on years.",
        ),
        "triage": (
            {"key": "saas", "label": "SaaS: a software company appears in the "
                                     "record, not services, agency or "
                                     "non-software work alone."},
            {"key": "senior", "label": "Seniority: has held GM, Head of "
                                       "Growth, VP+ commercial or founder "
                                       "scope, not IC or single-team "
                                       "management."},
            {"key": "growth_owner", "label": "Growth ownership: owned an "
                                             "acquisition, revenue or growth "
                                             "number, not only delivery or "
                                             "account management."},
            {"key": "multi_function", "label": "Breadth: led at least two of "
                                               "sales, customer success, "
                                               "implementation, marketing."},
            {"key": "early_stage", "label": "Stage: worked at an early or "
                                            "growth-stage company, not "
                                            "exclusively at enterprise "
                                            "scale."},
            {"key": "numbers", "label": "One checkable number: ARR, growth "
                                        "rate, pipeline, retention, headcount "
                                        "or budget, attached to something the "
                                        "candidate did."},
        ),
        "tells": {
            "strong": "A motion described end to end with figures on it, at a "
                      "company that was not already winning.",
            "weak": "Responsibilities listed in the JD's own vocabulary, with "
                    "no outcome under any of them.",
        },
        "gia": {
            "primary": ("Reasoning",),
            "secondary": ("Word Meaning",),
            "why": "The seat decides what to do next on incomplete "
                   "information and then has to make other people act on it. "
                   "Reasoning bears on both. Word Meaning because the JD asks "
                   "for someone who can \"create alignment across founders, "
                   "teams, and external partners\", and a CV is the only "
                   "writing this seat produces before interview.",
            "proxies": (
                "Moves across functions or industries that required learning "
                "an unfamiliar business quickly, with results on the far side "
                "of the move.",
                "Problems handed over without a template: a first commercial "
                "hire, a turnaround, a category nobody had sold yet.",
                "A summary line that states a position rather than listing "
                "adjectives.",
            ),
        },
        "reviewer": {
            "path": (
                "Employment history first, bottom to top, before reading the "
                "summary at all. The summary is written to be agreed with.",
                "Find the one job where they owned the growth number, and read "
                "only that one closely.",
                "Check whether any figure in the CV is theirs or the "
                "company's.",
                "Then the market row: schools, or anywhere a third party's "
                "safety governed the sale.",
            ),
            "calibration": "Seniority is not scope. Two candidates with the "
                           "same title differ here by budget, headcount and "
                           "who they reported to, and the CV usually says.",
            "probes": (
                "Walk me through the growth engine you built, channel by "
                "channel, and what it cost to acquire.",
                "What was broken in the operating cadence when you arrived, "
                "and what did you install?",
                "A time the founders wanted something different from what the "
                "number needed.",
            ),
        },
        "gaps": (
            "There is no work sample, so nothing here tests execution, "
            "judgment under time pressure, or how this candidate would "
            "sequence Ethos's first ninety days. Everything below a hiring "
            "decision has to come from interview.",
            "The JD's personal characteristics -- decisive, comfortable with "
            "ambiguity, moves between strategy and execution -- are "
            "unscoreable from a resume and are deliberately not rows here. "
            "Marking them would be marking prose style.",
            "Willingness to travel regularly is a requirement this grid "
            "cannot see. Ask at screen.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
    # -- 16. Recruiting ----------------------------------------------------
    #
    # Portal assignment 40, slug `recruiter`, live 2026-08-31; the Workable
    # posting (EC24F51CB9, "Recruiter - Philippines") went up the same morning
    # at 08:47 UTC. Sixty minutes including the video, which is the shortest
    # assessment in the pack by a factor of two, and the rubric that arrived
    # with it is a flat six-row table rather than the pack's block
    # architecture.
    #
    # The source's weights -- 20 / 25 / 30 / 10 / 5 / 10 across six rows --
    # are carried below unchanged. What this file decides is only which block
    # each row sits in, which changes how a card groups and nothing about the
    # arithmetic. Three things depart from the pack default, all three because
    # the source says so rather than because the assessment is short.
    #
    # `block_points` splits 50 / 10 / 10 / 5 / 25. The salary calls are the
    # spike rather than a third work-product row because the assessment names
    # them as the differentiator in its own words -- "whether you can make a
    # salary call and defend it, including when the easy answer and the fair
    # answer are different" -- and because that is the row where a recruiter
    # who is good at this and one who is merely fast come apart. Communication
    # buys 5 instead of 10; work product gives up 20 of the pack's 70 to pay
    # for the spike and the background block.
    #
    # `background` is open at 10, so `config.CV_WEIGHT_BY_SEAT` pins this
    # seat's external CV weight to 0.0 -- the pairing BLOCKS' `background`
    # note requires of any grid that opens it. The price is unit 14's price
    # and the reason is unit 14's reason in the source's own sentence: "The
    # Experience row adds, never blocks; score a missing signal 3, never 1."
    # A recruiter's public record is thin far more often than it is damning,
    # and at 10 points an empty optional section costs 4 points against an
    # otherwise perfect card and cannot decide the seat.
    #
    # `universal_auto_fails: False`, for the reason unit 14 switches it off.
    # Three of the four universal rules are cap-and-completeness rules, and
    # this rubric turns exactly those into scored rows: "Video absent" is a 1
    # on a 5-point row, "No note" is a 1 on a 10-point row, and "Grade what is
    # in front of you" is the source's own instruction. Leaving the list on
    # would have a grader close a candidacy on a missing video the rubric
    # prices at five points. The fourth universal rule -- fabricated facts
    # where the task supplied data -- is neither repealed nor contradicted
    # here and is carried forward by hand in `auto_fails` below: the task
    # supplies three stated expectations and a band, and a submission that
    # invents different ones is not marking the same exercise.
    #
    # What this rubric does NOT make an auto-fail is worth stating, because
    # the temptation is real. Asking what a finalist currently earns, and
    # anchoring an offer to Marisol's volunteered PHP figure, each break a
    # rule Material C states absolutely and the JD repeats. The source prices
    # both as 1-anchors on their own rows and ends the grading for neither, so
    # neither is an auto-fail here. They are triage checks and red flags
    # instead, which is where a rule that must be seen but must not decide the
    # score belongs.
    {
        "key": "recruiting",
        "unit": "Recruiting",
        "grid_name": "Recruiter",
        "entity": "Ajaia",
        "slugs": ("recruiter",),
        "roles": ("Recruiter (EC24F51CB9)",),
        "assessment": "Ajaia Recruiter Assessment, Core Assignment, 60 "
                      "minutes including the video",
        "location": "Remote -- Philippines, India, Pakistan, Kenya or Sri "
                    "Lanka, meaningful overlap with US Eastern. $10,000 to "
                    "$20,000 all-in: $9,000 to $17,000 base plus up to 18 "
                    "percent quarterly bonus.",
        "spike": "The salary calls",
        # 50 / 10 / 10 / 5 / 25. Stated in full rather than as a diff, because
        # a reader who sees only "spike: 25" cannot tell what paid for it.
        "block_points": {
            "work_product": 50,
            "background": 10,
            "ai_forwardness": 10,
            "communication": 5,
            "spike": 25,
        },
        "universal_auto_fails": False,
        "seat": "The seat owns hiring end to end for a company running 30+ "
                "roles across two systems and thousands of submissions: "
                "intake, writing the posting, sourcing, screening, moving "
                "people through the assessment platform, scheduling, and "
                "keeping every candidate informed. The assignment is that job "
                "compressed into one hour -- one intake, three salary calls, "
                "one posting -- and the JD's line that the volume \"only "
                "works when one person owns the whole machine and keeps it "
                "clean\" is what the hour is sampling.",
        "core_skill": "Making a call and defending it under a clock. The "
                      "assessment says so twice: 60 minutes is \"the only "
                      "hard constraint\", and \"a different approach is "
                      "treated the same as the expected one when it is "
                      "coherent, fits the constraints, and you say why you "
                      "chose it.\"",
        "competencies": (
            {"label": "Intake that prevents a rewrite",
             "asks": "The questions that would have to be answered before the "
                     "posting is written, and the courage to name what the "
                     "brief gets wrong.",
             "anchor": "\"Run intake with hiring managers: get the real "
                       "requirements out of them, and push back when a brief "
                       "asks for two different people\" (JD), tested by "
                       "Renata's years contradiction (Task 1)"},
            {"label": "Holding a range",
             "asks": "A number put in front of a candidate, and the sentence "
                     "said about it, when the easy answer and the fair answer "
                     "differ.",
             "anchor": "\"Handle salary conversations: know the band, hold it, "
                       "and tell a candidate early when their expectation and "
                       "our range do not meet\" (JD), tested by Daniel at "
                       "$9,500 and Aditi at $31,000 (Task 2)"},
            {"label": "Writing a posting to house style",
             "asks": "A publish-ready JD built from rough notes, against a "
                     "one-page style sheet the candidate is handed.",
             "anchor": "\"Most of our roles need the job description built "
                       "from a hiring manager's rough notes, not copied from "
                       "the last one\" (JD), against Material C (Task 3)"},
            {"label": "Candidate-pay discipline",
             "asks": "Whether the rule survives contact with a hiring manager "
                     "who asks for it and a candidate who volunteers it.",
             "anchor": "\"We never ask a candidate what they currently earn. "
                       "Expectations only\" (JD and Material C), tested by "
                       "Renata's request and Marisol's cover note"},
        ),
        "criteria": (
            {"key": "intake_questions", "label": "Intake questions (Task 1)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Questions that would prevent a rewrite: scope, must-haves "
                    "versus preferences, the interview loop, the decision "
                    "timeline. The years contradiction is named -- \"3+ years "
                    "minimum in process or ops or consulting, non negotiable\" "
                    "against \"honestly the years thing matters less than "
                    "whether they can hold a room\" -- and the answer says "
                    "what they would do about it. The current-compensation "
                    "request is refused plainly, with what they will provide "
                    "instead. Ten questions or fewer, and the three lines to "
                    "Renata are used on what has to be resolved before this "
                    "goes live.",
                 3: "A competent list, but generic, or it names the "
                    "contradiction without saying what they would do about it.",
                 1: "Questions that restate the notes back, no contradiction "
                    "noticed, or an intake question that asks what the "
                    "finalists currently earn.",
             }},
            {"key": "posting", "label": "The posting (Task 3)",
             "block": "work_product", "weight": 30,
             "anchors": {
                 5: "Publish-ready. House style held: one canonical title with "
                    "the alternates listed separately as search terms, no "
                    "\"rockstar\" and no other banned word, 5 to 7 short "
                    "requirement lines at one capability each, an AI fluency "
                    "line, and the compensation range stated in the body. "
                    "Overview, what the person will do, requirements and the "
                    "compensation line are all present. Reads like a person "
                    "wrote it and a good candidate would finish it.",
                 3: "On-format and clean, but generic, or one style rule "
                    "slipped, or the requirements bloat past 7 lines.",
                 1: "\"Rockstar\" or another banned word survived, the role is "
                    "posted under all three of Renata's titles, no "
                    "compensation line, or the requirements are a copy of "
                    "Renata's paragraph.",
             }},
            {"key": "salary_calls", "label": "The salary calls (Task 2)",
             "block": "spike", "weight": 25,
             "anchors": {
                 5: "Daniel brought into the band or flagged as under floor "
                    "with a recommendation -- his $9,500 sits below the stated "
                    "14 to 22. Aditi handled with an early, direct "
                    "conversation about the range, her $31,000 against a "
                    "$22,000 ceiling. Marisol assessed against the "
                    "client-facing requirement she has never met, not against "
                    "her six years. Marisol's volunteered PHP 65,000 never "
                    "used and never repeated. The recommendation follows what "
                    "Renata said she needs -- someone who can run a mapping "
                    "session without her in the room -- and picks one person, "
                    "under 100 words.",
                 3: "Reasonable calls, but one candidate is handled on the "
                    "resume rather than the brief, or the recommendation "
                    "hedges instead of picking.",
                 1: "Daniel offered $9,500 because he asked for it, an offer "
                    "anchored to Marisol's current pay, or Aditi advanced with "
                    "the range gap unmentioned.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names specific tools and specific uses, and names "
                    "something AI produced that was rejected, with a reason. "
                    "Inside 60 words.",
                 3: "Lists tools and uses, but nothing AI gave them was "
                    "apparently rejected.",
                 1: "No note, or a note with no specifics.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 5,
             "anchors": {
                 5: "Clear and direct on why this role and why Ajaia, then "
                    "walks through real reasoning on the hardest salary call "
                    "and a choice in the posting. Screen plus camera, 6 to 8 "
                    "minutes, salary expectations stated, every link opens.",
                 3: "Covers most of what was asked, but the walkthrough "
                    "narrates the work instead of explaining the thinking "
                    "behind it.",
                 1: "Video absent, salary expectations not stated, or the "
                    "submission is hard to follow.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             # The neutral-3 rule, on the row it governs, because this is the
             # row a grader is reading when the temptation to mark an empty
             # optional section as a 1 arises. The source states it twice, in
             # its preamble and in this row's own 3 anchor.
             "note": "Adds, never blocks. Nothing to review scores 3, never 1. "
                     "The 1 is reserved for links that do not open and for "
                     "shown work that contradicts the submission, never for "
                     "absence -- the section is optional and the assessment "
                     "says leaving it blank is fine.",
             "anchors": {
                 5: "Real, checkable recruiting work: roles filled, a posting "
                    "or careers page they wrote, a sourcing project, a public "
                    "profile that holds up.",
                 3: "Some relevant evidence. Also score 3 when nothing is "
                    "provided at all -- the section is optional and absence is "
                    "not a signal.",
                 1: "Links provided that do not open, or shown work that "
                    "directly contradicts the quality of the submission. Score "
                    "1 only for a broken or contradicted claim, never for an "
                    "empty field.",
             }},
        ),
        # Two rules only, and neither is a completeness rule -- see the header
        # comment above for why the universal list is off and why exactly one
        # of its four rules is carried forward here.
        "auto_fails": (
            "Fabricated facts or numbers where the task supplied them: a band "
            "other than 14 to 22, a stated expectation other than $16,000, "
            "$9,500 or $31,000, or a candidate detail invented rather than "
            "read out of Material B.",
            "Confirmed fraud or misrepresentation visible on the face of the "
            "submission: another person's work, a visibly different person "
            "across the video and the writing, burner-domain or "
            "automated-apply patterns, or materials that hand the posting's "
            "own phrasing back as content. This routes to the fraud log, not "
            "to grading.",
        ),
        "red_flags": (
            "Any request for a candidate's current pay, in any format, at any "
            "stage -- including agreeing to Renata's \"can you also find out "
            "what the finalists are making now\". Material C forbids it "
            "absolutely and the JD repeats it. It is a 1 on the row it sits "
            "under and a probe at screen, not an auto-fail.",
            "Marisol's PHP 65,000 repeated anywhere in the submission, even "
            "neutrally. The rule is that a volunteered figure is not used and "
            "does not go in the notes; a submission that quotes it back has "
            "put it in the notes.",
            "The role posted under all three of Renata's titles because she "
            "asked. The point of Material C is that it is the standard and the "
            "hiring manager's note is not.",
            "A recommendation that ranks all three and picks none. The task "
            "asks who to hire.",
            "Deference as a strategy: every one of Renata's instructions "
            "carried out, including the two Material C forbids. The seat "
            "exists to push back.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated-apply pattern, a different "
                                      "person in the video than in the "
                                      "writing, posting phrasing handed back "
                                      "as content."},
            {"key": "complete", "label": "Complete: intake questions plus the "
                                         "lines to Renata, three salary calls "
                                         "plus the recommendation, the "
                                         "posting, the AI Workflow Note, and a "
                                         "video link at the top."},
            {"key": "opens", "label": "Every link opens in a private window, "
                                      "and a reference to ajaia.ai appears in "
                                      "the materials. Ask once before counting "
                                      "a dead link against them."},
            {"key": "contradiction", "label": "The years contradiction in "
                                              "Renata's notes is named: 3+ "
                                              "non-negotiable against \"the "
                                              "years thing matters less\"."},
            {"key": "current_pay", "label": "Nobody is asked what they "
                                            "currently earn, at any stage, and "
                                            "Marisol's volunteered figure is "
                                            "neither used nor repeated."},
            {"key": "posting_style", "label": "The posting carries one "
                                              "canonical title, no banned "
                                              "word, and a compensation line."},
        ),
        "tells": {
            "strong": "A number put in front of Daniel that is not the number "
                      "he asked for, with the reason said out loud.",
            "weak": "An intake question that asks Renata to find out what the "
                    "finalists are making now.",
        },
        "do_not_penalize": (
            "Going over the 60 minutes. \"Grade what is in front of you.\" The "
            "clock is the candidate's constraint, not a criterion, and "
            "reviewers run no forensics on when a file was written.",
            "An approach that is not the one we had in mind. The assessment "
            "says so in as many words: a different approach is treated the "
            "same as the expected one when it is coherent, fits the "
            "constraints, and the candidate says why they chose it. Advancing "
            "Marisol on a development plan and rejecting Aditi on budget are "
            "both defensible; what is graded is the defence.",
            "Prose that reads as AI-written. This is an AI-native seat, the "
            "assessment instructs candidates to use AI, and what is graded is "
            "the Workflow Note and whether a filter was applied on top -- not "
            "whether a model was involved.",
            "Rough video production. Screen plus camera is the only "
            "requirement; lighting, edit and set are scored nowhere.",
            "An empty optional prior-work section. It is optional, it adds, "
            "and the background row scores 3 when there is nothing to read.",
            "A dead link, on first sight. Ask once for a working one, grade "
            "what is reachable meanwhile, and score `experience` and the "
            "`opens` triage check down only after the ask has gone "
            "unanswered.",
            "A short posting. Material C caps requirements at 5 to 7 lines and "
            "bans hype; a JD that comes in tight and plain is the house style "
            "working, not thin work.",
        ),
        "gia": {
            "primary": ("Word Meaning", "Reasoning"),
            "secondary": ("Perceptual Speed",),
            "why": "Word Meaning fits a seat whose whole output is written -- "
                   "postings, outreach, screening notes, rejections -- and "
                   "whose JD says a candidate can tell a templated message "
                   "from a real one in about four words. Reasoning fits "
                   "spotting that a brief contradicts itself and holding a "
                   "band against three different arguments for breaking it. "
                   "Perceptual Speed is secondary and belongs to the volume "
                   "part of the seat rather than to this hour. Ajaia "
                   "administers no instrument, so only the proxies below are "
                   "live.",
            "proxies": (
                "Whether the years contradiction was noticed at all, and how "
                "quickly it is disposed of.",
                "How the 60 minutes were allocated across four tasks and a "
                "video, and whether that allocation matches the weights.",
                "Whether the current-pay refusal is argued or merely asserted.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 2 first, all three calls and the recommendation. It is "
                "25 points and the fastest read in the submission (4 min).",
                "Task 1, checking for the contradiction and for the "
                "current-pay refusal (3).",
                "Task 3 against Material C, line by line: title count, banned "
                "words, requirement-line count, AI fluency line, compensation "
                "line (5).",
                "Task 4, the AI Workflow Note (1).",
                "Video, the opening 90 seconds and the hardest-call "
                "walkthrough (8).",
                "Optional prior work, links opened (2).",
            ),
            "calibration": "The failure mode on this seat is rewarding "
                           "compliance. A submission that does everything "
                           "Renata asked -- three titles, current pay "
                           "collected, Daniel signed at the number he named -- "
                           "is fast, tidy, and wrong on three counts, and it "
                           "reads well until you check it against Material C. "
                           "Read the posting with the style sheet open. The "
                           "second failure mode is the reverse: a candidate "
                           "who lectures the hiring manager instead of asking "
                           "her a question. The row rewards a refusal that "
                           "says what will be provided instead.",
            "probes": (
                "\"Renata needs this live by Wednesday and she is out Thursday "
                "and Friday. Walk me through your Monday.\"",
                "\"You put a number in front of Daniel. He says another firm "
                "offered him 12. What do you say?\"",
                "\"Tell me about a brief you pushed back on, and what "
                "happened.\"",
            ),
        },
        "notes": (
            "Use 4 and 2 often, which the source says in its own preamble and "
            "which matters more here than anywhere else in the pack. There "
            "are six rows and three of them carry 20 points or more, so a "
            "reviewer who marks only 3s and 5s is moving candidates in 12- "
            "and 15-point steps and compressing the pool rather than ranking "
            "it.",
            "What the 10-point background weighting changes, with the "
            "arithmetic. A candidate who scores 3 on `experience` and a "
            "perfect 5 everywhere else lands at 96, so an empty optional "
            "section costs 4 points and nothing else. Work product plus the "
            "spike is 75 on its own, so a candidate can clear the advance bar "
            "on the three task rows alone with no public record at all. That "
            "is what \"adds, never blocks\" means here.",
            "The bands are the pack's, unchanged, and the source agrees with "
            "them: advance at 75, hold 60 to 74, reject below 60. The pack "
            "splits the advancing band at 85 into Best and Better so the top "
            "of the queue is visible without opening every card; that is a "
            "presentation split above the source's bar, not a second bar.",
            "Check the posting against Material C with the style sheet open "
            "rather than from memory. Five of the checks in that row are "
            "objective and take under a minute between them -- count the "
            "titles, search for the six banned words, count the requirement "
            "lines, look for the AI fluency line, look for the compensation "
            "line -- and doing them by eye is how a polished posting that "
            "breaks two rules gets a 4.",
            "The assessment is 60 minutes including the video, the shortest "
            "in the pack. That is a scoping constraint on what can be asked "
            "of a submission, not a licence to mark it more gently: the "
            "weights already reflect what an hour can show, and the source's "
            "instruction is to grade what is in front of you.",
        ),
        "gaps": (
            "No sourcing task. The JD says \"many of the roles we fill need "
            "candidates found, not just candidates who applied\", and nothing "
            "in this hour tests whether the candidate can find anyone. The "
            "`experience` row is the only place a sourcing project is visible "
            "at all, and it is worth 10.",
            "No volume and no ATS hygiene. The seat exists because 30+ roles "
            "and thousands of submissions across two systems need one owner, "
            "and a one-intake, three-candidate exercise samples none of that. "
            "Screen for it, or watch it in the first month.",
            "Nothing tests candidate communication at scale -- the rejection "
            "that sounds like a person wrote it, the pipeline nobody sits in "
            "for six weeks. The JD names both as standards and the assessment "
            "asks for neither.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
    # -- 17. Marketing Associate -------------------------------------------
    #
    # Portal assignment 41, slug `marketing-associate`, and the first of six
    # grids added on 2026-08-31 from one instruction set. All six share an
    # architecture the pack did not have before them: a `seeded` list naming
    # what was planted in the materials on purpose, reported caught or missed
    # per criterion rather than averaged into a mark. See `seeded_of`.
    #
    # All six also open `background` at 10 on the same rule -- "always adds,
    # never blocks... a candidate with nothing to show there scores a 3, never
    # a 1" -- so `config.CV_WEIGHT_BY_SEAT` pins all six to 0.0, and all six
    # switch the universal auto-fails off, because every one of these rubrics
    # prices absence as a 1 on the row rather than as the end of the grading:
    # "under 300 words", "No note", "No working prototype", "Missing
    # walkthrough". The one universal rule none of them repeals -- fabricated
    # facts where the task supplied data -- is carried forward by hand, and it
    # bites harder here than usual, because each of these packs hands the
    # candidate real numbers to reason from.
    #
    # 50 / 10 / 10 / 5 / 25. The channel set is the spike rather than a third
    # work-product row because it is the only task where the constraint and
    # the easy win point in opposite directions: Task 2 is scoped to public
    # ajaia.ai material, and Sam's unverified 480 is sitting right there in
    # Material B looking like a great stat. Everything else in this pack
    # rewards judgment; that row rewards restraint, which is rarer.
    {
        "key": "marketing_associate",
        "unit": "Marketing Associate",
        "grid_name": "Marketing Associate",
        "entity": "Ajaia",
        "slugs": ("marketing-associate",),
        "roles": ("Marketing Associate (ABDD1D2556)",),
        "assessment": "Ajaia Marketing Associate Assessment, Core Assignment, "
                      "120 minutes including the video",
        "location": "Remote",
        "spike": "Working inside the constraint",
        "block_points": {
            "work_product": 50,
            "background": 10,
            "ai_forwardness": 10,
            "communication": 5,
            "spike": 25,
        },
        "universal_auto_fails": False,
        "seat": "A simulated day as Ajaia's Marketing Associate in the week "
                "the podcast is being prepped: a think piece, a channel "
                "piece, and a clip plan for the launch episode. The materials "
                "are deliberately slightly chaotic -- \"two people in the "
                "materials below do not fully agree with each other\" -- and "
                "the pack is built to see what the candidate notices while "
                "working fast.",
        "core_skill": "Noticing. Every one of the five planted items is "
                      "visible on one read and easy to skim past on a clock, "
                      "and the assessment says so: the video asks for "
                      "\"anything you deliberately left out or any conflict "
                      "in the materials you noticed.\"",
        # What the pack planted. Each names the material it lives in, so a
        # reviewer can check the grader rather than take its word.
        "seeded": (
            {"key": "beta_480",
             "label": "The unverified 480 beta signups",
             "where": "Material B, Sam Okafor's email",
             "criteria": ("channel_set",),
             "caught": "The figure is left out of the public-facing channel "
                       "piece, and ideally the candidate says why: Sam "
                       "flagged possible bot traffic from a Reddit thread and "
                       "wrote \"I wouldn't stake anything on it yet.\" "
                       "Deliberately not using it, and saying so, is the "
                       "signal.",
             "missed": "The 480 appears in the channel deliverable. That is "
                       "two failures at once: Task 2 is scoped to public "
                       "ajaia.ai material only, and the number is one the "
                       "source himself would not stand behind."},
            {"key": "client_names",
             "label": "Legal's no-client-names rule",
             "where": "Material A, Priya Chen 10:03 AM",
             "criteria": ("channel_set",),
             "caught": "No client or partner company is named in the public "
                       "piece. Bellwether Mutual and Marcus Webb are episode "
                       "material, not channel material, and the site copy is "
                       "used as the source of truth as instructed.",
             "missed": "Bellwether Mutual, or any other client or partner, "
                       "named in the channel piece. Priya's reminder is "
                       "explicit and says \"(again)\", which is the tell that "
                       "this is a standing rule rather than a preference."},
            {"key": "runtime_mismatch",
             "label": "The recording-length discrepancy",
             "where": "Material C: brief says 40 minutes, Priya's planning "
                      "note says 42, the timestamped outline closes at 39:30",
             "criteria": ("podcast",),
             "caught": "The mismatch is named. Resolving it is not required "
                       "and not expected -- noticing that three numbers "
                       "describing one recording disagree is the whole test.",
             "missed": "A clip plan and show notes written as though the "
                       "three figures agreed, with a runtime stated as "
                       "settled."},
            {"key": "hedged_stat",
             "label": "Marcus's walked-back results number",
             "where": "Material C, 33:15",
             "criteria": ("podcast",),
             "caught": "The near-half processing-time cut is attributed as "
                       "Marcus's own hedged claim, or left out. He says \"we "
                       "think\", \"still validating the methodology\" and "
                       "\"don't quote me on an exact number yet\" in one "
                       "breath.",
             "missed": "\"Cut processing time in half\" headlined as settled "
                       "fact in a clip title, hook or the show notes. This is "
                       "the single most common way to fail this row."},
            {"key": "launch_date",
             "label": "The unresolved launch date",
             "where": "Material A: Priya says this Thursday, Dev says the "
                      "calendar says the Friday after next, never resolved",
             "criteria": ("podcast",),
             "caught": "The conflict is flagged as an open question rather "
                       "than silently decided. Priya's own \"huh, will "
                       "check\" is the evidence that nobody has settled it.",
             "missed": "One date picked and stated as fact with no flag, or "
                       "no date handled at all."},
            {"key": "brand_voice",
             "label": "The brand voice note as a live test",
             "where": "Material D",
             "criteria": ("think_piece", "channel_set"),
             "caught": "No \"unlock\", \"seamless\", \"game-changing\" or "
                       "\"cutting-edge\" anywhere, and the prose would not "
                       "work on any other AI company's homepage.",
             "missed": "A banned word survives, or the copy is generic "
                       "AI-company writing. Material D is one short paragraph "
                       "and easy to skim past, which is the point of putting "
                       "it there."},
        ),
        "criteria": (
            {"key": "think_piece", "label": "Think piece (Task 1)",
             "block": "work_product", "weight": 30,
             "anchors": {
                 5: "Real, arguable position an enterprise buyer who has "
                    "already sat through a failed pilot would stop scrolling "
                    "for. Specific, supportable, and it sustains itself. "
                    "Brand voice held: no banned word, and it would not work "
                    "on another AI company's homepage.",
                 3: "Competent and readable, but the position is safe or the "
                    "examples are generic.",
                 1: "Definitional summary of AI, hype language, invented "
                    "statistics, or under 300 words.",
             }},
            {"key": "podcast", "label": "Podcast launch task (Task 3)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Clips chosen for a real reason, with hooks that earn the "
                    "next few seconds. The hedged results stat handled "
                    "honestly. Show notes tight, accurate and inside 150 "
                    "words. Timestamp ranges, titles and hooks all present.",
                 3: "Reasonable clip choices but a weak hook, or show notes "
                    "that pad rather than inform.",
                 1: "Clips picked at random, the hedged number presented as "
                    "settled fact, or show notes that misstate the material.",
             }},
            {"key": "channel_set",
             "label": "Channel execution set (Task 2)",
             "block": "spike", "weight": 25,
             "anchors": {
                 5: "Platform-native and platform named first. Built only "
                    "from public ajaia.ai material, brand voice held, no "
                    "client or partner named. The three sentences give clear "
                    "reasoning for the choices made and for what was "
                    "deliberately left out.",
                 3: "On-format and clean, but generic or leaning on template "
                    "thinking.",
                 1: "Off-constraint -- non-public or unverified material, "
                    "including the 480 figure -- or off-brand voice, or no "
                    "reasoning given.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Names specific tools and specific uses, and names "
                    "something AI produced that was rejected, with a reason. "
                    "Inside 100 words.",
                 3: "Lists tools and uses, but nothing was apparently "
                    "rejected.",
                 1: "No note, or no specifics.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 5,
             "anchors": {
                 5: "Walks through the real reasoning behind all three "
                    "deliverables, screen plus camera, 5 to 8 minutes, and "
                    "closes cleanly with who they are, why Ajaia, and salary "
                    "expectations.",
                 3: "Covers most of what was asked, or is thin on reasoning, "
                    "or the close is rushed.",
                 1: "Missing walkthrough, or the close never states why Ajaia "
                    "or salary expectations.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. Nothing to show scores 3, "
                     "never 1. Reserve the 1 for links that do not open or "
                     "work that contradicts the submission.",
             "anchors": {
                 5: "Real, checkable work: an account they grew, published "
                    "writing, a produced show, or similar.",
                 3: "Some relevant evidence, or nothing provided at all -- "
                    "score 3 here, never 1.",
                 1: "Links that do not open, or shown work that contradicts "
                    "the quality of the submission.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers where the pack supplied them: a "
            "runtime, a results figure, a signup count or a quotation "
            "attributed to Marcus that is not in Material C.",
            "Confirmed fraud or misrepresentation: another person's work, a "
            "visibly different person in the video, burner-domain or "
            "automated-apply patterns. Routes to the fraud log, not to "
            "grading.",
        ),
        "red_flags": (
            "The 480 used anywhere public. It is the most attractive number "
            "in the pack and the one the source refused to stand behind.",
            "A client or partner company named in the channel piece.",
            "Production polish carrying a piece that missed the substantive "
            "call. Say so explicitly in the brief -- \"well-produced but "
            "misses X\" -- rather than letting the polish lift the mark.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, posting phrasing "
                                      "handed back as content."},
            {"key": "complete", "label": "Complete: think piece, channel "
                                         "piece plus three sentences, clip "
                                         "plan and show notes, AI Workflow "
                                         "Note, and a video."},
            {"key": "opens", "label": "Every link opens in a private window "
                                      "and a reference to ajaia.ai appears in "
                                      "the materials. Ask once before "
                                      "counting a dead link against them."},
            {"key": "constraint", "label": "The channel piece stays inside "
                                           "public ajaia.ai material: no 480, "
                                           "no client or partner named."},
            {"key": "hedged", "label": "The near-half processing-time cut is "
                                       "not presented as a settled number."},
            {"key": "voice", "label": "No banned word survives: unlock, "
                                      "seamless, game-changing, "
                                      "cutting-edge."},
        ),
        "tells": {
            "strong": "A sentence saying what they left out and why -- the "
                      "480, a client name, a date they could not confirm.",
            "weak": "\"Cut processing time in half\" as a clip title.",
        },
        "do_not_penalize": (
            "Going over the 120 minutes. Grade what is in front of you.",
            "Rough production on the channel piece or the video. Neither is a "
            "production test; the reasoning behind the choices is what is "
            "scored.",
            "Prose that reads as AI-written. AI use is expected and asked "
            "for; what is graded is the Workflow Note and whether a filter "
            "was applied on top.",
            "An empty optional prior-work section. It is optional, it adds, "
            "and the background row scores 3 when there is nothing to read.",
            "A dead link on first sight. Ask once, grade what is reachable "
            "meanwhile.",
            "Not resolving the runtime discrepancy or the launch date. "
            "Noticing is the test; resolving is not asked for.",
        ),
        "gia": {
            "primary": ("Word Meaning", "Perceptual Speed"),
            "secondary": ("Reasoning",),
            "why": "Word Meaning fits a seat whose output is a think piece, a "
                   "caption and show notes. Perceptual Speed fits the real "
                   "task under the clock -- five planted items across four "
                   "short materials, none of them hidden and all of them easy "
                   "to skim past. Ajaia administers no instrument, so only "
                   "the proxies below are live.",
            "proxies": (
                "How many of the five planted items were named at all.",
                "Whether the hedged stat was attributed or asserted.",
                "Whether the three sentences on Task 2 name something left "
                "out.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 2 first, against the constraint: search the channel "
                "piece for 480 and for any company name (3 min).",
                "Task 3, checking the hedged stat and the runtime and date "
                "conflicts (4).",
                "Task 1 whole, for whether there is a position in it (5).",
                "Task 4, the AI Workflow Note (1).",
                "Video, the walkthrough then the close (8).",
                "Optional prior work, links opened (2).",
            ),
            "calibration": "The floor is a technically complete submission: "
                           "tasks done, links work. The separation is whether "
                           "the channel piece stayed inside the constraint "
                           "with the 480 sitting right there, whether the "
                           "podcast task handled the hedged stat honestly, "
                           "and whether the think piece takes an actual "
                           "position. A glossy channel piece built on the 480 "
                           "figure, or a think piece that reads like a 2026 "
                           "AI summary, is not rescued by production polish.",
            "probes": (
                "\"What did you leave out of the channel piece, and why?\"",
                "\"Which of the three runtime numbers would you have gone "
                "with, and what would you have done to find out?\"",
                "\"Read me your think piece's argument in one sentence. Who "
                "disagrees with it?\"",
            ),
        },
        "notes": (
            "Use 4 and 2 often. Six rows and three of them carry 20 points or "
            "more, so a reviewer marking only 3s and 5s moves candidates in "
            "large steps and compresses the pool rather than ranking it.",
            "Check the seeded issues before scoring the row they sit under. "
            "They are the primary signal on this pack -- more important than "
            "production polish or writing quality -- and four of the six are "
            "objective checks a reviewer can make with a text search.",
            "The background row at 10 means a candidate who scores 3 there "
            "and 5 everywhere else lands at 96. Work product plus the spike "
            "is 75 on its own, so the advance bar is reachable on the three "
            "task rows alone.",
        ),
        "gaps": (
            "No email or newsletter work, no analytics read, no posting "
            "cadence -- all of which the seat eventually owns.",
            "The channel piece is one asset. Nothing here tests whether a "
            "candidate can hold a look or a voice across a week of them.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },

    # -- 18. Head of Marketing ---------------------------------------------
    #
    # Portal assignment 42, slug `marketing-head`. The seat above unit 17 and
    # a different question: not "what do you notice" but "do you have a point
    # of view, and does it survive a founder pushing on it".
    #
    # 60 / 10 / 10 / 10 / 10. Work product takes three rows because the pack
    # asks for three separate pieces of writing and making, and the think
    # piece alone is 30 -- the assessment calls it "the centerpiece of this
    # exercise; spend real time here", so the weight follows the instruction.
    # The content engine plan is the spike at 10 rather than a fourth
    # work-product row because it is where the CAC number has to be handled,
    # and handling it is the seat: Theo's line, "whoever we hire for this
    # needs to actually own an answer to why not just paid", is the job
    # description in one sentence.
    {
        "key": "marketing_head",
        "unit": "Head of Marketing",
        "grid_name": "Head of Marketing",
        "entity": "Ajaia",
        "slugs": ("marketing-head",),
        "roles": ("Head of Marketing (2F71B2FA92)",),
        "assessment": "Ajaia Head of Marketing Assessment, Core Assignment, "
                      "120 minutes including the video",
        "location": "Remote",
        "spike": "Owning the answer to \"why not just paid\"",
        "block_points": {
            "work_product": 60,
            "background": 10,
            "ai_forwardness": 10,
            "communication": 10,
            "spike": 10,
        },
        "universal_auto_fails": False,
        "seat": "A finalist exercise for Head of Marketing. \"The founders "
                "want to see how you think and how you write before they see "
                "how you manage\" -- so the pack is four pieces of writing "
                "and making plus a plan, and the video is where the founders "
                "push back in person.",
        "core_skill": "Having a position and holding it. Three of the five "
                      "tasks fail the same way -- summary where a take was "
                      "asked for -- and the video's interruption is the same "
                      "test asked out loud.",
        "seeded": (
            {"key": "cac_drop",
             "label": "The one-week CAC drop",
             "where": "Exhibit A: Rana sees paid CAC down 40% last week; Theo "
                      "says one week off a small spend base is noise and asks "
                      "for the 8-week number; Rana has not pulled it",
             "criteria": ("content_engine", "video"),
             "caught": "The sample-size problem is named. If the figure is "
                       "used at all it is used with the baseline requirement "
                       "attached, which is exactly what Task 4 asks for: the "
                       "one number you would commit to, with the baseline you "
                       "would need first. This is the shape of the founder "
                       "interruption in the video.",
             "missed": "Rana's 40% cited as settled grounds to shift budget. "
                       "That is repeating an unresolved internal argument as "
                       "fact, in front of the two people having it."},
            {"key": "brand_voice",
             "label": "The brand voice note as a live test",
             "where": "Exhibit B",
             "criteria": ("think_piece", "commentary"),
             "caught": "No \"unlock\", \"seamless\", \"game-changing\" or "
                       "\"cutting-edge\" across the think piece and all three "
                       "takes, and the writing would not work on another AI "
                       "company's homepage.",
             "missed": "A banned word or generic AI-company voice. Task 2's "
                       "three short takes give three separate chances to "
                       "slip, which is why this issue reaches two rows."},
            {"key": "summary_not_take",
             "label": "Summary dressed as a take",
             "where": "Task 2, by design",
             "criteria": ("commentary",),
             "caught": "All three takes state an actual opinion. \"Say what "
                       "you actually think, not just what happened\" is the "
                       "instruction and it is met three times.",
             "missed": "\"X released a model with capability Y\" with no "
                       "stated opinion. The task is not done, however clean "
                       "the writing."},
            {"key": "make_it_craft",
             "label": "The making task as a craft test",
             "where": "Task 3, by design",
             "criteria": ("make_it",),
             "caught": "The reel or carousel is a real execution of one of "
                       "their own Task 2 takes, recognisably so, genuinely "
                       "platform-native, and built only from public ajaia.ai "
                       "material. \"This is the real piece, not a plan for "
                       "one.\"",
             "missed": "A generic AI-trend graphic not recognisably tied to "
                       "their own take, or material pulled from outside "
                       "ajaia.ai. Either skips the test."},
        ),
        "criteria": (
            {"key": "think_piece", "label": "AI think piece (Task 1)",
             "block": "work_product", "weight": 30,
             "anchors": {
                 5: "Real, arguable position a buyer who has sat through a "
                    "failed pilot would stop and read. Specific, supportable, "
                    "400 words or more, brand voice held.",
                 3: "Competent and readable but safe or under-argued.",
                 1: "Definitional summary, hype language, invented "
                    "statistics, or under 400 words.",
             }},
            {"key": "commentary", "label": "AI commentary set (Task 2)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "All three are genuine takes in Ajaia's voice, specific "
                    "and defensible, none generic.",
                 3: "Two of three land as real takes; one slides into summary "
                    "or hype.",
                 1: "Mostly summary rather than opinion, or hype throughout.",
             }},
            {"key": "make_it", "label": "Make it, reel or carousel (Task 3)",
             "block": "work_product", "weight": 15,
             "anchors": {
                 5: "Genuine platform-native rendering of one of their own "
                    "takes, only public material, with clear reasoning for "
                    "the format and the choices.",
                 3: "On-format and competent but generic, or the connection "
                    "to the chosen take is thin.",
                 1: "Off-constraint (non-public material), the take is not "
                    "recognisable in the piece, or the execution is broken or "
                    "incomplete.",
             }},
            {"key": "content_engine",
             "label": "Content engine plan (Task 4)",
             "block": "spike", "weight": 10,
             "anchors": {
                 5: "Grounded in real ajaia.ai material, with specific format "
                    "bets. The one committed number is paired with the "
                    "baseline that would be needed before committing to it, "
                    "and the CAC figure is treated with scrutiny if it is "
                    "used at all.",
                 3: "Reasonably grounded but generic-playbook bets, or the "
                    "CAC figure used uncritically.",
                 1: "Generic plan with the name inserted, or a number with no "
                    "baseline requirement.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Presents the plan clearly. The interruption answer "
                    "directly engages the CAC-versus-content tension. The "
                    "reel or carousel is shown with real reasoning, the "
                    "read-aloud take lands, and the close states why Ajaia "
                    "and salary expectations plainly.",
                 3: "Adequate, but the interruption answer is generic, the "
                    "reasoning thin, the read-aloud flat, or the close "
                    "rushed.",
                 1: "The interruption is not meaningfully answered, a "
                    "required piece is missing, or the close never states why "
                    "Ajaia or salary expectations.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 5)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Specific tools and uses, and names something rejected "
                    "with a reason. Inside 100 words.",
                 3: "Lists tools and uses, but nothing apparently rejected.",
                 1: "No note, or no specifics.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. Nothing to show scores 3, "
                     "never 1.",
             "anchors": {
                 5: "Real, checkable audience-building work that holds up and "
                    "matches the seat.",
                 3: "Some relevant evidence, or nothing provided at all -- "
                    "score 3 here, never 1.",
                 1: "Links that do not open, or shown work that contradicts "
                    "the quality of the submission.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers, including anything from the "
            "exhibits presented as verified fact about Ajaia. The assessment "
            "says the exhibits are fictional composites and says so twice.",
            "Confirmed fraud or misrepresentation. Routes to the fraud log, "
            "not to grading.",
        ),
        "red_flags": (
            "Rana's 40% repeated as a finding. Theo is in the same thread "
            "telling her it is noise.",
            "Exhibit material presented as real Ajaia data. The pack warns "
            "against this explicitly.",
            "A polished submission that never takes a real position anywhere. "
            "At this level production value does not rescue it -- say so in "
            "the brief.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, JD phrasing handed "
                                      "back as analysis."},
            {"key": "complete", "label": "Complete: think piece, three takes, "
                                         "an actual reel or carousel, the "
                                         "content engine plan, the AI "
                                         "Workflow Note, and a video."},
            {"key": "opens", "label": "Every link opens, the reel or carousel "
                                      "is reachable, and ajaia.ai is "
                                      "referenced. Ask once before counting a "
                                      "dead link against them."},
            {"key": "position", "label": "The think piece takes a position "
                                         "someone could disagree with, and "
                                         "clears 400 words."},
            {"key": "interruption", "label": "The video answers \"why would "
                                             "we not just spend this on "
                                             "paid\" on camera."},
            {"key": "voice", "label": "No banned word survives across the "
                                      "think piece and the three takes."},
        ),
        "tells": {
            "strong": "A committed number with the baseline named as a "
                      "precondition rather than an afterthought.",
            "weak": "Three takes that could each be the first line of a news "
                    "story.",
        },
        "do_not_penalize": (
            "Going over the 120 minutes.",
            "Rough production on the reel or carousel. It is a craft test of "
            "whether the point survives the format change, not a production "
            "test.",
            "Disagreeing with Theo. A candidate who argues the 40% is worth "
            "acting on, and says what they would check first, has engaged the "
            "tension; that is the row's 5, not its 1.",
            "Prose that reads as AI-written.",
            "An empty optional prior-work section.",
            "A dead link on first sight. Ask once.",
        ),
        "gia": {
            "primary": ("Word Meaning", "Reasoning"),
            "secondary": ("Number Speed and Accuracy",),
            "why": "Word Meaning because the seat is judged on writing before "
                   "it is judged on managing, by the assessment's own words. "
                   "Reasoning because the whole pack turns on one question: "
                   "is a 40% move over one week off a small base evidence of "
                   "anything. Ajaia administers no instrument, so only the "
                   "proxies below are live.",
            "proxies": (
                "Whether the sample-size problem is named without being "
                "prompted.",
                "Whether the committed number arrives with its baseline.",
                "Whether the interruption answer holds or retreats into the "
                "flattering statistic.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 1 whole. It is 30 points and the centerpiece (8 min).",
                "Task 2, all three takes, asking of each: is there an opinion "
                "here (4).",
                "Task 3, opened and viewed at full size, checked against the "
                "take it claims to render (4).",
                "Task 4, looking for the baseline and for how CAC is handled "
                "(3).",
                "Video, the interruption answer first, then the rest (10).",
                "Task 5 and the optional prior work (3).",
            ),
            "calibration": "The floor is a complete, well-organized "
                           "submission. The separation is whether the writing "
                           "sounds like a specific point of view or generic "
                           "AI-company copy; whether the reel or carousel is "
                           "a real execution of their own take or a generic "
                           "AI-trend post; whether the interruption answer "
                           "holds up or retreats into the flattering CAC "
                           "stat; and whether the plan is grounded in what is "
                           "actually on ajaia.ai or is a generic playbook "
                           "with the name inserted.",
            "probes": (
                "\"Rana asks you again next week with an 8-week number that "
                "still looks good. What do you say?\"",
                "\"Which of your three takes would you not publish, and "
                "why?\"",
                "\"What is on ajaia.ai today that you would kill?\"",
            ),
        },
        "notes": (
            "Use 4 and 2 often. The commentary and make-it rows at 15 each "
            "are where the middle of the scale does the most work: two of "
            "three takes landing is explicitly a 3.",
            "Three of the four seeded issues are objective checks. Search for "
            "the four banned words, check whether the 40% appears with or "
            "without its caveat, and check whether the Task 3 piece is "
            "recognisably one of the Task 2 takes.",
            "The interruption is scored in the video row, but what it tests "
            "is the spike. A candidate whose written plan handles CAC well "
            "and who then folds on camera has not held the position; note it "
            "in the brief so the two rows are read together.",
        ),
        "gaps": (
            "Nothing tests managing, which is half the seat -- the assessment "
            "says so and defers it deliberately.",
            "No budget, no channel mix, no attribution model, no hiring plan.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },

    # -- 19. Technical Project Manager -------------------------------------
    #
    # Portal assignment 43, slug `technical-project-manager`. The only one of
    # the six with a build in it, and the weights say so: 30 points of working
    # code against 25 of triage.
    #
    # 50 / 10 / 5 / 10 / 25. Triage is the spike because the pack is built as
    # a ranking problem -- five requests land in one day and they are
    # deliberately of four different kinds: a scope add against a board-
    # committed date, a real self-reported bug, a stale blocker, a legitimate
    # non-urgent item, and noise. Telling those apart is the seat. The AI note
    # is 5 rather than 10 here, and the video 10, because this pack has a
    # demo in it: the video is where a build either runs or does not.
    {
        "key": "technical_pm",
        "unit": "Technical Project Manager",
        "grid_name": "Technical Project Manager",
        "entity": "Ajaia",
        "slugs": ("technical-project-manager",),
        "roles": ("Technical Project Manager (E5946F1944)",),
        "assessment": "Ajaia Technical Project Manager Assessment, Core "
                      "Assignment, 120 minutes including the video",
        "location": "Remote",
        "spike": "Ranking across four kinds of urgency",
        "block_points": {
            "work_product": 50,
            "background": 10,
            "ai_forwardness": 5,
            "communication": 10,
            "spike": 25,
        },
        "universal_auto_fails": False,
        "seat": "TPM on the Corrigan Peak Logistics engagement, three weeks "
                "from a go-live Dana Okafor gave her board. \"You do not "
                "write the production code. You are responsible for the plan, "
                "the risk list, what Corrigan Peak is told, and for catching "
                "problems in Priya's work before a client ever sees them.\"",
        "core_skill": "Separating four kinds of urgency in one inbox, then "
                      "telling the client the truth about what you found.",
        "seeded": (
            {"key": "status_green",
             "label": "The politely wrong status update",
             "where": "Material 2, sent last Friday: Terminal 3 called a "
                      "\"minor data validation task\" expected to wrap \"without "
                      "impact\", and \"No blockers to report\"",
             "criteria": ("status_update",),
             "caught": "The new update does not repeat that framing "
                       "uncritically. It was sent before anyone could honestly "
                       "know the Terminal 3 issue was minor, and DET-121 now "
                       "says urgency scores are coming back null for a chunk "
                       "of Terminal 3 exceptions.",
             "missed": "Last Friday's message reworded, still green, still "
                       "no blockers. Overcorrecting into needless alarm is "
                       "the other failure and scores no better."},
            {"key": "scope_add",
             "label": "Dana's auto-reassign request",
             "where": "Material 1A: add automatic carrier reassignment before "
                      "the 8th, a date given to the board, with \"I'd rather "
                      "not go back to him with no if there's a way\"",
             "criteria": ("triage", "status_update"),
             "caught": "Treated as a scope change against a fixed date rather "
                       "than a small favour: deferred with a reason, or "
                       "traded against something, and answered in the client "
                       "message rather than left hanging.",
             "missed": "Accepted into the release without naming what it "
                       "costs, or declined with no answer offered to Dana at "
                       "all."},
            {"key": "priya_bug",
             "label": "Priya's self-reported retry bug",
             "where": "Material 1B, 4:52 PM: an exception can be pushed twice "
                      "if the queue push succeeds and the database update "
                      "fails, or if two poll cycles overlap",
             "criteria": ("triage",),
             "caught": "Scheduled as this-week work. She flagged it herself "
                       "and asked whether it is a today problem or a "
                       "launch-week problem; the answer is today, and "
                       "flagging is not the same as fixing.",
             "missed": "Noted as a risk and left unscheduled, or deferred to "
                       "launch week."},
            {"key": "det121",
             "label": "DET-121, stale since 8/6",
             "where": "Material 1C: the EDI schema mismatch on Terminal 3, "
                      "\"see comment from 8/6, never picked up\", now "
                      "returning null urgency scores, 2 extra days once "
                      "Corrigan Peak's IT answers",
             "criteria": ("triage",),
             "caught": "Named as urgent AND the candidate asks why it sat "
                       "unresourced since 8/6. The dependency on the client's "
                       "IT contact is put on somebody's plate with a date.",
             "missed": "Flagged as a problem now, with no notice that it has "
                       "been sitting for weeks and no move to unblock the "
                       "client dependency."},
            {"key": "det118",
             "label": "DET-118, legitimate but not urgent",
             "where": "Material 1D: nobody has contacted the three terminal "
                      "leads to schedule the routing-rules review. \"No "
                      "blocker, just sitting.\"",
             "criteria": ("triage",),
             "caught": "Ranked below the two real risks but still given an "
                       "owner and a date. It is genuinely lower priority and "
                       "genuinely not nothing.",
             "missed": "Treated as top priority, or as never-needed. Either "
                       "shows a ranking with only two buckets in it."},
            {"key": "colour_ask",
             "label": "The shade-of-blue request",
             "where": "Material 1E, Marcus Lin: ops liked a different shade "
                      "of blue for the dispatcher queue UI, \"probably a "
                      "quick CSS thing\"",
             "criteria": ("triage",),
             "caught": "Named as the noise item and declined fast and "
                       "specifically. Task 1 asks plainly which item is "
                       "noise.",
             "missed": "Worked because it is quick, or declined across a "
                       "paragraph of justification. Both fail: the first "
                       "spends launch-week capacity on paint, the second "
                       "spends the candidate's own time proving a point "
                       "nobody contested."},
            {"key": "dirty_data",
             "label": "The planted dirt in the export",
             "where": "Material 3: Terminal 3 against T3, swft/SWFT/Swft, "
                      "three timestamp formats, and CPX-88216 with an empty "
                      "carrier_code",
             "criteria": ("build",),
             "caught": "Terminal, carrier and timestamp all normalised, and "
                       "the blank carrier on CPX-88216 explicitly handled or "
                       "flagged in the note as the record that could not be "
                       "confidently cleaned. The task asks for exactly that "
                       "note.",
             "missed": "A script that runs and silently drops or mishandles "
                       "the blank-carrier record. It has not been checked, "
                       "only executed -- which is the distinction the task "
                       "draws in its own words."},
        ),
        "criteria": (
            {"key": "build", "label": "Hands-on build (Task 2)",
             "block": "work_product", "weight": 30,
             "anchors": {
                 5: "Script runs, normalizes terminal, carrier code and "
                    "timestamp formatting correctly, and explicitly handles "
                    "or flags the missing carrier code. The note shows real "
                    "verification -- how they checked it worked, not just "
                    "that it ran -- and the summary count by event type is "
                    "there.",
                 3: "Script runs and mostly normalizes but misses one dirty "
                    "case silently.",
                 1: "Script does not run, or there is no evidence of checking "
                    "output against the data.",
             }},
            {"key": "status_update",
             "label": "Client status update (Task 3)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Honest, specific, and it reflects the real state of "
                    "things given what was decided in Task 1. Reads like "
                    "something a client would trust, and it answers Dana on "
                    "her request rather than going quiet on it.",
                 3: "Reasonable and safe but generic, or it partly repeats "
                    "the \"minor, no impact\" framing.",
                 1: "Restates last Friday's update almost unchanged, or "
                    "overcorrects into needless alarm.",
             }},
            {"key": "triage", "label": "Triage (Task 1)",
             "block": "spike", "weight": 25,
             "anchors": {
                 5: "Cleanly separates real risk, foreseeable risk, "
                    "legitimate-but-not-urgent and noise across all five "
                    "items, with worked, deferred or declined and a reason "
                    "for each. The noise item is declined fast and "
                    "specifically.",
                 3: "Reasonable calls on most items but one or two carry the "
                    "wrong urgency, or the reasoning is vague.",
                 1: "Treats every item as equally urgent, or works the noise "
                    "item as if it mattered.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Plan and build demo are clear and specific, presence is "
                    "confident, the build is shown actually running, and the "
                    "close plainly covers why this role, why Ajaia and salary "
                    "expectations.",
                 3: "Covers the required content but is generic or thin on "
                    "the technical demo.",
                 1: "Missing required content, or does not actually demo the "
                    "build running.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 5,
             "anchors": {
                 5: "Specific tool use, and a real example of something "
                    "checked or rejected.",
                 3: "Generic description without a concrete example.",
                 1: "Missing, or purely performative -- \"I used AI for "
                    "everything\".",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. Never score 1 solely because "
                     "links were left blank.",
             "anchors": {
                 5: "Resume, links or public work clearly show relevant "
                    "delivery and build experience.",
                 3: "Reasonable background, or no links provided with a "
                    "plausible resume for the seat.",
                 1: "Resume or links actively contradict fit for the seat. "
                    "Never score 1 solely for blank links.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers where the pack supplied them: "
            "exception records not in Material 3, a ticket that does not "
            "exist, or a go-live date other than September 8.",
            "Confirmed fraud or misrepresentation. Routes to the fraud log, "
            "not to grading.",
        ),
        "red_flags": (
            "A build submitted with no note on how it was checked. The task "
            "asks for the check specifically, in its own words: \"how you "
            "checked that it actually worked, not just that it ran\".",
            "A status update that goes quiet on Dana's request rather than "
            "answering it.",
            "A triage list with only two buckets in it -- urgent and not.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, posting phrasing "
                                      "handed back."},
            {"key": "complete", "label": "Complete: the five-item triage, a "
                                         "build file or exported workflow "
                                         "plus its note, the full status "
                                         "update, the AI Workflow Note, and a "
                                         "video."},
            {"key": "runs", "label": "The build is demonstrated running, not "
                                     "just described. Ask once for a working "
                                     "file before counting this against "
                                     "them."},
            {"key": "noise", "label": "The shade-of-blue request is named as "
                                      "noise and declined."},
            {"key": "bug", "label": "Priya's retry bug is scheduled as work, "
                                    "not merely noted as a risk."},
            {"key": "honest", "label": "The status update does not repeat "
                                       "\"minor, no impact, no blockers\" now "
                                       "that DET-121 says otherwise."},
        ),
        "tells": {
            "strong": "A note naming CPX-88216 by id as the record that could "
                      "not be confidently cleaned, and why.",
            "weak": "\"Status: Green\" in the new update.",
        },
        "do_not_penalize": (
            "Going over the 120 minutes.",
            "The language or tool the build is written in. \"Any language or "
            "no-code tool\" is the instruction; a spreadsheet formula chain "
            "that demonstrably works is a working build.",
            "A build that is ugly. Correctness and the verification note are "
            "what this row scores.",
            "Declining Dana's scope add. Declining it is a defensible call "
            "and often the right one; what is graded is whether she is given "
            "an answer.",
            "Prose that reads as AI-written.",
            "An empty optional prior-work section.",
        ),
        "gia": {
            "primary": ("Reasoning", "Number Speed and Accuracy"),
            "secondary": ("Perceptual Speed",),
            "why": "Reasoning for the ranking problem, which is the seat. "
                   "Number Speed and Accuracy for the build: five records "
                   "with three timestamp formats and one blank field is a "
                   "small, exact task where the error is silent. Ajaia "
                   "administers no instrument, so only the proxies below are "
                   "live.",
            "proxies": (
                "How many distinct urgency buckets the triage actually uses.",
                "Whether the blank carrier code is named by record id.",
                "Whether the status update's changes from Friday's are "
                "deliberate or cosmetic.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 1, all five items, checking the bucket each landed in "
                "(6 min).",
                "Task 2: open the file, run it if it runs, and read the note "
                "for the check (8).",
                "Task 3 against Task 1 -- does the update reflect what they "
                "decided (4).",
                "Video, the build demo especially (10).",
                "Task 4 and the optional prior work (3).",
            ),
            "calibration": "Every competent candidate flags Priya's bug and "
                           "the EDI issue as real; that is the floor, not the "
                           "signal. The separation is a clean ranking across "
                           "all five items rather than everything treated as "
                           "equally urgent or equally ignorable, real "
                           "verification against the dirty data, and a status "
                           "update that reflects actual state rather than a "
                           "safer reword of Friday's. A well-produced video "
                           "that skips technical substance is a Hold, not an "
                           "Advance. Nothing rescues a runs-but-unchecked "
                           "build.",
            "probes": (
                "\"DET-121 sat from 8/6 to now. How does that happen, and "
                "what would you change?\"",
                "\"Dana calls after reading your update. What is the first "
                "thing she asks?\"",
                "\"Walk me through how you convinced yourself the script was "
                "right.\"",
            ),
        },
        "notes": (
            "Use 4 and 2 often. The build row at 30 is where the middle of "
            "the scale matters most: \"runs and mostly normalizes but misses "
            "one dirty case silently\" is explicitly a 3, and that is the "
            "commonest real outcome.",
            "Run the build if you can. This is the only grid in the pack "
            "whose heaviest row can be checked rather than judged, and a "
            "reviewer who reads the code instead of running it is giving up "
            "the advantage.",
            "The status update is scored against the candidate's own Task 1, "
            "not against an ideal. A candidate who deferred DET-121 and then "
            "wrote an update consistent with that decision is internally "
            "coherent; grade the coherence.",
        ),
        "gaps": (
            "No estimation, no dependency mapping, no stakeholder management "
            "beyond one email, and no exposure to the engineer relationship "
            "that is most of the real seat.",
            "The build is five records. It tests care, not scale.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
    # -- 20. Design Lead ----------------------------------------------------
    #
    # Portal assignment 44, slug `design-lead`. The say-do gap pack: a
    # stakeholder sponsor with a confident feature list, two assistants who
    # describe different bottlenecks, and a usage log that settles which of
    # them the evidence supports.
    #
    # 30 / 10 / 5 / 25 / 30. Synthesis is the spike at 30 -- the joint-heaviest
    # row in the grid -- because the instruction that came with this rubric
    # says so in as many words: the say-do gap is "the single most important
    # separator in the pack". Communication buys 25 because the video is
    # 20 of it: this is the only one of the six where the client interruption
    # is scored that heavily, and it is where the synthesis either survives
    # contact with Owen or does not.
    {
        "key": "design_lead",
        "unit": "Design Lead",
        "grid_name": "Design Lead",
        "entity": "Ajaia",
        "slugs": ("design-lead",),
        "roles": ("Design Lead (370B787999)",),
        "assessment": "Ajaia Design Lead Assessment, Core Assignment, 120 "
                      "minutes including the video",
        "location": "Remote",
        "spike": "Telling need from ask",
        "block_points": {
            "work_product": 30,
            "background": 10,
            "ai_forwardness": 5,
            "communication": 25,
            "spike": 30,
        },
        "universal_auto_fails": False,
        "seat": "Design Lead on a three-week discovery engagement at Marsh & "
                "Colby, a forty-person commercial insurance brokerage. \"Your "
                "job in this pack is to figure out what Marsh & Colby's "
                "underwriting assistants actually need, design the ideal "
                "solution, and be ready to defend it to Owen when what you "
                "show him does not match what he asked for.\"",
        "core_skill": "Reading behaviour over stated preference, and then "
                      "holding that reading in front of the person who asked "
                      "for something else.",
        "seeded": (
            {"key": "say_do_gap",
             "label": "The say-do gap -- the core planted test",
             "where": "Anika calls the checklist \"fine\" and asks for "
                      "\"smart autofill\"; her own log shows 23 of 61 "
                      "submissions flagged for a coverage-limit mismatch, a "
                      "2.1-day median wait for a broker reply and nine over 4 "
                      "days. Colin names document-hunting and his log agrees: "
                      "11 minutes locating against 3 to complete the "
                      "checklist",
             "criteria": ("synthesis", "solution"),
             "caught": "The design targets document location and mismatch "
                       "resolution, and the synthesis says plainly that what "
                       "Anika asked for is not where her own time goes. This "
                       "is the single most important separator in the pack.",
             "missed": "\"Smart autofill for the checklist\", because that is "
                       "what was asked for. The candidate designed the "
                       "wishlist, not the need."},
            {"key": "two_transcripts",
             "label": "Two transcripts that partly contradict each other",
             "where": "Materials 1 and 2: Anika says mismatch and email "
                      "back-and-forth, Colin says document hunting",
             "criteria": ("synthesis",),
             "caught": "Both are treated as real -- they work differently, "
                       "and neither is wrong -- and the usage log is used to "
                       "reconcile them rather than to pick a winner. Task 1 "
                       "asks for one statement of where they agree and one of "
                       "where they do not.",
             "missed": "One transcript restated as the finding, or the two "
                       "averaged into a vague need, with no use of Material 4 "
                       "to adjudicate."},
            {"key": "owen_wishlist",
             "label": "Owen's reasonable but unsupported wishlist",
             "where": "Material 3: smart autofill, a cleaner interface, an AI "
                      "chat assistant, mobile access",
             "criteria": ("synthesis", "solution"),
             "caught": "Not dismissed outright, but separated -- \"good idea, "
                       "not supported by the evidence\" against \"core to what "
                       "we are solving\". None of his four items directly "
                       "addresses document-hunting or mismatch delay.",
             "missed": "The four bullets adopted as the design, or waved away "
                       "without engaging them. He is the sponsor and he is "
                       "not stupid; he is just working from the wrong "
                       "evidence."},
            {"key": "checklist_untouched",
             "label": "Owen's \"we don't need to touch that part much\"",
             "where": "Material 3, closing line",
             "criteria": ("synthesis",),
             "caught": "Contradicted with evidence. Mismatch reconciliation "
                       "is the more expensive problem and it lives in the "
                       "checklist flow, whatever Owen believes about it.",
             "missed": "Taken at face value, which quietly removes the most "
                       "expensive problem from scope before design starts -- "
                       "even if the output that follows is polished."},
            {"key": "autofill_distinction",
             "label": "Two different things called autofill",
             "where": "Owen and Anika mean typing speed; the real opportunity "
                      "is auto-pulling data from located documents",
             "criteria": ("solution",),
             "caught": "The distinction is named explicitly. Auto-pulling is "
                       "downstream of solving location and mismatch, not a "
                       "replacement for either. Credit this only when it is "
                       "stated, not when it is merely implied by the design.",
             "missed": "\"Autofill\" used throughout without saying which one "
                       "is meant, leaving it unclear whether the candidate "
                       "found the insight or repeated the word."},
            {"key": "interruption_ready",
             "label": "Owen's interruption is answerable, not a gotcha",
             "where": "The video: \"Our users asked for the wishlist, why are "
                      "you showing me something else?\"",
             "criteria": ("video",),
             "caught": "Evidence lines ready, and the candidate names "
                       "specifically what they kept and what they "
                       "reprioritized from his list -- without getting "
                       "defensive and without folding.",
             "missed": "Concedes the moment he pushes, or argues without "
                       "citing a single number from Material 4. A prepared "
                       "candidate has both the numbers and the manners."},
        ),
        "criteria": (
            {"key": "solution", "label": "Solution design (Task 2)",
             "block": "work_product", "weight": 30,
             "anchors": {
                 5: "Clearly targets document location and mismatch "
                    "resolution, with the two or three screens that matter "
                    "and a user journey. Human checkpoints are designed in "
                    "with a stated reason for each.",
                 3: "Competent but leans toward the wishlist; checkpoints are "
                    "mentioned but not designed in.",
                 1: "Essentially Owen's four bullet points with better "
                    "visuals.",
             }},
            {"key": "synthesis",
             "label": "Synthesis and next questions (Task 1)",
             "block": "spike", "weight": 30,
             "anchors": {
                 5: "Clearly separates need from ask with an evidence line "
                    "for each claim, correctly uses the usage log to resolve "
                    "the say-do gap, names one place the transcripts and the "
                    "log agree and one where they do not, and ties each "
                    "follow-up question to a design decision that would "
                    "change on the answer.",
                 3: "Reasonable synthesis, cites some evidence, partially "
                    "resolves the transcript gap, questions are generic.",
                 1: "Restates the wishlist or one transcript without "
                    "reconciling against the log; questions have no stated "
                    "design impact.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 20,
             "anchors": {
                 5: "Handles Owen's interruption with specific evidence and "
                    "holds the position without being rigid. Presents as if "
                    "to the client sponsor, in character. Closes clearly with "
                    "why this role, why Ajaia and salary expectations.",
                 3: "Covers the required content but is somewhat general or "
                    "thin.",
                 1: "Missing required content, or folds immediately with no "
                    "evidence-based answer ready.",
             }},
            {"key": "communication",
             "label": "Communication, overall",
             "block": "communication", "weight": 5,
             "anchors": {
                 5: "Clear, specific, and reads like someone who has done "
                    "this before.",
                 3: "Clear but generic.",
                 1: "Hard to follow.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 3)",
             "block": "ai_forwardness", "weight": 5,
             "anchors": {
                 5: "Specific tool use, and a real example of something "
                    "checked or rejected.",
                 3: "Generic description without a concrete example.",
                 1: "Missing, or purely performative.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. Never score 1 solely for "
                     "blank links.",
             "anchors": {
                 5: "Portfolio, links or resume clearly show relevant "
                    "discovery and design work, including evidence of closing "
                    "a say-do gap before.",
                 3: "Reasonable background, or no links with a plausible "
                    "resume for the seat.",
                 1: "Resume or links actively contradict fit. Never score 1 "
                    "solely for blank links.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers where the pack supplied them: usage "
            "figures not in Material 4, or a quotation attributed to Anika, "
            "Colin or Owen that is not in the transcripts.",
            "Confirmed fraud or misrepresentation. Routes to the fraud log, "
            "not to grading.",
        ),
        "red_flags": (
            "High-fidelity visuals with no evidence trail. This pack rewards "
            "the reasoning; the screens are how it is expressed.",
            "A design that solves document location and never mentions "
            "mismatch reconciliation, or the reverse. Both appear in both "
            "logs.",
            "Follow-up questions unconnected to design impact. Task 1 asks "
            "what would change about the design depending on the answer.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, posting phrasing "
                                      "handed back."},
            {"key": "complete", "label": "Complete: synthesis with evidence "
                                         "lines, agree/disagree statements, "
                                         "up to four questions, a user "
                                         "journey plus wireframes or a "
                                         "prototype, the AI Workflow Note, "
                                         "and a video."},
            {"key": "opens", "label": "Every link opens and the prototype or "
                                      "wireframes are viewable. Ask once "
                                      "before counting a dead link against "
                                      "them."},
            {"key": "log_used", "label": "Material 4 is actually cited, not "
                                         "just mentioned -- a number from the "
                                         "usage log appears in the "
                                         "synthesis."},
            {"key": "not_wishlist", "label": "The design targets something "
                                             "other than Owen's four bullets."},
            {"key": "interruption", "label": "The video answers Owen's "
                                             "interruption on camera, in "
                                             "character."},
        ),
        "tells": {
            "strong": "\"Anika asked for autofill; her log says she loses 2.1 "
                      "days per mismatch\" -- the ask and the evidence in one "
                      "sentence.",
            "weak": "A beautiful checklist screen with smart autofill on it.",
        },
        "do_not_penalize": (
            "Going over the 120 minutes.",
            "Low-fidelity output. \"Wireframes or a clickable prototype, in "
            "any tool\" is the instruction; a rougher prototype that clearly "
            "targets the say-do gap outscores a polished one that builds the "
            "wishlist.",
            "Keeping one or two items from Owen's list. Separating "
            "\"supported\" from \"good idea, not now\" is the skill; keeping "
            "nothing at all is as blunt as keeping everything.",
            "Disagreeing with this rubric's reading of the evidence, when the "
            "candidate's own reading is argued from the log. The test is "
            "whether behaviour beat stated preference, not whether they "
            "reached our answer.",
            "Prose that reads as AI-written.",
            "An empty optional portfolio section.",
        ),
        "gia": {
            "primary": ("Reasoning", "Spatial Visualisation"),
            "secondary": ("Word Meaning",),
            "why": "Reasoning because the pack is one inference problem: two "
                   "accounts, one log, and a stakeholder pointing the wrong "
                   "way. Spatial Visualisation for the screens themselves. "
                   "Ajaia administers no instrument, so only the proxies "
                   "below are live.",
            "proxies": (
                "Whether a number from Material 4 appears in the synthesis at "
                "all.",
                "Whether the two transcripts are reconciled or one is "
                "dropped.",
                "How fast the interruption answer reaches evidence.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 1 first and slowly. It is the spike and it decides most "
                "cards (10 min).",
                "Task 2, opened and clicked if clickable, asking one "
                "question: what problem do these screens solve (10).",
                "Video, the interruption especially (10).",
                "Task 3 and the optional portfolio (4).",
            ),
            "calibration": "The floor is noticing the two transcripts differ "
                           "in emphasis. The separation is using the usage "
                           "log to work out which parts of each are borne out "
                           "by behaviour rather than stated preference, and "
                           "whether the design actually targets document "
                           "location and mismatch resolution. A visually "
                           "polished prototype that quietly builds Owen's "
                           "four bullets is a Hold at best. A rougher "
                           "prototype that clearly targets the say-do gap is "
                           "a strong Advance.",
            "probes": (
                "\"Owen signs off, then asks in month two why the checklist "
                "still looks the same. What do you say?\"",
                "\"Which of Owen's four would you build third, and what would "
                "have to be true first?\"",
                "\"What would you have asked Anika if you had ten more "
                "minutes with her?\"",
            ),
        },
        "notes": (
            "Read Task 1 before Task 2, always. A strong prototype primes a "
            "reviewer to read the synthesis more generously than it deserves, "
            "and on this pack the synthesis is the thing being tested.",
            "The two 30-point rows can and often should diverge. A candidate "
            "who diagnoses the say-do gap correctly and then designs the "
            "wishlist anyway is a 5 and a 2, not a 4 and a 4; that split is "
            "informative and averaging it away loses the finding.",
            "Use 4 and 2 often. With six rows and two of them worth 30, "
            "clustering at 3 and 5 moves candidates in 12-point steps.",
        ),
        "gaps": (
            "No visual design system work, no handoff to engineering, no "
            "accessibility, and no evidence of how this candidate runs a "
            "design team -- which the title implies and the pack never "
            "samples.",
            "One round of synthesis on pre-collected materials. Nothing tests "
            "whether they can run the interview themselves.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },

    # -- 21. Product Manager -------------------------------------------------
    #
    # Portal assignment 45, slug `product-manager`. Five materials that each
    # point somewhere different, and one right answer that only appears when
    # three of them are read together.
    #
    # 50 / 10 / 5 / 10 / 25. The roadmap call is the spike because the pack's
    # whole design is a synthesis trap: the tagging tickets, the 30% of reels
    # never opened hiding inside a 92% share rate, and the say-do gap between
    # the 58% survey ask and 6% editor usage are three views of one problem,
    # and a candidate who treats them as three decisions has missed it. Spec
    # and prototype sit in work product at 25 each because they are executed
    # downstream of that call, whatever it was.
    {
        "key": "product_manager",
        "unit": "Product Manager",
        "grid_name": "Product Manager",
        "entity": "Ajaia",
        "slugs": ("product-manager",),
        "roles": ("Product Manager (782440C1B6)",),
        "assessment": "Ajaia Product Manager Assessment, Core Assignment, 120 "
                      "minutes including the video",
        "location": "Remote",
        "spike": "Reading five materials as one problem",
        "block_points": {
            "work_product": 50,
            "background": 10,
            "ai_forwardness": 5,
            "communication": 10,
            "spike": 25,
        },
        "universal_auto_fails": False,
        "seat": "PM for SidelineReel, Ajaia's app for youth sports clubs. "
                "\"You do not write the production code and you do not decide "
                "the visual design. You decide what gets built next, why, and "
                "what the spec asks the team to build.\" Customers are clubs; "
                "users are coaches and parents, and the two do not want the "
                "same things.",
        "core_skill": "Finding the one problem that several materials are "
                      "each describing a face of, and then being able to say "
                      "no to a $180k account with a reason.",
        "seeded": (
            {"key": "carla_livestream",
             "label": "Carla's livestreaming request",
             "where": "Material 1: Ridgeline's Executive Director wants live "
                      "streaming of every game, has raised it with sales "
                      "twice, and renewal is six weeks out",
             "criteria": ("roadmap",),
             "caught": "Neither committed to as asked nor dismissed. The "
                       "candidate names what Ridgeline needs underneath the "
                       "ask -- visibility for grandparents and scouts -- and "
                       "answers Carla concretely. Livestreaming is not the "
                       "same capability as SidelineReel's post-game editing "
                       "and tagging job.",
             "missed": "Put on the roadmap because the account is big, or "
                       "refused with nothing offered in its place. Task 1 "
                       "explicitly invites naming it as a not-next item."},
            {"key": "say_do_gap",
             "label": "The editing-controls say-do gap",
             "where": "Material 2: 58% of 210 parents name more editing "
                      "controls as their single biggest ask; the Reel Editor "
                      "was opened by 6% in 90 days and under 1% of those "
                      "finished an edit, while the auto-recap Share button is "
                      "used in 90% of recaps sent",
             "criteria": ("roadmap",),
             "caught": "Stated preference is weighed against behaviour and "
                       "behaviour wins, with the numbers cited. What parents "
                       "actually do is share the recap.",
             "missed": "Editing controls prioritized because 58% asked for "
                       "them. The survey is in the same material as the usage "
                       "data that contradicts it."},
            {"key": "tagging_root_cause",
             "label": "Three tickets, one root cause",
             "where": "Material 3: number 14 against number 4, \"jersey "
                      "numbers were similar, just swapped digits\", number 1 "
                      "against number 11 -- three clubs, one failure mode",
             "criteria": ("roadmap", "spec"),
             "caught": "Recognised as one problem: roster tagging confuses "
                       "visually similar jersey numbers. Three reports across "
                       "three unrelated clubs is a pattern, not a "
                       "coincidence.",
             "missed": "Handled as three separate complaints, or as "
                       "individual bugs to be fixed one at a time."},
            {"key": "noise_tickets",
             "label": "The app icon and the watermark",
             "where": "Material 3: a soccer-ball app icon for an all-sports "
                      "league, and a watermark sometimes missing on "
                      "camera-roll saves",
             "criteria": ("roadmap",),
             "caught": "The app icon is named as noise and declined quickly. "
                       "The watermark is real but low-severity and is ranked "
                       "as such -- the reporter says so himself: \"Not "
                       "urgent, just noticed it.\"",
             "missed": "Either ranked seriously, or a paragraph spent "
                       "declining the app icon. Both are triage misses."},
            {"key": "sales_pressure",
             "label": "The Slack thread is pressure, not evidence",
             "where": "Material 4: Jamal Whitfield, VP Sales, $180k a season, "
                      "\"our highest-risk renewal this quarter\", \"whatever "
                      "we tell Carla needs to be concrete\"",
             "criteria": ("roadmap",),
             "caught": "The commercial risk is named plainly as a real "
                       "business risk -- Ridgeline may not renew -- and kept "
                       "separate from the product evidence. Something "
                       "concrete goes back to Carla either way.",
             "missed": "The roadmap reordered because sales asked loudest and "
                       "the account pays most, with no evidence the fix "
                       "addresses the problem. This is the failure mode the "
                       "material exists to test."},
            {"key": "denominator",
             "label": "The 92% with the missing denominator",
             "where": "Material 5: Reel Share Rate 92%, \"calculated only "
                      "over reels a parent opened\", with the analytics note "
                      "below it saying roughly 30% of generated reels are "
                      "never opened, often because the thumbnail shows the "
                      "wrong kid",
             "criteria": ("roadmap",),
             "caught": "The 92% is not taken as health. Better still, the "
                       "unopened 30% is connected to the tagging tickets -- "
                       "the analytics note makes the link explicitly, and "
                       "following it is the strongest possible case for what "
                       "to build next.",
             "missed": "92% cited uncritically as evidence the product is "
                       "healthy. The caveat is printed directly beneath the "
                       "number."},
        ),
        "criteria": (
            {"key": "spec", "label": "The spec (Task 2)",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Outcome, in and out of scope, the human-in-the-loop "
                    "point, and acceptance criteria all specific and "
                    "testable. The top two failure modes are real ones, each "
                    "with a stated way it would be caught.",
                 3: "Covers the required elements but the acceptance criteria "
                    "or failure modes are generic.",
                 1: "A required element is missing, or the acceptance criteria "
                    "are not testable by anyone but the author.",
             }},
            {"key": "prototype", "label": "Prototype (Task 3)",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Runs or clicks through end to end, the core flow works, "
                    "and it matches the spec.",
                 3: "Runs or clicks partially; one step is incomplete or "
                    "rough.",
                 1: "No working prototype -- static frames, a mockup image, "
                    "or a written description only.",
             }},
            {"key": "roadmap", "label": "The roadmap call (Task 1)",
             "block": "spike", "weight": 25,
             "anchors": {
                 5: "Evidence-based ranking that connects the tagging "
                    "tickets, the unopened-reel share inside the 92%, and the "
                    "say-do gap into one coherent case. Names at least one "
                    "thing not to build and why, and handles Carla by naming "
                    "the need underneath her ask.",
                 3: "Reasonable ranking that correctly deprioritizes the "
                    "noise, but treats the materials as separate decisions.",
                 1: "Prioritizes by the loudest voice or by survey preference "
                    "alone, or ranks the app icon or editing controls "
                    "seriously.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "Clearly walks the roadmap call, the spec and the working "
                    "prototype, demoed running. Closes with why this role, "
                    "why Ajaia and salary expectations stated plainly.",
                 3: "Covers the required content but is generic, or the "
                    "reasoning is thin.",
                 1: "Missing required content, or does not demo the prototype "
                    "running.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 5,
             "anchors": {
                 5: "Specific tool use, and a real example of something "
                    "checked or rejected.",
                 3: "Generic description without a concrete example.",
                 1: "Missing, or purely performative.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. Never score 1 solely for "
                     "blank links.",
             "anchors": {
                 5: "Resume, links or public work clearly show shipped "
                    "product decisions and outcomes relevant to the seat.",
                 3: "Reasonable background, or no links with a plausible "
                    "resume for the seat.",
                 1: "Resume or links actively contradict fit. Never score 1 "
                    "solely for blank links.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers where the pack supplied them: usage "
            "figures not in Materials 2 or 5, ticket contents not in Material "
            "3, or an account value other than $180k a season.",
            "Confirmed fraud or misrepresentation. Routes to the fraud log, "
            "not to grading.",
        ),
        "red_flags": (
            "The 92% quoted as evidence of health with the analytics note "
            "unmentioned.",
            "Livestreaming committed to with a date because the account is "
            "big.",
            "A prototype that looks good in screenshots but does not run or "
            "click when demoed. Nothing rescues this; the task says static "
            "frames alone are not the task.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, posting phrasing "
                                      "handed back."},
            {"key": "complete", "label": "Complete: a ranked roadmap call "
                                         "with a named not-next item, the "
                                         "spec, a prototype link or file, the "
                                         "AI Workflow Note, and a video."},
            {"key": "runs", "label": "The prototype actually runs or clicks. "
                                     "Ask once for a working link before "
                                     "counting this against them."},
            {"key": "tagging", "label": "The three tagging tickets are read "
                                        "as one root cause."},
            {"key": "denominator", "label": "The 92% is not cited as health "
                                            "without its denominator "
                                            "caveat."},
            {"key": "not_next", "label": "At least one thing is explicitly "
                                         "named as not-next, with a reason."},
        ),
        "tells": {
            "strong": "The unopened 30% and the jersey-number tickets named "
                      "in the same sentence.",
            "weak": "\"58% of parents asked for editing controls, so that is "
                    "what we build.\"",
        },
        "do_not_penalize": (
            "Going over the 120 minutes.",
            "Building the prototype with AI. The task names v0, Lovable, "
            "Replit, Bolt and Claude by name and invites any tool at all.",
            "A rough-looking prototype. It has to run; it does not have to be "
            "designed. Visual design is explicitly not this seat's call.",
            "Choosing to build something other than tagging, when the case is "
            "argued from the materials. A candidate who ranks the unopened "
            "reels problem, or notification timing, and argues it from the "
            "same evidence has done the task.",
            "Prose that reads as AI-written.",
            "An empty optional prior-work section.",
        ),
        "gia": {
            "primary": ("Reasoning", "Number Speed and Accuracy"),
            "secondary": ("Word Meaning",),
            "why": "Reasoning because the pack is a synthesis problem "
                   "disguised as five separate inputs. Number Speed and "
                   "Accuracy because the decisive move is noticing what a "
                   "percentage was calculated over -- 92% of opened reels is "
                   "not 92% of reels. Ajaia administers no instrument, so "
                   "only the proxies below are live.",
            "proxies": (
                "Whether the 30% and the tagging tickets are connected.",
                "Whether behaviour is weighed against the survey, with "
                "numbers.",
                "How the $180k is handled: named as a risk, or allowed to set "
                "the roadmap.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 1 first. It is the spike and it is where the pack is "
                "won (8 min).",
                "Task 2 against Task 1 -- is this the spec for what they "
                "ranked first (7).",
                "Task 3: open it and click it. Do not judge from screenshots "
                "(6).",
                "Video, with the prototype demo watched rather than skipped "
                "(10).",
                "Task 4 and the optional prior work (3).",
            ),
            "calibration": "The floor is noticing the tagging issue is real "
                           "and the app icon is not worth building. The "
                           "separation is connecting the tagging tickets, the "
                           "unopened-reel share inside the 92%, and the "
                           "say-do gap into one coherent case rather than "
                           "treating each material as isolated. Also how "
                           "Carla is handled, whether the spec has testable "
                           "acceptance criteria and named failure modes, and "
                           "whether the prototype actually runs. A polished "
                           "video does not rescue a prototype that does not "
                           "click.",
            "probes": (
                "\"Carla reads your answer and asks what she tells her board "
                "on Monday. What is the sentence?\"",
                "\"Your fix ships and the unopened share does not move. What "
                "was wrong with the theory?\"",
                "\"Which of your acceptance criteria would you drop if you "
                "had to ship a week earlier?\"",
            ),
        },
        "notes": (
            "Open the prototype before scoring anything else on it. This is "
            "the row most often marked from a screenshot, and the 1 anchor "
            "exists precisely for submissions that look finished and are not.",
            "The spec is scored against the candidate's own roadmap call, not "
            "against ours. A coherent spec for a defensibly-ranked second "
            "choice beats an incoherent spec for the one we would have "
            "picked.",
            "Use 4 and 2 often. Three rows at 25 mean a single mark moves the "
            "total by 5 points at a time; clustering at 3 and 5 compresses "
            "the pool.",
        ),
        "gaps": (
            "No pricing, no discovery with real users, no engineering "
            "negotiation, and no exposure to the coach side of the product at "
            "all -- every material here is parent-facing or commercial.",
            "The prototype is scored on whether it runs, not on whether it is "
            "good, which is the honest limit of a 35-minute build.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },

    # -- 22. Client Delivery Lead --------------------------------------------
    #
    # Portal assignment 46, slug `client-delivery-lead`. Three accounts, one
    # week, and a pack built so that the account already on fire is not the
    # one that most needs the candidate's attention.
    #
    # 45 / 10 / 10 / 10 / 25. The portfolio call is the spike because Task 1
    # asks for the one account they lose sleep over AND the tradeoff being
    # made, and the instruction that came with this rubric names refusing to
    # answer that as "the single most common failure mode on this
    # assessment". Everything else in the pack can be done well by a careful
    # operator; naming a tradeoff is the part that requires a spine.
    {
        "key": "client_delivery_lead",
        "unit": "Client Delivery Lead",
        "grid_name": "Client Delivery Lead",
        "entity": "Ajaia",
        "slugs": ("client-delivery-lead",),
        "roles": ("Client Delivery Lead (2CF95851A3)",),
        "assessment": "Ajaia Client Delivery Lead Assessment, Core "
                      "Assignment, 120 minutes including the video",
        "location": "Remote",
        "spike": "Naming the tradeoff",
        "block_points": {
            "work_product": 45,
            "background": 10,
            "ai_forwardness": 10,
            "communication": 10,
            "spike": 25,
        },
        "universal_auto_fails": False,
        "seat": "Three fictional composite accounts in one week: Bellhaven "
                "green with Phase 2 kicking off in two weeks, Kestrel at risk "
                "with a deliverable due at 9am tomorrow and auditors on "
                "Thursday, and Vantage live six weeks with adoption stuck at "
                "30%. The seat is deciding where the week goes and saying so "
                "out loud.",
        "core_skill": "Reading past a status label, and being willing to say "
                      "what is being given up.",
        "seeded": (
            {"key": "bellhaven_green",
             "label": "Green does not mean fully staffed",
             "where": "Material 1: Bellhaven is reported Green with 1.5 "
                      "engineers allocated against 3 needed, Phase 2 kickoff "
                      "in two weeks, and a note that Phase 2 scope assumes "
                      "full staffing from week 1",
             "criteria": ("portfolio",),
             "caught": "The staffing gap is flagged before it becomes a fire. "
                       "Reading past the status label to the grid underneath "
                       "it is the judgment this material tests.",
             "missed": "Only the two accounts already burning are addressed. "
                       "The one still preventable is the one that was "
                       "missed."},
            {"key": "phi_overreach",
             "label": "The PHI overreach in the Kestrel export",
             "where": "Material 2 includes Patient Social Security Number and "
                      "Patient home address; Material 3 has Alicia saying the "
                      "auditors need \"enough to trace a sample of claims end "
                      "to end, nothing more, that was very clear\"",
             "criteria": ("quality",),
             "caught": "The two offending fields are named exactly. This is a "
                       "specific, concrete defect and the export target is an "
                       "external, read-only portal outside Kestrel's own "
                       "systems.",
             "missed": "A vague \"data quality concern\" or \"we should "
                       "review the PHI\" with no field named. Naming them is "
                       "the difference between spotting it and sensing it."},
            {"key": "deadline_pressure",
             "label": "The deadline pressure test",
             "where": "9am tomorrow, auditors Thursday, and Alicia asking for "
                      "it tonight if possible",
             "criteria": ("quality",),
             "caught": "A fast, scoped fix -- drop the two fields, confirm "
                       "the rest still lets auditors trace a sample end to "
                       "end -- plus a calm, honest message to Alicia. The "
                       "urgency is real and the fix is small.",
             "missed": "Shipping as-is, which prioritizes the date over a "
                       "named compliance problem, or an unscoped delay with "
                       "no client message at all."},
            {"key": "jae_kim_trade",
             "label": "The quiet resourcing trade",
             "where": "Material 5: Jae Kim asks to be pulled onto Kestrel "
                      "tomorrow morning, is supposed to be finishing "
                      "Bellhaven's Phase 2 kickoff deck due Thursday, and "
                      "Dani says it should only be a few hours -- nobody "
                      "mentions Bellhaven's 1.5 of 3",
             "criteria": ("portfolio",),
             "caught": "The move is connected to its Bellhaven consequence, "
                       "whether it is approved or not. Approving it is "
                       "defensible; approving it silently is not.",
             "missed": "Approved with no mention of what it does to an "
                       "account already at half staffing. The portfolio call "
                       "and the resourcing grid were read as separate "
                       "documents."},
            {"key": "adoption_red_herring",
             "label": "The adoption red herring",
             "where": "Material 4: training completion is already 90%, while "
                      "night shift sits at 4% against day shift 62%, "
                      "dispatchers over 10 years at 6% against under-2s at "
                      "55%, and 8 of 11 open tickets describe bad route "
                      "recommendations on weather-affected lanes",
             "criteria": ("adoption",),
             "caught": "The concentration is diagnosed -- night shift and the "
                       "most tenured dispatchers -- and tied to the routing "
                       "complaints. Trust was broken by specific bad "
                       "recommendations, not by unfamiliarity.",
             "missed": "A generic re-training push, which the 90% completion "
                       "figure already rules out as the primary fix."},
            {"key": "tradeoff_test",
             "label": "The tradeoff test",
             "where": "Task 1: name the one account you lose sleep over, and "
                      "state the tradeoff you are making",
             "criteria": ("portfolio",),
             "caught": "One account named, and what is being given up said "
                       "plainly.",
             "missed": "Three equally-weighted responses with no named "
                       "tradeoff. This is the single most common failure mode "
                       "on this assessment, and a beautifully organized plan "
                       "for all three accounts has not cleared Task 1."},
        ),
        "criteria": (
            {"key": "quality", "label": "Quality decision (Task 2)",
             "block": "work_product", "weight": 25,
             "anchors": {
                 5: "Identifies the specific overreaching fields, makes a "
                    "scoped ship, fix or delay call, and writes a calm, "
                    "honest, specific client message in full.",
                 3: "Recognizes a general quality problem and proposes a "
                    "reasonable fix, but does not name the exact fields, or "
                    "the message is generic.",
                 1: "Ships as-is, delays without a client message, or cannot "
                    "articulate what is actually wrong.",
             }},
            {"key": "adoption", "label": "Adoption play (Task 3)",
             "block": "work_product", "weight": 20,
             "anchors": {
                 5: "Diagnoses the concentration by shift and tenure, ties it "
                    "to the routing complaints, and proposes a two-week plan "
                    "that is not primarily more training, naming who does "
                    "what.",
                 3: "Reasonable two-week plan but treats the problem as "
                    "uniform rather than concentrated.",
                 1: "Defaults to a generic training push despite the 90% "
                    "completion figure already given.",
             }},
            {"key": "portfolio", "label": "Portfolio call (Task 1)",
             "block": "spike", "weight": 25,
             "anchors": {
                 5: "Names one account as the priority with a stated "
                    "tradeoff, catches Bellhaven's staffing gap under the "
                    "green label, and connects the Jae Kim move to its "
                    "Bellhaven consequence.",
                 3: "Reasonable actions for all three accounts but no clear "
                    "priority or tradeoff named.",
                 1: "Treats all three as equally urgent, misses the Bellhaven "
                    "staffing gap, or ignores the resourcing conflict "
                    "entirely.",
             }},
            {"key": "video", "label": "Video",
             "block": "communication", "weight": 10,
             "anchors": {
                 5: "The walkthrough matches the written plan exactly, the "
                    "reasoning is explained conversationally as if briefing a "
                    "manager rather than read from a script, and it closes "
                    "with a specific why-Ajaia and a stated salary figure.",
                 3: "Accurate but reads like a script, or the closing is "
                    "generic.",
                 1: "Contradicts what was written, or omits salary "
                    "expectations.",
             }},
            {"key": "ai_workflow_note", "label": "AI Workflow Note (Task 4)",
             "block": "ai_forwardness", "weight": 10,
             "anchors": {
                 5: "Specific about what AI drafted versus what stayed human, "
                    "with a concrete example of something checked or "
                    "rejected.",
                 3: "Generic AI use described without a specific example.",
                 1: "Missing, or claims no AI use on an AI-native task.",
             }},
            {"key": "experience",
             "label": "Experience and public presence",
             "block": "background", "weight": 10,
             "note": "Always adds, never blocks. A blank optional section is "
                     "a 3.",
             "anchors": {
                 5: "Resume or links show real ownership of delivery across "
                    "multiple concurrent accounts, with material reflecting "
                    "sound judgment.",
                 3: "Solid resume with no additional material, or neutral "
                    "links.",
                 1: "Provided material raises a specific concern, such as "
                    "contradicting a claim made elsewhere in the submission.",
             }},
        ),
        "auto_fails": (
            "Fabricated facts or numbers where the pack supplied them: "
            "staffing figures not in Material 1, adoption percentages not in "
            "Material 4, or export fields not in Material 2.",
            "Confirmed fraud or misrepresentation. Routes to the fraud log, "
            "not to grading.",
        ),
        "red_flags": (
            "A client message that describes what would be said rather than "
            "saying it. Task 2 asks for the message in full and says so "
            "twice.",
            "Patient SSN or home address left in a scoped export to an "
            "external read-only auditor portal, with no comment.",
            "A video that states a different decision than the written "
            "tasks. Flag it as a consistency concern rather than averaging "
            "the two.",
        ),
        "triage": (
            {"key": "fraud", "label": "No fraud tells: burner domain, "
                                      "automated apply, posting phrasing "
                                      "handed back."},
            {"key": "complete", "label": "Complete: the portfolio call, the "
                                         "quality decision with the client "
                                         "message written out in full, the "
                                         "two-week adoption plan, the AI "
                                         "Workflow Note, and a video."},
            {"key": "opens", "label": "Every link opens and the video plays. "
                                      "Ask once before counting a dead link "
                                      "against them."},
            {"key": "tradeoff", "label": "One account is named as the one "
                                         "they lose sleep over, with a stated "
                                         "tradeoff."},
            {"key": "fields", "label": "The two overreaching Kestrel fields "
                                       "are named specifically."},
            {"key": "concentration", "label": "The Vantage plan addresses "
                                              "night shift and tenured "
                                              "dispatchers rather than "
                                              "training in general."},
        ),
        "tells": {
            "strong": "\"I am prioritizing Kestrel this week and Bellhaven's "
                      "kickoff deck slips to Monday\" -- the choice and its "
                      "cost in one line.",
            "weak": "A tidy three-column plan with no priority in it.",
        },
        "do_not_penalize": (
            "Going over the 120 minutes.",
            "Choosing to ship Kestrel with the fields removed and a caveat, "
            "or to delay by half a day. Both are defensible; what is scored "
            "is whether the fields were named and whether Alicia was told.",
            "Losing sleep over Bellhaven rather than Kestrel. Kestrel is "
            "urgent and Bellhaven is the one nobody is watching; either is a "
            "5 when the tradeoff is stated.",
            "A blunt client message. Calm and honest is the standard, not "
            "diplomatic.",
            "Prose that reads as AI-written.",
            "An empty optional prior-work section.",
        ),
        "gia": {
            "primary": ("Reasoning", "Perceptual Speed"),
            "secondary": ("Word Meaning",),
            "why": "Reasoning for the portfolio call and for spotting that a "
                   "green label and a staffing grid disagree. Perceptual "
                   "Speed because two of the three planted items are found by "
                   "reading a table and a field list carefully rather than by "
                   "thinking hard. Word Meaning for the client message, which "
                   "is the only real writing in the pack. Ajaia administers "
                   "no instrument, so only the proxies below are live.",
            "proxies": (
                "Whether the two PHI fields are named or gestured at.",
                "Whether Bellhaven appears in the answer at all.",
                "Whether the adoption plan segments the population or treats "
                "it as one.",
            ),
        },
        "reviewer": {
            "path": (
                "Task 1, looking for one named account and one stated "
                "tradeoff before anything else (5 min).",
                "Task 2, checking the field list against Material 2, then "
                "reading the client message as Alicia would (7).",
                "Task 3 against Material 4's segments (5).",
                "Video, checked against the written tasks for consistency "
                "(10).",
                "Task 4 and the optional prior work (3).",
            ),
            "calibration": "The floor is flagging that Kestrel has a "
                           "due-tomorrow problem and Vantage's adoption is "
                           "low. The separation is naming the exact wrong "
                           "fields rather than a general quality worry, "
                           "catching that Bellhaven's green status hides a "
                           "staffing gap, an adoption play aimed at night "
                           "shift and veteran dispatchers specifically, and "
                           "an actual named tradeoff in Task 1. Check the "
                           "video against the written tasks: a candidate "
                           "describing a different decision on camera than "
                           "what is written down is a consistency problem "
                           "worth flagging.",
            "probes": (
                "\"Alicia replies that the auditors specifically asked for "
                "the SSN. What now?\"",
                "\"You pulled Jae Kim. Bellhaven's kickoff slips. What do you "
                "tell Bellhaven, and when?\"",
                "\"Night shift is at 4%. What is the first thing you do that "
                "is not a training session?\"",
            ),
        },
        "notes": (
            "Score Task 1 on whether a choice was made, not on which choice. "
            "All three accounts have a defensible case; the failure mode is "
            "declining to choose, and it is common enough that it is worth "
            "checking for before reading the rest.",
            "The client message is a writing sample and the only one in this "
            "pack. Read it as Alicia would, not as a reviewer: it is going to "
            "someone who has already told you what her auditors need and who "
            "is expecting a file tonight.",
            "Use 4 and 2 often. Two rows at 25 and one at 20 mean the middle "
            "of the scale carries most of the ranking here.",
        ),
        "gaps": (
            "No commercial work: no renewal conversation, no scoping, no "
            "margin, and no exposure to the sales relationship that a "
            "delivery lead lives beside.",
            "One week of one portfolio. Nothing tests what this candidate is "
            "like in month six, which is when delivery seats are actually "
            "decided.",
            "Ajaia administers no formal GIA instrument today, so only the "
            "proxy signals above are live.",
        ),
    },
)


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
