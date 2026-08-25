"""
Configuration for the assessment reminder system.
All secrets come from environment variables (or a .env file).
"""

import os
from pathlib import Path

# The repository root -- this file is backend/core/config.py, so the project is
# two directories up. Every path in the project is resolved from here rather
# than from the current working directory, so a cron entry, a systemd unit and
# a shell sitting anywhere all find the same .env, assessments/ and state/.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


# --- Workable ---
WORKABLE_API_TOKEN = os.environ.get("WORKABLE_API_TOKEN", "").strip()
WORKABLE_BASE_URL = "https://ajaia.workable.com/spi/v3"
# The documented limit is 10 requests / 10 s, but the API returns 429 at
# exactly that rate. Stay under it, and retry on 429 regardless.
WORKABLE_RATE_LIMIT = 8
WORKABLE_MAX_RETRIES = 4

# --- Assessment Portal ---
PORTAL_BASE_URL = "https://candidateassessments.ajaia.ai"
PORTAL_LOGIN_URL = f"{PORTAL_BASE_URL}/admin/login"
PORTAL_EMAIL = os.environ.get("PORTAL_EMAIL", "")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "")
# The portal's own "Export CSV" endpoint. One request returns every submission
# with real columns -- far more reliable than scraping the dashboard HTML,
# which is paginated and renders only ~200 of the rows.
PORTAL_COMPANY_ID = "1"
PORTAL_SUBMISSIONS_CSV = (
    f"{PORTAL_BASE_URL}/admin/companies/{PORTAL_COMPANY_ID}/submissions.csv"
)

# THE SAME ENDPOINT, WITHOUT ITS HIDDEN FILTER.
#
# The bare URL above is not the full export. It quietly returns only rows still
# at review_status "new" -- measured 13 Aug 2026, 4,460 rows out of 8,606. The
# other 4,146 are everyone a reviewer has touched: pending 2,646, rejected
# 1,117, reviewed 300, interview 83. Nothing in the response says so; it is a
# 200 with a plausible row count, which is why this went unnoticed.
#
# For reminders that omission is the worst kind. A candidate who submits and is
# moved to Pending Review vanishes from the export, so the next run cannot tell
# them apart from someone who never opened the assessment -- and emails them a
# reminder to do work they have already done.
#
# `review_status=all` is a true superset: every id in the default export is
# present, no duplicates. Verified against the three Pending Review candidates
# on the dashboard, all absent from the default export and all present here.
#
# An unrecognised value silently falls back to the filtered default rather than
# erroring, so do not "fix" this string casually -- ?review_status=pending_review
# returns the same 4,460 rows as no filter at all.
#
# In practice portal_scraper.py rarely fetches this URL: the combined body is
# ~40 MB and the portal drops it part-way through most of the time, so the
# export is normally reassembled one review state at a time and this is only
# the fallback. See REVIEW_BUCKETS there.
PORTAL_SUBMISSIONS_CSV_ALL = f"{PORTAL_SUBMISSIONS_CSV}?review_status=all"

# WHAT A CREDIBLE EXPORT LOOKS LIKE.
#
# A 200 is not evidence that the export worked. An expired portal session
# answers 200 with an HTML login page; a bucket whose review_status the portal
# stopped recognising answers 200 with a valid CSV containing zero rows. Both
# parse without complaint, and both mean the same thing downstream: candidates
# who ARE on the portal are missing from the record set, so they look like they
# never started, so the next run emails them.
#
# That is the whole failure mode, and it is silent in every direction -- the
# scrape "succeeds", the counts look plausible for a quiet week, and the only
# signal is the mail going out. So the export is checked for being credible,
# not merely for having arrived.

# Columns every export must carry. Their absence means the body is not the
# export at all -- an HTML login page, an error document, or a schema that has
# moved under us. Checked before a single row is trusted.
PORTAL_REQUIRED_COLUMNS = ("candidate_email", "job_id", "submission_status")

# Largest request body the dashboard will read. Hardening rather than a fix for
# a demonstrated hole: the two endpoints a stranger can reach both refuse
# before reading a body -- the login throttle answers 429, and a review link is
# rejected on its token -- so there is no known way to make this process buffer
# a large upload. It is set anyway because that is a property of today's
# routes, not a rule anything enforces, and the next endpoint somebody adds
# inherits the limit instead of having to remember it.
#
# Nothing here uploads a file. Every request is JSON of a few kilobytes at
# most, so 1 MB is generous by three orders of magnitude. Raise it deliberately
# if a route ever does accept an upload.
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", 1024 * 1024))

# The smallest total row count that could plausibly be the real export. Well
# below the ~8,600 rows seen in practice: this is a floor against catastrophe
# (an export that returns almost nothing), not a tuned threshold.
PORTAL_MIN_TOTAL_ROWS = int(os.environ.get("PORTAL_MIN_TOTAL_ROWS", "100"))

# How much of a bucket may disappear between runs before the fetch is treated
# as broken rather than as news.
#
# THIS IS THE CHECK THAT CATCHES THE REAL CASE. An absolute floor does not: the
# "interview" bucket holds ~83 rows, so it could empty completely without
# moving a total-row threshold, and those candidates would be mailed. Comparing
# each bucket against what it held last time catches a bucket that quietly
# stops returning rows, at any size.
#
# 0.5 means a bucket may lose up to half its rows -- generous, because real
# movement between buckets is normal -- and a bucket that had rows and now has
# none is always treated as broken, whatever this is set to.
PORTAL_BUCKET_DROP_TOLERANCE = float(
    os.environ.get("PORTAL_BUCKET_DROP_TOLERANCE", "0.5"))

# Which of the portal's review queues ingest.py pulls into Mongo for grading.
#
# "new" is the untouched queue -- nobody has looked at these. "pending" is the
# portal's Pending Review section: the candidate submitted, a reviewer moved
# them into the queue, and no verdict has been recorded yet. Both are ungraded
# work, so both are evaluated.
#
# The three states left out are deliberate, not an oversight. rejected,
# reviewed and interview all carry a verdict a human already reached, and
# grading them would spend tokens re-deciding ~1,500 settled candidates. Add
# one here only if you actually want it re-scored.
#
# Every value must appear in portal_scraper.REVIEW_BUCKETS -- an unrecognised
# review_status does not error, it silently returns the default "new" rows, so
# a typo here would look like a successful run that graded the same queue
# twice. ingest.py validates against that list before fetching.
INGEST_REVIEW_BUCKETS = ("new", "pending")

# Admin pages the CSV export does not cover: the roles themselves and the
# assessment text each one is graded against. See portal_crawler.py.
# Two requests per role across ~26 roles, so pace them against a small app.
PORTAL_CRAWL_DELAY = 0.4         # seconds between assignment page fetches

# --- MongoDB ---
# Stores every submission (including the full answer markdown), each role's
# live assessment, and our own accept/reject decisions and AI evaluations.
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB = os.environ.get("MONGO_DB", "assessment-evaluation")

# --- Screening rules ---
# A submission missing either artefact is auto-rejected without ever reaching
# the model: there is nothing to review, and grading it would spend tokens to
# produce a score nobody can act on.
#
# Measured against all 1752 submitted records on 2026-08-10: every one had a
# resume link and 333 (19%) had no video. So today this rule is in practice
# "no video, no review" -- but both artefacts are checked, because a missing
# resume should fail just as loudly if the form ever stops requiring one.
REQUIRED_ARTEFACTS = ("video_link", "resume_link")

# The same rule for a role that never asked for a video.
#
# A CV-only posting collects one artefact, because one artefact is all it asks
# for. Applying the pair above to those candidates would auto-reject every one
# of them for `missing_video` before the grader ever saw them -- a rejection
# for not submitting something nobody requested.
#
# This is the whole reason REQUIRED_ARTEFACTS is now read through a function
# rather than imported as a constant. See `required_artefacts_for`.
CV_ONLY_REQUIRED_ARTEFACTS = ("resume_link",)

# --- AI evaluation ---
# Provider-agnostic, OpenAI-compatible chat-completions. Set LLM_BASE_URL and
# LLM_MODEL for whichever provider you land on (Groq, Together, OpenRouter, a
# local llama.cpp server); nothing below is vendor-specific.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
# Reasoning models think before they answer, and those tokens come out of the
# same output budget as the JSON. Sent only when set, because a model without a
# reasoning mode rejects the parameter outright -- so this moves with LLM_MODEL
# in .env rather than carrying a default that only suits one provider.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "").strip()
# Output reservation for one grading call. 2,950 covers the JSON schema itself
# (seven criteria carrying a quote, a missing-list and evidence, six triage
# notes, the GIA read and the brief); gpt-oss-120b spent a further 576 on
# reasoning at low effort. Raising this buys safety margin and costs queue
# depth -- Groq counts the reservation, not the usage, against both the minute
# and the day.
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "3600"))
# Two clocks, because a stalled call and a slow one are different faults.
#
# Some providers -- NVIDIA's free build-tier endpoint especially -- accept a
# request and then never schedule it. Measured over a dozen calls there, the
# first token arrived somewhere between 37s and 159s, or it never arrived at
# all; nothing landed in between. So the useful question is not "how long has
# this call taken" but "has it said anything yet", and a single flat timeout
# cannot ask it: raise the figure and every corpse costs the full wait, lower
# it and you cut off requests that were writing perfectly well.
#
# LLM_TTFT_TIMEOUT is the silence budget -- how long a call may say nothing
# before it is written off as never scheduled. It only applies until the first
# token lands, so it can be tight without endangering a live generation.
# Measured on this endpoint with a real grading prompt (~5,900 tokens in,
# 3,600 reserved out): first token at 146s, then 24s to write the verdict.
# Small test prompts came back in 37-50s, which is why this wants calibrating
# against a genuine call and not a toy one -- a budget set from the toy figure
# cuts off every healthy request before it speaks.
LLM_TTFT_TIMEOUT = float(os.environ.get("LLM_TTFT_TIMEOUT", "180"))
# ...and once the tokens are flowing, this caps the whole reply, so a stream
# that stalls half way through still ends.
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "180"))
# More attempts, because each one is now cheap. Abandoning a stalled request
# and sending a fresh one is a new draw against the same queue, and on this
# endpoint most candidates land on the first or second.
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "4"))
# Longest a single retry will wait when the provider says "come back later".
# Free tiers answer a per-minute overrun with a few seconds and an exhausted
# daily quota with 40+ minutes; the first is worth sleeping through, the second
# is not. Past this, evaluator.py stops and says how long the quota has left
# rather than burning its retries on a wait it was never going to honour.
LLM_MAX_BACKOFF = float(os.environ.get("LLM_MAX_BACKOFF", "120"))
# Grading calls in flight at once.
#
# Worth more here than anywhere else: measured against this endpoint, parallel
# requests do not slow each other down -- four in flight each answered in the
# time one would have taken alone, and the fast ones stayed fast at eight. The
# queue wait is per-request and mostly idle, so concurrency buys close to
# linear throughput. Past about six the share that never gets scheduled climbs,
# so this stops where the returns do. Drop it to 1 on a metered per-minute
# tier, where the constraint is tokens rather than latency.
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "4"))
# Answers run to 129k characters at the extreme. Truncate before sending so a
# single outlier cannot blow the context window or the bill.
MAX_ANSWER_CHARS = 60_000

# How much of a candidate's CV goes into the grading prompt.
#
# Lower than the 8,000 characters resume_reader stores, deliberately. The CV is
# background for reading the answer, not the thing being marked, so it does not
# earn the same room -- and it is paid for on every single call, where a long
# answer is one candidate's cost. 4,000 characters covers a two-page CV in full;
# past that a resume is repeating itself in a skills matrix.
RESUME_PROMPT_CHARS = int(os.environ.get("RESUME_PROMPT_CHARS", "4000"))

# The same figure for a seat where the CV is the whole submission.
#
# 4,000 is right when the CV is background to an assessment: it is paid for on
# every call and it is not the thing being marked, so the tail of a long one is
# the first thing to cut. On a CV-only role that reasoning inverts. There is no
# answer sharing the context window, the tail of the CV is where the early
# career sits -- which is exactly what a "built it once already" row needs to
# read -- and truncating it would mark a candidate on a document we chose not
# to finish. 8,000 is what resume_reader stores, so this takes all of it.
CV_ONLY_PROMPT_CHARS = int(os.environ.get("CV_ONLY_PROMPT_CHARS", "8000"))

# How the two documents split the final score, seat by seat.
#
# Every candidate is marked twice, out of 100 each. The assessment is marked
# against its family grid in rubric_pack.py; the CV is marked against its own
# three criteria in evaluator.CV_CRITERIA, judged against the same seat. This
# table is the only thing that decides how those two hundreds combine.
#
# It replaces a flat 0.50 everywhere, which asserted that a four-hour full-stack
# build and a ninety-minute Customer Success plan carry the same share of the
# hiring decision. They do not. The question each row answers is: for THIS seat,
# how much of the signal is the track record, and how much is the work sample?
#
# Three readings move a seat toward the CV:
#
#   * the assessment is narrow against the job. Customer Success runs 90 minutes
#     against a seat owning "more than 80,000 students, 5,000 teachers, and 100
#     schools"; Implementation is a 60-minute working session.
#   * the job's value is accumulated rather than demonstrable in an afternoon --
#     a deal sheet, a network, years of incident command, standing with a board.
#   * the seat is accountable rather than productive. Nobody's first security
#     program should be ours.
#
# And three move it toward the assessment:
#
#   * the assessment IS the job in miniature: a build, a campaign assembled to
#     real character limits, a workflow reconstructed from a real transcript.
#   * the JD discounts credentials outright -- Marketing's "certification is a
#     baseline, not proof of skill" is the clearest case in the pack.
#   * the seat hires on aptitude rather than record. Weighting a fellowship's CV
#     heavily just re-ranks applicants by how long they have been working, which
#     is the opposite of what a fellowship is selecting for.
#
# Two things this table deliberately does NOT do. It does not re-weight any
# grid: the pack's criteria still sum to 100 and validate_grid still enforces
# it, so `rubric_score` stays comparable across every candidate in a family
# whatever their seat's split. And it never lets the CV touch a grid criterion
# -- that is the same evidence counted twice, and evaluator's prompt forbids it
# in as many words.
#
# Keys are portal assessment slugs first, then pack grid keys, so a pack family
# and a derived grid each land on their own number. Anything unlisted falls back
# to CV_WEIGHT_DEFAULT. The value is the CV's share; the assessment carries the
# rest.
CV_WEIGHT_BY_SEAT = {

    # -- Scored inside the grid, so zero here -------------------------------
    # This is not "the CV does not matter on these seats". It is the opposite:
    # their rubrics score the record as a criterion IN the grid, with anchors,
    # so it is already paid for by the time the blend runs. Three grids in the
    # pack do that -- the two AI Strategist ones at 40 of 100 points, and the
    # Social Media and Marketing Intern one at 10 -- and they are the only
    # grids that open rubric_pack's `background` block.
    #
    # So the blend has to be off. Every other seat here reads "how much of the
    # decision is the resume", and the resume is marked separately in
    # `cv_assessment` and folded in afterwards. Leave one of these seats at the
    # 0.50 default and the track record is paid for twice -- once inside the
    # grid and again in the blend at 50 percent of the total -- which is the
    # exact error the whole split exists to prevent. 0.0 means the grid is the
    # score, which for these seats it is.
    #
    # Two consequences worth knowing. CV_MISSING_POLICY never fires here, so a
    # candidate with an unreadable CV is not capped at a ceiling; they land on
    # the background row's neutral 3 -- "background not stated anywhere also
    # scores 3, with a note" on the Strategist grids, "an empty field scores 3,
    # never 1" on the intern one -- which is each rubric's own instruction and
    # a far better answer. And `rubric_score` and `score` are the same number
    # on these cards.
    # Both AI Strategist grids, because both open the background block. The
    # associate one scores raw material rather than accomplishment, but it
    # scores it in the grid at the same 40 points, so the double-count it would
    # cause here is the same double-count.
    "ai_strategy": 0.0,
    "ai_strategy_associate": 0.0,
    "ai-strategist": 0.0,

    # The intern seat, where the reason for 0.0 is the mirror image of the
    # Strategist one. There the record is 40 points because it decides the
    # seat; here it is 10 because it must NOT decide the seat -- an intern pool
    # is mostly people with thin files, and the rubric's own words are "adds,
    # never blocks", "a candidate can advance at 75+ on work product alone".
    # A 0.50 blend would hand the portfolio back roughly half the decision and
    # undo that in one line, which is precisely the failure mode the rubric's
    # calibration section names: rewarding polish and pedigree.
    "social_marketing_intern": 0.0,
    "social-marketing-intern": 0.0,

    # The GM & Head of Growth seat, where 0.0 means something the three above
    # only half mean: there is no assessment at all. Grid 15 is 100 points of
    # background, so the CV is not scored inside the grid alongside a work
    # sample -- it IS the grid. A non-zero weight here would take a number
    # computed entirely from the resume and blend it with a second number
    # computed entirely from the same resume, which is not a weighting of two
    # documents but a rounding of one.
    "gm_growth": 0.0,
    "gm-head-of-growth": 0.0,

    # -- Assessment-dominant: the artefact is the job ----------------------
    # 4-6 hours of building, graded on a spike named "scope cut defense". What
    # someone shipped this week beats what they shipped in 2019.
    "full_stack": 0.25,
    "full-stack-developer-assignment": 0.25,
    "product-engineer": 0.25,
    "developer": 0.25,

    # "Certification is a baseline, not proof of skill" -- the JD's words. The
    # grid already spends 24 of its 70 on account evidence and campaign defense,
    # so the track record is inside the work sample rather than beside it.
    "marketing": 0.30,
    "marketing-advertising": 0.30,

    # Nine sub-deliverables from one hedged transcript in 120 minutes. Whether
    # someone can separate fact from inference shows here or nowhere; a CV of
    # analyst titles does not answer it.
    "analysts": 0.30,
    "workflow-analyst": 0.30,

    # Fellowships select for aptitude. A heavy CV weight here would rank
    # applicants by years served, which is what the seat exists not to do.
    "enterprise-ai-strategy-fellow": 0.30,
    "enterprise-ai-product-automation-fellow": 0.30,
    "founder-s-office-ai-consulting-fellow": 0.30,

    # These four assessments are build assignments whatever the title says --
    # ship a working slice in a timebox and defend the cuts.
    "remote-project-manager": 0.30,
    "technical-program-manager": 0.30,
    "senior-ai-native-designer": 0.30,
    "full-stack-designer": 0.30,

    # Method, not memory. The spike is evaluator independence, which is visible
    # in how the analysis is built. This is also where JD-echo spam is heaviest,
    # so the work sample is doing the separating.
    "research_data": 0.35,
    "data-scientist": 0.35,

    # Repurposing judgment is craft, and the assessment shows it directly.
    "social_media": 0.35,
    "social-media-manager": 0.35,

    # -- Balanced, tilted to the work -------------------------------------
    # The JD wants "a sophisticated track record in high-performance fields",
    # which is a CV fact -- but the assessment already asks for prior delivery
    # evidence and a live demo, so a quarter of that record is inside the grid.
    "ai_training": 0.40,
    "ai-trainer": 0.40,

    # One layer below the Chief of Staff and executional: the assessment is
    # the daily work -- synthesise the meeting, fix the calendar, build the pack.
    "exec_ops_associate": 0.40,
    "operations-associate": 0.40,

    "ai-solutions-architect": 0.40,
    "senior-product-and-brand-designer": 0.40,

    # Approved-stack ownership: which stacks someone has actually run, at what
    # size, is a resume fact the assessment can only ask about.
    "it_manager": 0.45,
    "it-manager": 0.45,

    "ai-delivery-lead": 0.45,
    "project-manager": 0.45,

    # -- Balanced, tilted to the record ------------------------------------
    # A 60-minute working session is the thinnest sample in the pack, and
    # school-calendar realism comes from having worked a school year.
    "implementation": 0.50,

    # Recruiting is a network and a placement history. The assessment tests
    # system design, which is half the seat.
    "recruitment-manager": 0.50,

    # -- Experience-dominant ------------------------------------------------
    # An accountability seat in a regulated domain. 180 minutes of writing
    # cannot show a decade of incident command, an audit survived, or a FERPA
    # program actually run.
    "it_security": 0.55,
    "director-it-ciso": 0.55,

    # A trust seat beside the CEO. "Influence without formal authority" is the
    # JD's phrase for something that accrues over a career and is asserted, not
    # demonstrated, in an assignment.
    "exec_ops_cos": 0.55,
    "chief-of-staff": 0.55,

    # 90 minutes against a seat that owns districts. Relationships with school
    # leaders and district IT, and having survived a rollout, are the job; the
    # assessment can only sample the writing.
    "customer_success": 0.60,
    "ce-product-success": 0.60,

    # A deal sheet, closed transactions and a network. None of the three can be
    # produced in a 120-minute exercise.
    "investments": 0.60,
    "investment-lead": 0.60,

    # Relationship capital, and no platform assessment exists at all -- the
    # family runs on a working sample today.
    "partnerships": 0.60,
}

# The split for a seat this table does not name.
#
# 0.50 keeps an unlisted role exactly where every role sat before the table
# existed, so adding a new assessment cannot silently change what its
# candidates were promised. Add the seat here once you know which way it leans.
# 0.0 restores the behaviour up to 2026-08-14-b, where the CV was read but
# scored nothing.
CV_WEIGHT_DEFAULT = float(os.environ.get("CV_SCORE_WEIGHT", "0.50"))

# One weight for every seat, ignoring the table above. Empty means the table
# rules. This exists for two jobs and no others: comparing a run against the
# old flat split, and reverting in one line if the per-seat weights turn out
# wrong, without editing thirty rows under time pressure.
CV_WEIGHT_OVERRIDE = os.environ.get("CV_WEIGHT_OVERRIDE", "").strip()


def cv_weight_for(grid=None, slug=None):
    """
    The CV's share of the final score for one seat, and where the number came
    from. Returns (weight, source).

    Resolution order is slug, then grid key, then the default. Slug first
    because it is the identifier that survives both kinds of grid: pack grids
    carry a tuple of them and a derived grid carries exactly one, while grid
    keys are `derived_<slug>` for anything the pack does not cover.

    `source` is stored on the verdict and shown on the dashboard. A reviewer
    disputing a score should be able to see whether the seat was weighted
    deliberately or fell through to the default.
    """
    if CV_WEIGHT_OVERRIDE:
        try:
            return max(0.0, min(1.0, float(CV_WEIGHT_OVERRIDE))), "override"
        except ValueError:
            pass

    slugs = [slug] if slug else []
    if grid:
        slugs.extend(s for s in (grid.get("slugs") or ()) if s not in slugs)
    for candidate in slugs:
        if candidate in CV_WEIGHT_BY_SEAT:
            return max(0.0, min(1.0, CV_WEIGHT_BY_SEAT[candidate])), "seat"

    key = (grid or {}).get("key")
    if key in CV_WEIGHT_BY_SEAT:
        return max(0.0, min(1.0, CV_WEIGHT_BY_SEAT[key])), "seat"

    return max(0.0, min(1.0, CV_WEIGHT_DEFAULT)), "default"


# Kept as the name the rest of the tree imported before the table existed, and
# as the number a caller with no seat in hand should use.
CV_SCORE_WEIGHT = CV_WEIGHT_DEFAULT

# What happens to the CV's share when there is no readable CV.
#
# This matters more than it looks, and it matters more again now that the
# weight varies by seat. 38% of candidates who submitted a link have no
# extractable text behind it -- a private Drive file, a LinkedIn profile page, a
# photographed CV with no text layer -- and none of that is anything they did.
#
#   forfeit   they lose the CV points, so their ceiling is (1-w)*100.
#   rescale   they are scored on the rubric alone, rescaled to 100, so they
#             stay comparable with everyone else.
#
# Under "forfeit" the ceiling is now a different number per family, and on the
# experience-dominant seats it is below the Good band, not merely below the
# advance bar:
#
#   w=0.25  ceiling 75   full stack, product engineer     -- exactly the bar
#   w=0.30  ceiling 70   marketing, analysts, fellowships -- Good, cannot advance
#   w=0.40  ceiling 60   AI training, ops associate       -- bottom of Good
#   w=0.50  ceiling 50   implementation, recruitment      -- Okay
#   w=0.55  ceiling 45   IT/security, chief of staff      -- Okay
#   w=0.60  ceiling 40   customer success, investments    -- Okay
#
# Read that column as a policy, because it is one: on a 0.60 seat a candidate
# whose Drive link was private is capped in the bottom band however good both
# documents are, for a failure of our extraction. "rescale" is one env var away
# and removes it -- CV_MISSING_POLICY=rescale -- and is the recommended setting
# now that the weights run this high. "forfeit" is retained as the default only
# because it was set by explicit instruction on 2026-08-14.
CV_MISSING_POLICY = os.environ.get("CV_MISSING_POLICY", "forfeit").strip().lower()

# --- Evaluation matrix ---
# The scoring architecture is the Ajaia Assessment Scoring Rubrics pack
# (version 2026-08-12), which lives in rubric_pack.py: 100 points in four fixed
# blocks -- Work product 70, AI-forwardness 10, Communication and judgment 10,
# and a named family spike 10 -- with each criterion rated 1 to 5 against
# behavioural anchors written from the real task content, then weighted
# (score x weight / 5) and summed.
#
# The criteria themselves are per-family rather than global, because the
# anchors are the point: "post-money $13.3M shown as $2M / 0.15" is a mark a
# second reviewer can check, and "shows domain craft" is not. What stays
# comparable across families is the four blocks and the bands -- Best 85+,
# Better 75-84, Good 60-74, Okay below 60. The advance bar sits at 75, where the
# interview system draws it, so an assessment score and an interview score still
# mean the same thing.
#
# rubric_pack.py refuses to load a grid whose weights do not sum to exactly
# 100, so a hand-edit cannot silently rescale a family's scores. Editing a
# weight still moves the bar for everyone in that family: re-grade it
# afterwards, or old scores will be compared against a standard that has moved.
# Evaluations record the grid version they were marked against so the dashboard
# can tell them apart.

# --- Partial grids ---
# What to do when the model marks some of the criteria and not the rest.
#
# It happens, and it happens for our reasons rather than the candidate's: the
# JSON runs past LLM_MAX_OUTPUT_TOKENS and the tail of the grid never gets
# written. The scorer renormalises what came back to 100 -- six rows out of
# seven is still a defensible read of the work -- and that renormalisation is
# exactly the trap. One row marked 5 renormalises to 100.0, and 100.0 is what
# a ranking sorts on, so a submission nobody actually graded lands at the top
# of a hiring manager's list with a number that looks like the best result in
# the round.
#
# Two settings, doing two different jobs:
#
#   GRID_MIN_COVERAGE   the floor below which the verdict is not a verdict.
#                       Measured in RUBRIC WEIGHT, not row count, because the
#                       rows are not worth the same -- a 40-point background
#                       row alone is 40% of the grid, four 5-point rows are
#                       20%. Under the floor the verdict is refused outright
#                       (EvaluationFailed), so the submission stays ungraded
#                       and the next grading run tries again. That is the right
#                       outcome: an ungraded candidate is visibly ungraded, a
#                       fake 100 is not.
#
#   SHORTLIST_REQUIRE_COMPLETE_GRID
#                       whether a verdict above the floor but still short of
#                       the full grid may be RANKED. Default true: it is shown
#                       to the recruiter, flagged as provisional, and held out
#                       of the shortlist until someone re-grades it. Rank is a
#                       claim that these people were compared, and a partial
#                       grid was not compared with anything.
#
# 0.5 as the floor is deliberately low. It is not a quality bar -- it is the
# line under which renormalising is arithmetic on nothing. Raising it toward
# 1.0 makes grading stricter and the retry rate higher; the honest way to cut
# partial grids is to raise LLM_MAX_OUTPUT_TOKENS so the JSON fits.
GRID_MIN_COVERAGE = max(0.0, min(1.0, float(
    os.environ.get("GRID_MIN_COVERAGE", "0.5"))))

SHORTLIST_REQUIRE_COMPLETE_GRID = (
    os.environ.get("SHORTLIST_REQUIRE_COMPLETE_GRID", "true")
    .strip().lower() in ("1", "true", "yes", "on"))

# Crawled assessment text lands here as <slug>.md, and grids derived for roles
# the pack does not cover as grid-<slug>.json, so both can be reviewed and
# hand-edited in git.
ASSESSMENT_DIR = PROJECT_ROOT / "assessments"

# --- Role display names ---
# What the dashboard calls a role, when the portal's own name for it is not
# what you want on a screen.
#
# The portal names an admin object, and it names it for whoever administers it:
# "Ajaia AI Strategist Assessment", "AI-Native Marketing & Execution
# Assessment", "Customer Experience & Product Success Assessment". Those read
# as filenames. What a recruiter is looking at is a seat, and the seat is
# called AI Strategist.
#
# This is an override, not a rename. `portal_crawler.fetch_roles` keeps
# reporting exactly what the portal says and `upsert_roles` keeps storing it;
# the substitution happens on the way out of `mongo_store.get_role(s)`, and the
# portal's own name survives on every role as `portal_title`. So the record of
# what the portal is actually called is never lost, and re-crawling cannot
# clobber the display name -- which is the trap with editing the stored title
# directly, since `upsert_roles` $sets `title` on every crawl.
#
# It reaches more than the dashboard, deliberately. The shortlist email, the
# spreadsheet header and the interview and rejection emails all read the role
# title, and "your application for Ajaia AI Strategist Assessment" is worse in
# a candidate's inbox than it is on a dashboard. One name everywhere.
#
# Keyed by portal slug, which is stable; the portal's title is not, and the
# job id says nothing to a reader. A slug that is not listed keeps whatever
# the portal calls it.
#
# The one entry here is a pin rather than a correction, and it is worth knowing
# which. The portal called job 38 "Ajaia AI Strategist Assessment" when it was
# crawled at 13:37 on 2026-08-21 and "AI Strategist" when it was crawled again
# at 14:10, so somebody renamed it at the source in between and this line now
# agrees with the portal instead of overriding it. It stays because the
# dashboard name was asked for explicitly: pinned here, it is ours, and a
# future portal edit cannot move it without someone changing this file.
#
# That cuts both ways -- a deliberate rename on the portal will not reach the
# dashboard while this line stands. If the portal should win for this seat
# again, delete the entry rather than editing it to match.
ROLE_TITLES = {
    "ai-strategist": "AI Strategist",
}

# --- Brevo (email) ---
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_SENDER_NAME = "Priyansh Singh"
BREVO_SENDER_EMAIL = "priyanshs@ajaia.ai"

# --- Hiring-manager shortlists ---
# Once a role's assessments are graded, the recruiter sends its best candidates
# to the hiring manager who owns the seat. Managers are stored per role in
# Mongo (roles.hiring_managers) rather than here, because they change with
# every reorg and a config edit + restart is the wrong ceremony for that.
SHORTLIST_SIZE = int(os.environ.get("SHORTLIST_SIZE", "20"))

# Hard ceiling on one shortlist send. A typo in the size box should not mail a
# hiring manager 4,000 rows.
SHORTLIST_MAX = int(os.environ.get("SHORTLIST_MAX", "100"))

# The manager sees WHO we rate best, never BY HOW MUCH. Scores, band names and
# verdicts are recruiting-internal: an AI number next to a person's name anchors
# a human reviewer before they have read a word of the work, and it is not a
# number we want quoted back to us in a hiring debrief. The shortlist carries
# rank position, name, contact, CV and the link to the submission itself --
# everything needed to form an independent view.
#
# Flipping this to true is a deliberate policy change, not a display tweak.
#
# WHAT THIS SETTING IS NOW IS THE DEFAULT, not the whole answer. The dashboard's
# Shortlist tab carries an "AI score" tick that the recruiting team sets per
# send: this value is where the box starts, and either way the number reaches
# the SPREADSHEET only -- never the email body, never the manager's review
# page, never the board. Leaving this off and ticking the box for one role is
# the intended shape; turning it on makes every hand-off carry the score unless
# somebody unticks it.
#
# The tick is the recruiting team's alone. A hiring-manager account asking for
# it is refused by server.py's _scores_arg(), which is what actually enforces
# the rule -- shortlist.py builds whatever it is handed and cannot see who is
# asking.
SHORTLIST_SHOW_SCORES = (os.environ.get("SHORTLIST_SHOW_SCORES", "")
                         .strip().lower() in ("1", "true", "yes", "on"))

# --- Manager review links ---
#
# The shortlist email carries a private link to a page where the hiring manager
# marks candidates for interview, hire or rejection. The link IS the credential,
# and this is the one surface that deliberately does NOT ask for a sign-in --
# a manager will not log in to answer one email, and if they had to, the link
# would stop being something they can act on from a phone in a taxi.
#
# The dashboard is the other half and does have accounts (see AUTH_ENABLED
# above). The two are independent: a manager may hold both, and neither needs
# the other.
#
# That puts the whole weight on the token, so it is 32 bytes from
# secrets.token_urlsafe -- not a submission id, not a role slug, nothing
# guessable -- and it is scoped to ONE role and ONE manager. It reveals the
# candidates that were sent to that person and nothing else: no other role, no
# other manager's list, no scores, and none of the dashboard.

# Where the manager's browser can reach this server. It is what the link in the
# email is built from, so a localhost value produces a link that works only on
# the machine that sent it -- send_shortlist() says so out loud rather than
# mailing twenty dead links.
#
# Exposing this server to reach it needs REVIEW-ONLY MODE -- see the note above
# main() in server.py. The full dashboard asks for a sign-in, but /api/run and
# /api/shortlist/send are not endpoints to put on the internet on the strength
# of a password form; the review surface is the part that is built to face it.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/")

# The mailbox on the List-Unsubscribe header, and the fallback a candidate gets
# when the one-click URL is unavailable -- which it is on plain http, because a
# one-click endpoint without TLS is an unsubscribe anyone on the path can forge.
#
# IT HAS TO BE A MAILBOX SOMEBODY READS. This is not a formality: it is the
# address a candidate writes to when they want us to stop, and mail sent there
# and ignored is worse than no header at all. Defaults to the reply-to already
# configured for candidate mail, which is a person, and never a no-reply box.
UNSUBSCRIBE_MAILTO = os.environ.get(
    "UNSUBSCRIBE_MAILTO",
    os.environ.get("CANDIDATE_REPLY_TO", "")).strip()

# Where the recruiter dashboard answers. Its own setting rather than a reuse of
# PUBLIC_BASE_URL, because in production the two are not the same process:
# review links point at the review-only server on the public interface, while
# evaluations.html is only ever served by the full dashboard. Defaults to
# PUBLIC_BASE_URL, which is exactly right on a one-process dev machine and
# wrong the moment the review server is split out.
#
# The shortlist email uses this to link a manager straight to the role's
# pipeline board -- not the shortlist tab, which is recruiting-team machinery
# and is not drawn for a manager at all. Safe to send: the dashboard asks them
# to sign in, and what they see once they do is only the roles they are listed
# on. That last part is what makes it safe, so shortlist.dashboard_link()
# returns nothing at all when AUTH_ENABLED is off. It still has to
# be an address they can actually reach -- under review-only mode the link 404s,
# because evaluations.html is not on that allowlist, so this must point at the
# dashboard process rather than the public review one.
DASHBOARD_BASE_URL = (os.environ.get("DASHBOARD_BASE_URL", "").strip()
                      or PUBLIC_BASE_URL).rstrip("/")

# Whether that deep link is rendered in the manager's copy at all. Its own
# switch so it can be dropped without editing the template or blanking
# DASHBOARD_BASE_URL, which would leave a setting whose emptiness is load
# bearing and nothing to say so.
SHORTLIST_DASHBOARD_LINK = (os.environ.get("SHORTLIST_DASHBOARD_LINK", "1")
                            .strip().lower() in ("1", "true", "yes", "on"))

# How long a review link stays live. Long enough for a manager who was on leave
# when it landed, short enough that a forwarded email is not a permanent key.
# A fresh send mints a fresh link, so this is not a deadline on the hiring.
REVIEW_LINK_DAYS = int(os.environ.get("REVIEW_LINK_DAYS", "30"))


# The Ajaia wordmark at the top of every outgoing email. A mail client cannot
# read a file off this disk, so the logo has to be an absolute URL it can
# fetch, and PUBLIC_BASE_URL is the address this app answers on. Set on its own
# to serve the mark from the marketing site or a CDN instead.
EMAIL_LOGO_URL = (os.environ.get("EMAIL_LOGO_URL", "").strip()
                  or f"{PUBLIC_BASE_URL}/assets/ajaia-logo-white.png")

# ---------------------------------------------------------------------------
# Dashboard accounts
#
# WHO SEES WHAT. The dashboard used to be one undivided surface: whoever
# reached it read every role, every candidate's address, and every button that
# sends real mail. That is fine for a recruiter who owns the whole funnel and
# wrong for a hiring manager who owns one seat -- a manager opening the page to
# look at their own shortlist should not be reading another team's pipeline.
#
# So there are two kinds of account, and the difference is enforced on the
# server, not by hiding buttons:
#
#   admin     the recruiting team. Every role, plus the machinery -- portal
#             sync, reminder sends, who owns which seat, and the accounts
#             themselves.
#   manager   a hiring manager. ONLY the roles whose `hiring_managers` list
#             carries their address. Every other role is a 403, and the roles
#             endpoint never mentions it, so there is nothing to guess at.
#
# A manager's roles are not a field on their account. They are derived, live,
# from the hiring-manager list the recruiter already maintains per role -- the
# same list the shortlist mails to. One place to say who owns a seat means
# access cannot drift away from ownership: take someone off a role and they
# lose the role, in the same click, with no second screen to remember.
#
# AUTH_ENABLED   the only way to turn this off. Default on. Set to 0 ONLY on a
#                local machine with no real data -- it restores the old
#                everybody-sees-everything behaviour, and the server says so
#                loudly at startup every time.
AUTH_ENABLED = (os.environ.get("AUTH_ENABLED", "1")
                .strip().lower() not in ("0", "false", "no", "off"))

# Bootstrap admins. Accounts are stored in Mongo and managed from the dashboard
# (or manage_users.py); these two settings exist for the very first login, when
# there is no account to log in with and therefore no way to create one.
#
# On startup every address here is created as an admin if it does not exist,
# with PORTAL_ADMIN_PASSWORD, and is marked must-change so the shared bootstrap
# password cannot quietly become somebody's real one. An address that already
# exists is left alone -- this never resets a password or re-promotes an
# account that was deliberately demoted.
#
# Leave PORTAL_ADMIN_PASSWORD unset in production once the first admin has
# logged in and set their own; a password in a .env file is a password on disk.
PORTAL_ADMINS = tuple(
    address.strip().lower()
    for address in os.environ.get("PORTAL_ADMINS", "").split(",")
    if "@" in address
)
PORTAL_ADMIN_PASSWORD = os.environ.get("PORTAL_ADMIN_PASSWORD", "")

# How long a login lasts. Two clocks, and the shorter one wins:
#   TTL   absolute age of the session, however active it has been. A stolen
#         cookie has an expiry no amount of use can extend.
#   IDLE  time since the last request. A dashboard left open on an unlocked
#         laptop stops being a way in overnight.
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))
SESSION_IDLE_HOURS = int(os.environ.get("SESSION_IDLE_HOURS", "8"))

# Name of the cookie, and whether it is marked Secure. Secure is inferred from
# DASHBOARD_BASE_URL rather than configured: a cookie marked Secure is simply
# never sent over the plain-HTTP loopback dev server, which would look exactly
# like a login that does not work. Force it on with SESSION_COOKIE_SECURE=1
# when the dashboard sits behind a TLS proxy that this process cannot see.
SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "ajaia_session")

# The second cookie: the CSRF token, readable by script because the page has to
# put it in a request header. Its name is FIXED rather than derived from
# SESSION_COOKIE -- frontend/auth.js reads it by name, and a renamed session
# cookie would otherwise silently break every POST the dashboard makes.
CSRF_COOKIE = "ajaia_csrf"
SESSION_COOKIE_SECURE = (
    os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
    in ("1", "true", "yes", "on")
    or DASHBOARD_BASE_URL.startswith("https://")
)

# Failed-login lockout. Per account, so a password guessed at from anywhere
# stops working for everyone rather than only for the address doing the
# guessing -- a distributed attempt is the one worth stopping. The window is
# short: this is here to make guessing slow, not to lock a manager out of
# their own shortlist because they typed it wrong twice before coffee.
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "8"))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))

# ...AND A THROTTLE ON WHOEVER IS DOING THE GUESSING, which the lockout above
# is not and cannot be.
#
# A per-account lockout answers the distributed attempt. On its own it also
# hands anyone who knows an admin's address a way to keep that admin signed
# out: eight wrong passwords locks them for fifteen minutes, and eight more
# does it again, for ever, from anywhere, at no cost to the attacker. The
# lockout is the right rule and the denial of service is its shadow.
#
# So the source of the attempts is rate-limited first. An address that has run
# out of attempts is refused BEFORE the password is checked, which means those
# attempts never reach the account and never count toward locking it -- the
# throttle absorbs the flood instead of converting it into a lockout. A real
# person who mistypes their password a few times is far below this; somebody
# working through a list is not.
#
# Counted per (source address, window) in Mongo rather than in a dict, because
# the lockout it defends is in Mongo: an in-process counter would reset on
# every deploy and restart, which is exactly when it is worth having.
# THIS MUST STAY BELOW LOGIN_MAX_ATTEMPTS, and that is the whole mechanism.
#
# If a single source can make more failed attempts than it takes to lock an
# account, the throttle never gets the chance to stop it: eight wrong passwords
# lock the admin out and the twentieth attempt that would have been refused
# never happens. Below the lockout threshold, one source runs out of attempts
# BEFORE the account runs out of tolerance -- so a lone attacker gets
# throttled, and the account lockout is left to do the job it is actually for,
# which is the attempt spread across many addresses.
LOGIN_IP_MAX_ATTEMPTS = int(os.environ.get("LOGIN_IP_MAX_ATTEMPTS", "5"))
LOGIN_IP_WINDOW_MINUTES = int(os.environ.get("LOGIN_IP_WINDOW_MINUTES", "15"))

# How many reverse proxies stand in front of this process.
#
# X-Forwarded-For is a header, which means the client writes it. Trusting it
# blindly makes the throttle above useless -- a new forged address per request
# is a fresh bucket every time. Each proxy APPENDS the address it saw, so with
# a known number of trusted hops the real client is that many entries from the
# right, and everything left of it is the client's own writing.
#
# 0 (default) ignores the header entirely and uses the socket's peer address,
# which is correct when nothing is in front of this. Set it to the number of
# proxies you actually run -- 1 behind a single nginx or Cloud Run, 2 behind a
# CDN in front of that. Setting it too HIGH is the dangerous direction: it
# reaches back into the part of the header the client controls.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))

if LOGIN_IP_MAX_ATTEMPTS >= LOGIN_MAX_ATTEMPTS:
    # Not fatal -- somebody may have raised both deliberately -- but said out
    # loud, because the failure is invisible: everything still works, and the
    # only symptom is that an admin can be kept locked out by anyone who knows
    # their address.
    import warnings
    warnings.warn(
        f"LOGIN_IP_MAX_ATTEMPTS ({LOGIN_IP_MAX_ATTEMPTS}) is not below "
        f"LOGIN_MAX_ATTEMPTS ({LOGIN_MAX_ATTEMPTS}). A single source can now "
        "lock an account before its own attempts are throttled, which is the "
        "denial of service the throttle exists to prevent.",
        RuntimeWarning, stacklevel=2)

# Shortest password the dashboard will accept when one is set or changed.
# Length rather than a character-class rule: a rule that demands a symbol is
# how "Password1!" happens.
PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", "12"))

# --- Candidate outcome emails ---
# What the candidate hears once a hiring manager moves them on the board. Two
# messages, both to the candidate rather than to a manager:
#
#   interview  the invitation, carrying the manager's own cal.com link so the
#              candidate books a slot that is genuinely free. The link is not
#              configured here -- it belongs to the manager, is typed by them
#              in the dashboard, and is stored per manager in Mongo.
#   rejected   the turn-down after a human has seen them. NOT the same mail as
#              the missing-artefact rejection: this person was read and
#              considered, and a form letter about a missing file would be
#              wrong in front of them.
#
# Nothing is sent for `hired`. An offer is a conversation someone has, not a
# templated mail a board click fires, and the day this system sends one by
# accident is the day it stops being trusted with the other two.
PIPELINE_EMAILS_ENABLED = (os.environ.get("PIPELINE_EMAILS_ENABLED", "1")
                           .strip().lower() in ("1", "true", "yes", "on"))

# PAUSED, like the portal automation above. While this is off, NO candidate
# mail rides along with anything: a stage move on the dashboard, and a hiring
# manager's decision on their review link, are recorded and nothing is sent.
# Every candidate email leaves this system from one place -- the Send button in
# the candidate's drawer, clicked by a person who has just read the preview.
#
# The two flags are different questions and are kept apart on purpose:
#
#   PIPELINE_EMAILS_ENABLED=0  candidate mail is off entirely. Even the Send
#                              button does nothing but say so.
#   PIPELINE_AUTO_EMAIL=0      candidate mail works, but only a human click
#                              starts it.
#
# Set PIPELINE_AUTO_EMAIL=1 in .env to go back to sending on the move itself,
# on both surfaces at once. Nothing else has to change: the send path, the
# templates and the duplicate suppression are the same either way, so the
# switch is the whole difference between manual and automatic.
PIPELINE_AUTO_EMAIL = (os.environ.get("PIPELINE_AUTO_EMAIL", "")
                       .strip().lower() in ("1", "true", "yes", "on"))

# Where a candidate's reply goes. Both mails invite one -- "I cannot make any
# of those times", "can I ask what let me down" -- and a no-reply address in
# front of someone who was just rejected is a small cruelty. Defaults to the
# sender, which is a person.
CANDIDATE_REPLY_TO = os.environ.get("CANDIDATE_REPLY_TO", "").strip() or BREVO_SENDER_EMAIL

# How long the candidate is asked to book within. Copy only -- nothing expires
# on our side, because a link that stops working while the candidate is deciding
# turns a booking into an email thread.
INTERVIEW_BOOK_WITHIN_DAYS = int(os.environ.get("INTERVIEW_BOOK_WITHIN_DAYS", "5"))

# --- Bulk rejections ---
# The Rejections page mails everybody who did not make it, one personalised
# message each rather than one BCC to four hundred people. Three numbers govern
# how hard that leans on Brevo and on our sending reputation.

# Seconds between messages. Brevo's transactional endpoint tolerates a good
# deal more than three a second, but a five-hundred-message burst from a domain
# that normally sends thirty a day is exactly the shape a spam filter is
# watching for. At the default the batch takes about three minutes, which is
# nothing against the alternative of the whole send landing in Promotions.
REJECTION_SEND_DELAY = float(os.environ.get("REJECTION_SEND_DELAY", "0.35"))

# The blast radius of one click. A paste that accidentally carried the whole
# candidate export should be refused rather than sent -- the recruiter can
# raise this deliberately, which is the point of it being a number and not a
# confirmation dialog.
REJECTION_MAX_PER_SEND = int(os.environ.get("REJECTION_MAX_PER_SEND", "600"))

# Consecutive Brevo failures that stop the run. A single bounce is one bad
# address in a pasted list and the batch should carry on past it; twenty in a
# row is the API key, the account, or the network, and grinding through another
# four hundred to discover the same thing helps nobody.
REJECTION_ABORT_AFTER = int(os.environ.get("REJECTION_ABORT_AFTER", "20"))

# --- Reminder rules ---
# Candidates are chased only inside a window: old enough to have had a fair
# shot at the assessment, recent enough that a nudge is still relevant. The
# upper bound also caps blast radius -- a months-old backlog can never be
# emailed all at once.
REMINDER_AFTER_BUSINESS_DAYS = 4     # lower bound: applied at least this long ago
REMINDER_UNTIL_BUSINESS_DAYS = 7     # upper bound: and no longer ago than this
MAX_REMINDERS_PER_CANDIDATE = 2
DAYS_BETWEEN_REMINDERS = 2           # business days between 1st and 2nd reminder

# --- Candidate selection ---
# Workable automation sends the assessment invite when a candidate applies, so
# `created_at` is a reliable stand-in for "invite sent" -- measured across 40
# candidates the median lag was 0.00 days and the maximum 1.81. That is what
# lets this system skip per-candidate activity-log scans entirely.
#
# It only holds for candidates who APPLIED. Sourced candidates were added by a
# recruiter, never applied, and never received an invite -- emailing them would
# send an assessment link to someone who was never selected. Review, Failed
# Assessment and Talent Pool are all past this stage. Keep this filter tight.
ELIGIBLE_STAGES = {"applied", "assessment"}

# Jobs whose invite automation was switched on part-way through their life:
# workable_shortcode -> the first date (UTC, inclusive) an invite actually went
# out. Candidates who applied BEFORE this date are in the Applied stage but were
# never invited, so "applied implies invited" is false for them.
#
# Waiting for the window to move past the switch-on date is not enough. The
# window spans four business days, so for several days it holds invited and
# never-invited candidates at the same time -- only a per-candidate date check
# separates them.
#
# Each date below was measured by reading the invite link out of candidate
# activity logs day by day and finding the boundary: 0/4 sampled candidates
# invited the day before, 4/4 on the day itself. Add an entry here whenever you
# enable automation on a job that already has applicants.
INVITES_START_AT = {
    "55D093D336": "2026-08-06",   # IT Operations & Infrastructure Lead
    "056AFC5F93": "2026-08-06",   # IT & Compliance Auditor
    "A17EA2076A": "2026-08-06",   # Systems & Security Administrator
}

# Jobs to monitor: workable_shortcode -> (label, portal_job_id, apply_slug)
#
# Many Workable postings feed one portal assignment. There are fifteen separate
# marketing postings and nine analyst postings, all pointing at a single
# assessment -- so this cannot be derived from job titles, and a wrong mapping
# sends a candidate the wrong assessment.
#
# The mapping below was measured, not guessed. Two kinds of evidence appear in
# the trailing comment on each line:
#
#   "N shared, X%"       -- candidate-email overlap: how many of this job's
#                           candidates appear in that portal assignment, and what
#                           share of the assignment's records they represent.
#                           Fewer than two shared was treated as coincidence.
#   "invite link in N/M"  -- the portal apply URL read directly out of the invite
#                           email in the candidates' Workable activity logs. This
#                           is direct evidence rather than inference, and it works
#                           for assignments nobody has submitted to yet.
#
# Prefer the invite-link method when adding jobs: overlap breaks down on postings
# with thousands of candidates, where two coincidental shares mean nothing.
#
# To re-derive after adding jobs, see the note in README.md.
JOB_ASSESSMENTS = {

    # portal 31 -- Business Workflow Analyst (2369 portal records)
    "87599E53E2": ("Business Process Analyst", "31", "workflow-analyst"),               # 563 shared, 24%
    "CC26439A17": ("Business Systems Analyst", "31", "workflow-analyst"),               # 443 shared, 19%
    "97A728C3B0": ("Process Improvement Analyst", "31", "workflow-analyst"),            # 413 shared, 17%
    "588F19BE21": ("Business Workflow Analyst", "31", "workflow-analyst"),              # 269 shared, 11%
    "A8F97CB4F4": ("AI Workflow Analyst", "31", "workflow-analyst"),                    # 246 shared, 10%
    "A181036C30": ("Business Process Consultant", "31", "workflow-analyst"),            # 174 shared, 7%
    "6B0ED43BCE": ("AI Process Consultant", "31", "workflow-analyst"),                  # 129 shared, 5%
    "9D1B95FE34": ("Business Process Automation Specialist", "31", "workflow-analyst"), # 98 shared, 4%
    "1D65AE4480": ("Systems & Workflow Analyst", "31", "workflow-analyst"),             # 88 shared, 4%
    "2752D71C4A": ("Business Operations Analyst", "31", "workflow-analyst"),            # 729 shared, 31%; invite link in 2/2 sampled

    # portal 17 -- AI-Native Full Stack Developer Assignment (735 portal records)
    "0C6BA6AAA9": ("Full Stack Developer", "17", "full-stack-developer-assignment"),    # 619 shared, 84%

    # portal 30 -- AI-Native Marketing & Execution Assessment (409 portal records)
    "E7AA1D58FD": ("Digital Marketing Specialist", "30", "marketing-advertising"),      # 78 shared, 19%
    "A62B548E95": ("Google Ads Specialist", "30", "marketing-advertising"),             # 64 shared, 16%
    "8F2BB57537": ("Performance Marketing Manager", "30", "marketing-advertising"),     # 50 shared, 12%
    "81E7623FF8": ("Marketing Operations Specialist", "30", "marketing-advertising"),   # 50 shared, 12%
    "CADB7D3FF7": ("Digital Marketing Manager", "30", "marketing-advertising"),         # 37 shared, 9%
    "FDE9DE298E": ("Growth Marketing Manager", "30", "marketing-advertising"),          # 34 shared, 8%
    "FA32F18330": ("Search Engine Marketing (SEM) Specialist", "30", "marketing-advertising"),  # 32 shared, 8%
    "E21A3D4922": ("Acquisition Marketing Manager", "30", "marketing-advertising"),     # 32 shared, 8%
    "DCDA2900E9": ("Performance Advertising Manager", "30", "marketing-advertising"),   # 28 shared, 7%
    "C248DB4FA9": ("Growth Marketing Specialist", "30", "marketing-advertising"),       # 25 shared, 6%
    "D90F72BB86": ("Media Buying Specialist", "30", "marketing-advertising"),           # 20 shared, 5%
    "29D74D0167": ("Growth & Acquisition Manager", "30", "marketing-advertising"),      # 19 shared, 5%
    "942ECEA894": ("Customer Acquisition Specialist", "30", "marketing-advertising"),   # 8 shared, 2%
    "4CB871A83E": ("Demand Generation Specialist", "30", "marketing-advertising"),      # 7 shared, 2%
    "A7AA277A3E": ("User Acquisition Manager", "30", "marketing-advertising"),          # 4 shared, 1%

    # portal 28 -- Full Stack Product Engineer (161 portal records)
    "741C5E8B3F": ("Full Stack Product Engineer", "28", "product-engineer"),            # 130 shared, 81%

    # portal 33 -- Customer Experience & Product Success (78 portal records)
    "46E5B95C46": ("AI Product Support Specialist", "33", "ce-product-success"),        # 12 shared, 15%
    "BAE56554BA": ("EdTech Customer Success Manager", "33", "ce-product-success"),      # 7 shared, 9%
    "170BFDC584": ("Customer Success Specialist", "33", "ce-product-success"),          # 5 shared, 6%
    "442CED2E85": ("Client Success & Implementation Manager", "33", "ce-product-success"),  # 5 shared, 6%
    "24003248F3": ("Product Support & Success Manager", "33", "ce-product-success"),    # 4 shared, 5%
    "1DE0E83953": ("Customer Success Manager", "33", "ce-product-success"),             # 3 shared, 4%
    "1DC1F9D083": ("Product Success Manager", "33", "ce-product-success"),              # 3 shared, 4%
    "939E241DDB": ("Customer Onboarding & Success Manager", "33", "ce-product-success"),# 3 shared, 4%
    "0BB0342F99": ("Customer Experience Manager", "33", "ce-product-success"),          # 3 shared, 4%
    "4D0A4F2986": ("Customer Experience & Product Success Lead", "33", "ce-product-success"),   # 2 shared, 3%
    "9CCB0D5AD5": ("Customer Experience & Product Success Lead", "33", "ce-product-success"),   # 2 shared, 3%
    "760FD1C6C4": ("Technical Customer Success Manager", "33", "ce-product-success"),   # 2 shared, 3%
    "8DA755AF6B": ("Customer Support & Success Manager", "33", "ce-product-success"),   # invite link in 2/2 sampled
    "0B5972FC48": ("Customer Success & Product Support Lead", "33", "ce-product-success"),  # invite link in 2/2 sampled
    "9D6CFA8B6A": ("Product Adoption Manager", "33", "ce-product-success"),             # invite link in 2/2 sampled

    # portal 23 -- AI Trainer (65 portal records)
    "B046AC35DD": ("AI Trainer", "23", "ai-trainer"),                                   # 3 shared, 5%
    "523C2FDC1C": ("Generative AI Trainer", "23", "ai-trainer"),                        # 3 shared, 5%
    "E7C61B05EF": ("AI Business Transformation Consultant", "23", "ai-trainer"),        # invite link in 2/2 sampled
    "2AD32031D8": ("Artificial Intelligence Trainer", "23", "ai-trainer"),              # invite link in 2/2 sampled
    "E35EAEC399": ("AI Transformation Consultant", "23", "ai-trainer"),                 # invite link in 2/2 sampled
    "46B4E1B2C9": ("AI Integration Consultant", "23", "ai-trainer"),                    # invite link in 2/2 sampled
    "78DA363BF1": ("AI Innovation Consultant", "23", "ai-trainer"),                     # invite link in 2/2 sampled
    "97051FB348": ("AI Workforce Enablement", "23", "ai-trainer"),                      # invite link in 2/2 sampled
    "C166AF37C0": ("Enterprise AI Trainer", "23", "ai-trainer"),                        # invite link in 2/2 sampled
    "FB48CAE79A": ("AI Coach", "23", "ai-trainer"),                                     # invite link in 2/2 sampled
    "4829B926C3": ("AI Professor", "23", "ai-trainer"),                                 # invite link in 2/2 sampled

    # portal 35 -- Director of IT / CISO (58 portal records)
    "5CDC8BCA86": ("Cloud Security & DevOps Engineer", "35", "director-it-ciso"),       # 24 shared, 41%
    "0026C2D946": ("Director of IT & Security", "35", "director-it-ciso"),              # 16 shared, 28%
    "B4F9D994DA": ("Information Security Analyst", "35", "director-it-ciso"),           # 8 shared, 14%
    "F45197C664": ("Chief Information Security Officer (CISO)", "35", "director-it-ciso"),  # 6 shared, 10%
    "A1CA7FB5BD": ("IT Security Manager", "35", "director-it-ciso"),                    # 2 shared, 3%
    # Automation enabled 2026-08-06 -- see INVITES_START_AT above. Everyone who
    # applied before then is in Applied but was never invited.
    "056AFC5F93": ("IT & Compliance Auditor", "35", "director-it-ciso"),                # invite link in 4/4 sampled from 2026-08-06
    "55D093D336": ("IT Operations & Infrastructure Lead", "35", "director-it-ciso"),    # invite link in 3/3 sampled from 2026-08-06
    "A17EA2076A": ("Systems & Security Administrator", "35", "director-it-ciso"),       # invite link in 4/4 sampled from 2026-08-06

    # portal 29 -- Chief of Staff (49 portal records)
    "49DAEDE42F": ("Chief of Staff (AI-Enabled CEO Operations Partner)", "29", "chief-of-staff"),  # 20 shared, 41%
    "A651C60B57": ("Strategy & Operations Lead", "29", "chief-of-staff"),               # 8 shared, 16%
    "1860B6BE9F": ("Executive Strategy & Operations Lead", "29", "chief-of-staff"),     # 5 shared, 10%
    "4E7E143A3C": ("Executive Strategy Lead", "29", "chief-of-staff"),                  # 4 shared, 8%
    "2A24775712": ("Strategic Programs Lead", "29", "chief-of-staff"),                  # 2 shared, 4%
    "34C40E8E50": ("Enterprise Operations Lead", "29", "chief-of-staff"),               # invite link in 2/2 sampled

    # portal 32 -- Strategic Investment Lead (12 portal records)
    "63AC5E1E31": ("Head of Investments", "32", "investment-lead"),                     # 2 shared, 17%
    "C1BDFD4EC7": ("Investment Strategy Lead", "32", "investment-lead"),                # 2 shared, 17%
    "C64682D9BA": ("Investment Manager", "32", "investment-lead"),                      # invite link in 2/2 sampled
    "8E1352F3CC": ("Head of Strategic Investments", "32", "investment-lead"),           # invite link in 2/2 sampled
    "9021EC2DF4": ("Venture Investment Lead", "32", "investment-lead"),                 # invite link in 2/3 sampled
    "B633510958": ("Strategic Investment Manager", "32", "investment-lead"),            # invite link in 2/2 sampled
    "C6154F0468": ("Director of Strategic Investments", "32", "investment-lead"),       # invite link in 2/2 sampled
    "F986BFBB0C": ("Head of Investments & Strategic Partnerships", "32", "investment-lead"),  # invite link in 2/2 sampled
    "2D54459CAD": ("Investment & Corporate Development Lead", "32", "investment-lead"), # invite link in 2/2 sampled
    "91A08F5669": ("Investment Executive", "32", "investment-lead"),                    # invite link in 2/2 sampled
    "2760277B53": ("Strategic Investment Lead", "32", "investment-lead"),               # invite link in 2/2 sampled
    "ACCCFDDB74": ("Corporate Development Lead", "32", "investment-lead"),              # invite link in 2/2 sampled
    "2E4F4C98B3": ("Strategic Finance & Investments Lead", "32", "investment-lead"),    # invite link in 2/2 sampled
    "BD99D062D5": ("Transactions & Investments Lead", "32", "investment-lead"),         # invite link in 2/2 sampled
    "B1DEDB2871": ("Corporate Investment Lead", "32", "investment-lead"),               # invite link in 2/2 sampled

    # portal 15 -- Social Media Manager (19 portal records)
    "C5B968396C": ("Social Media and Video Content Manager", "15", "social-media-manager"),  # 16 shared, 84%; invite link in 1/10 sampled

    # portal 24 -- IT Manager (7 portal records)
    "D2350B30CE": ("IT Manager", "24", "it-manager"),                                   # 6 shared, 86%

    # portal 38 -- Ajaia AI Strategist Assessment (new 2026-08-20)
    #
    # Two postings, one assessment, and that is deliberate rather than a
    # mapping accident: the senior and the junior seat sit the same 90-minute
    # exercise. Both invite links were read out of candidate activity logs on
    # 2026-08-21, the day after the postings went up, and both point here.
    #
    # Invite automation was on from the first day for both, so there is no
    # INVITES_START_AT entry to add -- every applicant postdates it, because
    # the jobs did not exist before it.
    #
    # What differs between the two is the standard, not the assessment. The
    # rubric pack carries a grid for each -- `ai_strategy` for the senior seat
    # at four to seven years, `ai_strategy_associate` for the associate seat at
    # zero to three -- and both hang off this one slug. JOB_TIERS below is what
    # picks between them, so a posting added here without a tier entry is
    # marked against the senior grid by default.
    "218F45AD60": ("Senior AI Strategist", "38", "ai-strategist"),               # invite link in 5/5 sampled
    "32DBC63865": ("AI Strategist", "38", "ai-strategist"),                      # invite link in 5/5 sampled

    # portal 39 -- Social Media and Marketing Intern (new 2026-08-22)
    #
    # One posting, one assignment, and no overlap evidence to be had: the job
    # went up on 2026-08-21 and all eight of its candidates are still in
    # Applied, so nobody has submitted yet. The invite link is direct evidence
    # and does not need them to -- it was read out of all eight activity logs
    # on 2026-08-22 and every one of them points here.
    #
    # Invite automation was on from the posting's first day, so there is no
    # INVITES_START_AT entry to add: every applicant postdates it, because the
    # job did not exist before it.
    "9AB42204CE": ("Social Media and Marketing Intern", "39", "social-marketing-intern"),  # invite link in 8/8 sampled

    # portal 34 -- Operations Associate (no submissions yet, so overlap could
    # never have found these -- every one came from the invite link)
    "1E233EC268": ("Executive Associate to the CEO", "34", "operations-associate"),     # invite link in 2/2 sampled
    "89865C911C": ("Executive Operations Associate", "34", "operations-associate"),     # invite link in 2/2 sampled
    "D78C559F47": ("CEO Operations Associate", "34", "operations-associate"),           # invite link in 2/2 sampled
    "15835EB581": ("Executive Business Partner", "34", "operations-associate"),         # invite link in 2/2 sampled
    "D13B58CCFD": ("Executive Strategy Associate", "34", "operations-associate"),       # invite link in 2/2 sampled
}

# The portal has 29 assignments in total, up from 25: ai-strategist (38) and
# information-security-analyst (37) both appeared on 2026-08-20, and
# social-marketing-intern (39) on 2026-08-22. Two of the three are mapped
# above; information-security-analyst is not, and joins the list below.
#
# The 14 below still have no Workable job mapped to them -- no posting's invite
# email points at their apply link, so whatever feeds them is not a Workable
# job we can see:
#   4  project-manager       (1 submission)    21 senior-product-and-brand-designer (7)
#   26 recruitment-manager   (2 submissions)   27 remote-project-manager            (1)
#   36 data-scientist        (1 submission)
# and these, which have apply links but no submissions at all:
#   13 technical-program-manager           14 ai-delivery-lead
#   16 enterprise-ai-strategy-fellow       18 founder-s-office-ai-consulting-fellow
#   19 enterprise-ai-product-automation-fellow
#   20 ai-solutions-architect              22 senior-ai-native-designer
#   25 full-stack-designer                 37 information-security-analyst
# Add them by hand once you know which Workable posting feeds each.
#
# Separately, 30 published Workable jobs are deliberately NOT listed above. They
# send no assessment invite at all: every one of their candidates sits in the
# Applied stage, none has ever reached Assessment, and no invite email exists in
# any sampled activity log. They are the Research/Data Analyst, Partnerships and
# Implementation Specialist families. Adding one would email an assessment link
# to candidates who were never invited to take it -- the same failure mode the
# sourced-candidate filter above guards against.
#
# When automation does get switched on for one of them, add it here AND add its
# switch-on date to INVITES_START_AT. The three IT jobs went through exactly
# that on 2026-08-06 and are the worked example.

# --- Which standard a posting is marked against -----------------------------
#
# workable_shortcode -> the tier of the rubric grid its candidates are graded
# on. Only needed where two postings share one portal assignment and therefore
# one slug, which today is the AI Strategist pair and nothing else: both sit
# the identical 90-minute exercise, so the assessment cannot tell them apart
# and the posting has to.
#
# Deliberately a separate map rather than a fourth element on JOB_ASSESSMENTS.
# Every posting in this system has a slug; almost none has a tier, and widening
# that tuple would make thirty-odd entries carry a None to describe a fact that
# applies to two of them. It also keeps ASSESSMENT_JOBS' unpacking below
# unchanged, which reminder.py and the dashboard both read.
#
# An unlisted shortcode has no tier, which is not an error: rubric_pack's
# `for_slug` falls back to the slug's default grid. For the AI Strategist pair
# that default is the senior one, which is the stricter of the two on
# background -- being wrong in that direction costs a candidate a second look
# rather than a false advance.
JOB_TIERS = {
    "218F45AD60": "senior",      # Senior AI Strategist, 4 to 7 years
    "32DBC63865": "associate",   # AI Strategist, 0 to 3 years
}


def tier_for_job(shortcode: str | None) -> str | None:
    """The rubric tier a Workable posting's candidates are graded at, if any."""
    return JOB_TIERS.get(shortcode) if shortcode else None


def _validate_job_assessments() -> None:
    """
    Every portal_job_id in JOB_ASSESSMENTS must be a string. Checked at import.

    THIS IS NOT PEDANTRY, AND THE FAILURE IS SILENT. The portal's job_id
    arrives as a CSV column, so it is always a str -- "31". Matching a
    candidate to their assignment is an equality test against the value in the
    table above (portal_scraper.get_portal_emails). Type `31` instead of `"31"`
    on one line of a 92-entry table and that test is `"31" != 31`, which is
    True for every row: the set of people who have started that assignment
    comes back EMPTY, every candidate on it looks like they never began, and
    the next run emails all of them.

    Nothing else notices. There is no error, the scrape succeeds, the counts on
    the dashboard look plausible for a job nobody has started yet, and the only
    signal is the mail going out. The comparisons are str()-normalised at both
    call sites as well -- this assertion is the second lock, so a wrong type is
    a startup failure with a line number rather than a mass send.
    """
    wrong = {
        shortcode: portal_job_id
        for shortcode, (_label, portal_job_id, _slug) in JOB_ASSESSMENTS.items()
        if not isinstance(portal_job_id, str)
    }
    if wrong:
        detail = ", ".join(
            f"{shortcode}: {value!r} is {type(value).__name__}"
            for shortcode, value in sorted(wrong.items()))
        raise TypeError(
            "JOB_ASSESSMENTS portal_job_id must be a string, quoted, because "
            "the portal CSV's job_id column is a string and these are compared "
            f"for equality. Wrong: {detail}")


_validate_job_assessments()


# Expanded into the shape the rest of the system expects. The assessment URL is
# generic per assignment rather than per candidate, which is why it is built
# here instead of being parsed out of each candidate's invite email.
ASSESSMENT_JOBS = {
    shortcode: {
        "label": label,
        # str() belt-and-braces. _validate_job_assessments() has already
        # refused a non-string, so this can only be a no-op -- but it is the
        # value every downstream comparison is made against, and normalising it
        # where it is built means no caller has to remember to.
        "portal_job_id": str(portal_job_id),
        "assessment_url": f"{PORTAL_BASE_URL}/apply/ajaia/{slug}",
    }
    for shortcode, (label, portal_job_id, slug) in JOB_ASSESSMENTS.items()
}


# --- Roles with no assessment ----------------------------------------------
#
# Postings that are decided on the CV alone. There is no portal assignment, no
# invite, no submission and nothing for reminder.py to chase -- the candidate
# applies on Workable, uploads a resume, and that resume is the whole file.
#
# So these jobs sit in their own map rather than as an entry in JOB_ASSESSMENTS
# with two of its three fields empty. Everything that reads JOB_ASSESSMENTS
# reads it to answer "which assessment, and has this person started it", and a
# posting here has no answer to either question. Keeping them apart is what
# stops a CV-only role appearing in a reminder run.
#
# The resumes come from Workable's own API rather than the portal: the
# candidate detail endpoint returns a presigned link to the file they uploaded,
# which is a real PDF or DOCX and not the Drive share page the portal form
# collects. Measured over all 58 candidates on this posting on 2026-08-25,
# resume_reader extracted text from 58 of them. The portal path's figure for
# the same operation is about 60%, and the whole difference is where the file
# is hosted.
#
#   workable_shortcode -> (label, rubric slug, dashboard job_id)
#
# `job_id` is ours, not the portal's. The portal numbers its assignments 3 to
# 39 and hands those ids to the roles collection; these roles have no portal
# assignment to be numbered by, so they are allocated from a band far above
# anything the portal will reach. See CV_ONLY_ID_BASE for the matching
# reservation on submission ids.
CV_ONLY_JOBS = {
    "EA7059EA8E": ("General Manager & Head of Growth", "gm-head-of-growth", 900),
}

# Where CV-only submission ids start.
#
# Portal submission ids run 163 to 9,915 today and grow by a few thousand a
# year, all of them allocated by the portal. A Workable candidate id is a hex
# string and cannot be one of these, but the dashboard's routes are
# `<int:submission_id>` throughout, so a candidate graded here still needs an
# integer id -- and it has to be one the portal will never issue.
#
# One million is that number with a century of headroom, and it is allocated
# sequentially from a counter rather than hashed from the candidate id, because
# a hash gives collisions and a collision here silently overwrites somebody's
# evaluation. `mongo_store.upsert_workable_candidates` keys on the Workable
# candidate id and allocates an integer only on first sight, so re-running the
# ingest is idempotent.
CV_ONLY_ID_BASE = 1_000_000


def cv_only_job(shortcode: str | None) -> dict | None:
    """The CV-only posting behind a Workable shortcode, or None."""
    entry = CV_ONLY_JOBS.get(shortcode or "")
    if not entry:
        return None
    label, slug, job_id = entry
    return {"shortcode": shortcode, "label": label, "slug": slug,
            "job_id": job_id}


# The dashboard job_ids of every CV-only role, for the readers that have a
# submission in hand and no shortcode.
CV_ONLY_JOB_IDS = frozenset(job_id for _label, _slug, job_id in CV_ONLY_JOBS.values())


def is_cv_only(job_id) -> bool:
    """Whether this role is decided on the CV alone."""
    return job_id in CV_ONLY_JOB_IDS


def required_artefacts_for(job_id) -> tuple[str, ...]:
    """
    What a candidate on this role must have submitted to be worth grading.

    The pair for a role with an assessment, the resume alone for one without.
    A function rather than a constant because the answer stopped being the same
    for every role the day a posting with no assessment was added, and the
    screening rule in ingest.apply_auto_rejections runs across every submission
    in the database at once.
    """
    return (CV_ONLY_REQUIRED_ARTEFACTS if is_cv_only(job_id)
            else REQUIRED_ARTEFACTS)


# --- Automation switch ---
# PAUSED. Nothing in this system touches the portal, Workable or Brevo on its
# own while this is off: opening the dashboard no longer scans, and an
# unattended `python reminder.py` refuses to send. Every scrape and every send
# has to be asked for -- the dashboard's "Sync portal" button, or a CLI run with
# --force.
#
# Turn it back on with AUTOMATION_ENABLED=1 in .env (and re-enable the cron
# entry in crontab.example).
AUTOMATION_ENABLED = os.environ.get("AUTOMATION_ENABLED", "").strip().lower() \
    in ("1", "true", "yes", "on")

# --- State and logs ---
STATE_DIR = PROJECT_ROOT / "state"
STATE_FILE = STATE_DIR / "reminder_log.json"

# The last portal scan, kept on disk. With automatic scanning off, the
# dashboard has nothing to draw after a server restart unless the previous scan
# outlives the process.
SCAN_CACHE_FILE = STATE_DIR / "last_scan.json"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "reminder.log"

# ROTATION IS A RETENTION POLICY, NOT HOUSEKEEPING.
#
# This log is not ordinary application output. It names candidates and their
# email addresses line by line across the whole funnel -- that is deliberate,
# because the dashboard's Logs panel is how a recruiter follows what a run
# actually did, and "sent to <address>" is the useful line. It also means the
# file is a growing plaintext register of everyone who has ever applied.
#
# An unbounded FileHandler keeps that register for ever. Rotation bounds it in
# two directions at once: the live file stays small enough to read, and the
# oldest addresses fall off the end instead of accumulating until somebody
# copies the directory somewhere.
#
# 5 MB x 5 is roughly a year of the current send rate. Raise the count only if
# you have decided the retention period, and lower it if you have decided a
# shorter one -- this number IS the policy, so it belongs somewhere a person
# can find it rather than in a logging call.
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))

# 0o600 -- owner read/write, nobody else. A no-op on Windows, which does not
# have POSIX modes; the container runs Linux as uid 10001 and this is what
# stops a log full of candidate addresses being world-readable inside it.
LOG_FILE_MODE = 0o600
