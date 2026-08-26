# Assessment Reminder System

Sends reminder emails to candidates who were invited to an assessment but have
not started it.

## Automation is paused

Nothing in this system reaches the portal, Workable or Brevo on its own right
now. There is no scheduled run, and opening the dashboard no longer scans —
**scraping happens only when someone clicks "Sync portal"**, on either page.

- `AUTOMATION_ENABLED` in `config.py` (set from `.env`) is off. While it is off,
  a plain `python reminder.py` logs a "paused" line and exits without scanning
  or sending. Read-only modes (`--scan-only`, `--dry-run`, `--preview`) still
  work, and `--force` sends from a run you are watching.
- The cron entry in `crontab.example` is commented out.
- The dashboard serves the last scan, however old, and says how old it is. A
  live send refuses to work from a scan older than 15 minutes and asks for a
  sync first, so a stale table can never email someone who has since started.

To turn it back on: set `AUTOMATION_ENABLED=1` in `.env` and re-install the
cron entry.

## How it works

Each run — a Sync portal click, or `reminder.py` once automation is back on —
does three steps.

**Step 1 (portal):** Logs into the assessment portal at
candidateassessments.ajaia.ai and downloads its CSV export — every candidate who
has started or submitted. These candidates do NOT need reminders.

**Step 2 (Workable):** For each job in `config.py`, fetches candidates who
applied between 3 and 7 business days ago and are in the Applied or Assessment
stage.

**Step 3 (cross-reference):** Anyone in that window who is not in the portal
gets a reminder via Brevo, with the job's assessment link.

Each candidate receives a maximum of 2 reminders, spaced 2 business days apart.

A run takes about 15 seconds.

## Why this approach

**Applying is the invite.** Workable automation emails the assessment link when
a candidate applies, so `created_at` doubles as "invite sent". Measured across
40 candidates the median apply-to-invite lag was 0.00 days and the maximum 1.81
— and 67 of 67 sampled candidates in the Applied and Assessment stages had a
real invite in their activity log.

That is what lets this system skip per-candidate activity-log scans. The
earlier design opened every candidate's activity feed looking for the invite
email: one API call each, roughly 45 minutes for a job this size. The whole scan
is now a single paginated candidate list.

**Stage cannot be trusted, but it still matters.** Some jobs move candidates to
Assessment when the email goes out; others leave them in Applied. So the stage
does not tell you whether someone was invited — which is why both are eligible.
It does tell you whether someone *applied*: Sourced candidates were added by a
recruiter and never received an invite, so emailing them would send an
assessment link to someone who was never selected. `ELIGIBLE_STAGES` keeps them
out, along with Review, Failed Assessment and Talent Pool, who are all past this
stage.

**The window bounds the blast radius.** The upper bound matters as much as the
lower one. Without it, the first run against an established job would email
every candidate who ever failed to start — 2,371 people on Full Stack Developer
alone, some of whom applied months ago. With it, only a recent cohort is ever in
scope.

**The portal has a CSV export.** `/admin/companies/1/submissions.csv` returns
every submission with real columns, including an explicit `submission_status`.
The original scraper parsed dashboard HTML, which renders only ~200 of the rows
and required guessing status from badge text — it both under-reported and
mis-classified.

**That export has a hidden filter, and it caused real duplicate reminders.**
The bare URL returns only rows still at `review_status = new` — measured 13 Aug
2026, 4,460 rows out of 8,606. The missing 4,146 are everyone a reviewer has
touched: pending 2,646, rejected 1,117, reviewed 300, interview 83. There is no
error and no warning; it is a 200 with a plausible row count.

For reminders that omission is the worst one available. A candidate submits,
gets moved to Pending Review, and disappears from the export — so the next run
cannot tell them from someone who never opened the assessment, and emails them
a reminder to do work they have already done. Three candidates sitting in
Pending Review on the portal dashboard were confirmed absent from the default
export while twenty of their same-day peers were present.

`PORTAL_SUBMISSIONS_CSV_ALL` in `config.py` adds `?review_status=all`, which is
a true superset: every id from the default export, no duplicates. An
unrecognised filter value falls back to the default silently rather than
erroring, so that string is not one to edit casually —
`?review_status=pending_review` returns the same 4,460 rows as no filter at
all.

**The complete export will not download in one piece.** It is ~40 MB, almost
all of it `submission_markdown`, and the portal drops the connection part-way
through the body: 6 failures in 8 attempts, at ~100s each. Streaming does not
help, and no column-trimming parameter is honoured (`fields`, `columns`,
`include_markdown` — all ignored, still 17 columns and 39.5 MB). So
`portal_scraper.py` fetches one review state at a time and reassembles them —
`REVIEW_BUCKETS` — which comes down reliably in under a minute. The single
unfiltered request is kept as a fallback for when a bucket fails.

The cost of splitting is that `REVIEW_BUCKETS` has to stay current: an
unrecognised value falls back instead of erroring, so a review state the portal
adds later would go unfetched and its candidates would look like they never
started. Per-bucket counts and the `review_status` spread are logged every run,
so a state that stops appearing is visible in the log.

`review_status` itself is carried on every record and exposed as
`under_review`, with `UNREVIEWED` in `portal_scraper.py` listing the values
that genuinely mean untouched. Anything unfamiliar counts as reviewed: the two
errors are not symmetric, and emailing someone about work they already
submitted is the expensive one. The status reported for a candidate is still
the portal's own `submission_status` — 1,586 rows are `in_progress` with the
review column moved on, so promoting those to "submitted" would only put a
wrong badge on the dashboard.

**`ingest.py` fetches two of those queues, not all five and not just one.**
`INGEST_REVIEW_BUCKETS` in `config.py` is `("new", "pending")`: the untouched
queue plus the portal's Pending Review section. Both are submissions nobody has
reached a verdict on, which is exactly what the grader is for. The other three
— rejected, reviewed, interview — carry a decision a human already made, and
pulling them in would spend tokens re-scoring ~1,500 settled candidates.

This is the same per-queue fetch `portal_scraper.py` uses, and for the same
reason: one request per `review_status`, retried, reassembled by
`submission_id` so a record that moves between queues mid-fetch is stored once.
A queue that will not download costs its own rows and nothing else — the rest
still upserts, the missing queue is named in the log, on the dashboard's sync
message, and by exit code 3.

`--review-status new,pending` overrides the default for one run. Values are
validated against `REVIEW_BUCKETS` before anything is fetched, because the
portal answers an unknown `review_status` with the default rows and a 200 — an
unchecked typo would look like a clean run that fetched `new` twice.

## Prerequisites

- Python 3.10+
- A Workable API token with the `r_candidates` scope (`r_jobs` is useful for
  looking up shortcodes, but the system does not need it)
- A Brevo API key with transactional email permission
- Login credentials for the assessment portal
- For evaluations only: MongoDB (a local `mongod` is fine) and an API key for
  any OpenAI-compatible model provider. MongoDB also stores the dashboard's
  accounts, so signing in needs it running

## Setup

```bash
cd assessment-reminder
pip install -r requirements.txt
cp .env.example .env
# Fill in all four values in .env
python manage_users.py add you@ajaia.ai --admin   # the first account
```

That last line prints a password once and asks you to change it at first
sign-in. Everything on the dashboard needs an account from here on — see
**Accounts: who sees which roles**.

Add each job to `ASSESSMENT_JOBS` in `config.py`:

```python
ASSESSMENT_JOBS = {
    "0C6BA6AAA9": {
        "label": "Full Stack Developer",
        "portal_job_id": "17",
        "assessment_url": "https://candidateassessments.ajaia.ai/apply/ajaia/full-stack-developer-assignment",
    },
}
```

`portal_job_id` comes from the portal dashboard's own links
(`/admin/companies/1?tab=dashboard&job_id=NN`). It scopes the "already started"
check to this assignment — someone who completed a different role's assessment
has not started this one.

`assessment_url` is the invite link. It is generic per job rather than per
candidate, which is why it is configured here rather than parsed out of each
candidate's invite email.

### Working out which assessment a job feeds

Titles are useless here — fifteen marketing postings and eleven AI-consultant
postings each collapse onto a single assessment, and a wrong mapping sends a
candidate the wrong one. Derive it instead:

1. **Read the invite link out of the job's own candidates.** The automation that
   fires on apply embeds the portal apply URL in its email, and that email is in
   the candidate's activity log:

   ```
   GET /jobs/{shortcode}/candidates?limit=10
   GET /candidates/{id}/activities        # grep for /apply/ajaia/<slug>
   ```

   Sample a few candidates and require at least two to agree. This is direct
   evidence, and it works for assignments nobody has submitted to yet.

2. **Map the slug to `portal_job_id`** using the portal's own jobs tab,
   `/admin/companies/1?tab=jobs` — each row links to both its apply URL and its
   `job_id`.

3. **Sanity-check with candidate overlap** where the assignment has submissions:
   how many of the job's candidate emails appear in that assignment's CSV rows.
   Treat this as corroboration only. On a posting with thousands of applicants,
   two shared candidates is coincidence — people apply to several roles.

If no invite link turns up **and** every candidate is sitting in the `Applied`
stage with none in `Assessment`, the job has no assessment wired up. Leave it
out. Adding it would email an assessment link to people who were never invited
to take one.

### Enabling automation on a job that already has applicants

Then only candidates who applied *after* the switch-on get an invite, and the
earlier ones are indistinguishable from them — same stage, same `created_at`
shape. Find the boundary by sampling activity logs day by day (it is sharp: 0/4
invited the day before, 4/4 on the day), and record it in `INVITES_START_AT` in
`config.py`. `workable_scanner.py` drops anyone who applied earlier.

Waiting for the reminder window to move past the date is **not** a substitute.
The window is four business days wide, so for several days it contains invited
and never-invited candidates at once.

## Running

Scan only — see who qualifies, send nothing:

```bash
python reminder.py --scan-only
```

Dry run — full flow including dedupe, but no emails:

```bash
python reminder.py --dry-run
```

Production run — blocked while automation is paused, see above:

```bash
python reminder.py
python reminder.py --force    # send anyway, paused or not
```

Useful flags:

| Flag | Effect |
|---|---|
| `--job CODE` | Restrict to one job shortcode |
| `--limit N` | Send at most N emails this run — worth using on the first live run |
| `--force` | Send even though automated runs are paused |

## Dashboard

A review-then-send UI over the same pipeline:

```bash
python server.py     # http://127.0.0.1:5000
```

It shows everyone in the current window with their portal status and reminder
count, lets you tick individual candidates, and sends only to those. The send
cap defaults to 20. Nothing is sent until you press a button and confirm.

Loading the page costs nothing: it draws the last scan the server holds (kept in
`state/last_scan.json`, so it survives a restart) and the header says when that
was. **Sync portal** is the only control that goes out to the portal and
Workable; on the evaluations page it is the same button, re-crawling into Mongo.

The dashboard and the CLI call the same two functions in `reminder.py` —
`gather_state()` decides who qualifies, `send_batch()` does the sending — so the
two can never drift apart. A selection narrows the send; it cannot bypass the
window, stage filter, portal check or dedupe, all of which still run server-side.

This page is recruiting-team only — it lists candidates across every role and
its buttons send real mail, so a hiring-manager account is redirected to the
evaluations page instead. See **Accounts: who sees which roles** below.

`server.py` still binds to localhost by default. A sign-in is the right lock on
who reads what; it is not a reason to put a box that mails hundreds of
candidates on a public port without TLS in front of it. See
`frontend/README.md` for the API contract.

## Accounts: who sees which roles

The dashboard used to be one undivided surface — whoever opened it read every
role, every candidate's email address, and every button that mails hundreds of
real people. That is the right shape for a recruiter who owns the whole funnel
and the wrong shape for a hiring manager who owns one seat.

So there are two kinds of account, and the difference is enforced on the server:

| | Sees | Can do |
|---|---|---|
| **Recruiting** (`admin`) | every role | everything: portal sync, reminder sends, who owns which seat, accounts |
| **Hiring manager** (`manager`) | only the roles their address is listed on | read those roles, **grade their candidates**, move them, send their shortlist, set their own booking link |

**A hiring manager grades their own roles.** Both doors: *Grade pending* walks
the role's queue in a batch, and *Evaluate now* in the drawer marks the one
candidate they are looking at. It was never a rule that they could not — the
route has been scoped by role, not by account type, all along — but their
payload carried no `decision.status`, so the page could not count the pending
queue, the button sat disabled on every role and the status column read
`unknown`. The queue state (and the reason behind a `rejected` row, which means
a missing CV or video rather than a verdict) is now on their payload; `source`
and `at` are bookkeeping and stay off it. Nothing about a role a manager is not
named on has changed: that is still a 404.

**A hiring manager reads the AI score on the dashboard**, along with the grid
behind it, the per-criterion marks, the verdict and the brief — the same
Candidates table and the same drawer a recruiter opens, narrowed to their own
roles. `MANAGER_DASHBOARD_SCORES=0` takes it back out, of the page *and* of the
payload.

That is a different answer from the one the **shortlist email** gives, and
deliberately: a number in an inbox arrives alone, with nothing around it to
argue with, and gets quoted back at us in a debrief. A number on the dashboard
opens into the rubric that produced it, one click away, for a reader who is
signed in and named on the role. The email, the spreadsheet and the token-only
[review page](#review-links-are-separate-and-still-have-no-login) are unchanged — see
[the hand-off](#hiring-managers-and-the-top-20-hand-off).

**Which roles a manager sees is not configured anywhere.** It is the
**Hiring managers** list on each role — the same list the shortlist email goes
to. Add somebody there and they can open that role; take them off and they
cannot, in the same click. One place to say who owns a seat is what stops
access from drifting away from ownership, and it means there is no second
screen to remember when somebody moves team.

There are **two doors onto that one list**, for the two directions you think
about it from:

- **Evaluations → the role → Shortlist → Hiring managers** — "who owns this
  seat", when you are already looking at the role.
- **Evaluations → Accounts → Roles they can open** — "what does this person
  own", when you are already looking at the account. Each role is a chip with
  an ×, and a picker adds another.

Both write `hiring_managers` on the role. Neither is a permission stored on the
account, which is why they cannot disagree. Assigning from either one also
decides who that role's shortlist is emailed to and who can move its candidates
to Interview — the same act, said out loud in both places.

### First run

```bash
python manage_users.py add you@ajaia.ai --admin   # prints a password, once
python server.py
```

Open <http://127.0.0.1:5000>, sign in, and set your own password when it asks.
Everyone else is created from **Accounts** on the evaluations page. Setting
`PORTAL_ADMINS` and `PORTAL_ADMIN_PASSWORD` in `.env` does the same thing at
startup, for a deployment where nobody has a shell.

`manage_users.py` also has `list`, `passwd`, `promote`, `demote`, `disable`,
`enable`, `remove` and `roles <email>` — the last one prints exactly what an
account can open, read the way the server reads it, which is the fastest answer
to "why is their dashboard empty". (Usually: they have an account but nobody has
put them on a role yet.)

### What the rules actually are

- **A role a manager does not own answers 404, not 403.** "You may not see role
  41" tells them role 41 exists and roughly how busy it is. The answer is the
  same one a job id that was never real gets, and their roles grid never
  mentioned it — so there is nothing to guess at.
- **The scope is a filter in the query, not a filter over the answer.** `roles`,
  the pipeline board and the rejection list all narrow in MongoDB, so a role
  outside the scope is never read, never counted and never serialised. The
  header tallies narrow with them: a manager whose two roles have six interviews
  between them reads six, not the company's ninety.
- **Reads and writes are checked separately.** A manager can move their own
  candidates and send their own shortlist. They cannot rewrite a role's
  hiring-manager list — that list *is* the access rule, and a manager who could
  POST to it could add themselves to any role and read it a second later.
- **`to` on a shortlist send is admin-only.** It mails names, addresses and CV
  links to whoever is named in it; without it the send goes to the role's own
  managers, which is the only address list it is for.
- **Booking links are your own.** `/api/managers/cal-link` writes across every
  role an address owns, so a manager may set only theirs. It is what a candidate
  is sent to book with, and pointing somebody else's at your calendar would
  quietly take over their interviews.
- **The machinery is admin-only**: portal sync, `/api/run`, the reminders
  dashboard and its log, and the accounts screen. A manager who opens `/` is
  sent to the evaluations page rather than shown an empty table.

Hiding a button is never the lock. Every route makes its own check, and the
page's `is_admin` only stops a manager being offered controls that would answer
403 anyway. When adding an admin-only feature: gate it in `server.py` first,
hide it in the frontend second.

### Sessions and passwords

- PBKDF2-HMAC-SHA256, 240k iterations, from the standard library — no new
  dependency for the one thing here that must not be improvised. The parameters
  travel with each hash, so raising the cost later does not lock anyone out.
- The cookie holds a random token; MongoDB holds only its SHA-256. A dump of the
  sessions collection cannot be pasted into a browser.
- A session is a pointer to the account, and the account is re-read on every
  request — no role is baked into the cookie. Demoting or disabling somebody
  takes effect on their next click, and both also end their open sessions.
- Two expiry clocks and the shorter wins: `SESSION_TTL_HOURS` (12) is absolute
  age however active the session has been, `SESSION_IDLE_HOURS` (8) is time
  since the last request.
- A wrong password and an unknown address give the identical message and take
  about the same time. A sign-in form that distinguishes them is a way of asking
  whether a given person works here.
- `LOGIN_MAX_ATTEMPTS` (8) failures lock the **account** for
  `LOGIN_LOCKOUT_MINUTES` (15) — per account, not per source, because a
  distributed guess is the one worth stopping.
- Every password an admin creates or resets is `must_change`: it is shown once,
  and until it is replaced the account can reach nothing but the change form.
- State-changing requests carry a CSRF token in a header, checked against the
  session. The session cookie is `SameSite=Lax` and `HttpOnly`; the CSRF cookie
  is readable, because a value that must travel in a header is proof of origin
  rather than a credential.

### Review links are separate, and still have no login

The token in a `/review/<token>` URL is its own credential and answers before
any of this — a manager clicking the link in their shortlist email does not sign
in, which is the whole point of it. `--review-only` mode serves that surface and
nothing else. See **Managers deciding for themselves** below.

### Checking it still holds

```bash
python test_access.py
```

Signs in as a throwaway admin and a throwaway manager, borrows a real
hiring-manager address so the manager account owns real roles, and asserts every
rule above — then deletes both accounts. Read-only on roles, submissions and
evaluations.

Run it after adding any route that names a role. An access rule is correct
exactly when nothing happens, so a route that forgot its `_role_guard()` call
looks like a working feature from every screen; this is the thing that notices.

### Turning it off

`AUTH_ENABLED=0` restores the old undivided dashboard. It is for a local machine
with no real data, and the server logs a warning saying so on every start.

## Evaluations: scoring submitted assessments

A second pipeline, independent of reminders. Reminders chase people who have
*not* started; this scores the ones who *have* finished.

```bash
python ingest.py                 # portal -> MongoDB (roles, assessments, submissions)
python ingest.py --resumes       # fetch + extract resume text (opt-in, nothing reads it yet)
python grade.py --job 23 --limit 5   # AI-score five pending candidates for one role
python server.py                 # dashboard at /evaluations.html
```

### How it works

**Ingest.** `ingest.py` crawls all 28 roles and each one's LIVE assignment
markdown off `/admin/jobs`, then downloads the submissions CSV. That export
already contains every candidate's full answer text in `submission_markdown`,
so there is nothing to scrape per candidate. Each role's assessment is also
written to `assessments/<slug>.md` so it can be read and diffed in git.

The CSV is fetched one review queue at a time: the untouched queue **and the
portal's Pending Review section**. A candidate who submits and gets moved to
Pending Review is waiting on a verdict, not carrying one, so they belong in the
grading queue like anyone else — they were simply invisible to it while the
export's hidden filter went unnoticed. Queues that already hold a human's
decision are left out; see the export section above.

**Screening.** A submission missing its video or resume is rejected outright,
without ever reaching a model: there is nothing to review. Measured across all
1,755 submitted records, every one had a resume and 335 (19%) had no video — so
in practice this rule is "no video, no review", but both artefacts are checked
in case the form stops requiring one.

**Resume text.** `python ingest.py --resumes` fetches each submitted
candidate's resume, extracts the text, and stores it on the submission as
`resume_text` / `resume_fetched_at` / `resume_error`. It is its own command,
never part of a normal `ingest.py` run: the first backfill is ~3,700 requests
to third-party hosts.

Re-running is cheap: a row whose link has already been read is skipped, so a
second run costs only the candidates who are new or who changed their link.
`--limit N` caps a run. Two retry modes, and the distinction matters:
`--retry-transient` re-attempts only the failures that were about the moment
(rate limits, timeouts, dropped connections) — Google begins throttling around
the 2,400th fetch of a backfill, so **run this once after any full backfill**.
`--retry-errors` re-attempts everything that failed, which mostly means 1,400
requests for files that will be exactly as private the second time. Pair the
retry with `--workers 2`: the throttling is why you are retrying, and going
back in at the same rate earns the same refusal.

Observed on the first full backfill: fetches ran clean at six workers for
~2,400 rows, then Google began dropping connections outright — the last 100
rows of the run returned zero successes and 198 `ConnectionError`s. Those rows
are readable; they were just asked for too fast.

Expect roughly **40% of rows to fail**, and that is the ceiling rather than a
bug. Surveyed across all 7,084 links: 83% are Google Drive/Docs, 5% are
LinkedIn profiles with no file behind them at all, and the portal hosts none of
them — all 22 `candidateassessments.ajaia.ai` links are the `/apply` page the
candidate pasted by mistake. Of the rest, ~20% of Drive files are private or
deleted, ~4% point at a folder, and ~10% of the PDFs that do arrive are scans
with no text layer. Reading those needs OCR or a headless browser; both were
ruled out to keep `requirements.txt` free of system binaries.

The extracted fields are written by `mongo_store.set_resume()` with its own
targeted `$set` and are deliberately **not** in `PORTAL_FIELDS` — that list is
applied as `{k: rec.get(k) for k in PORTAL_FIELDS}`, so a field listed there
but absent from the CSV would be set to `None` on every ingest and wipe the
backfill.

**The CV in grading.** Every candidate is marked **twice, out of 100 each**.
The assessment answer is marked against its family grid; the CV is marked
separately against three criteria of its own — relevant experience, depth and
progression, skills match — judged against the same seat. Neither is a
criterion inside the other. The grids still sum to exactly 100 and
`validate_grid` still enforces it, `rubric_score` on the verdict is still the
grid alone, and the prompt forbids the CV from lifting a grid criterion: that
would be the same evidence counted twice, which is the error the split exists
to prevent.

**How the two hundreds combine is a per-seat decision**, in
`config.CV_WEIGHT_BY_SEAT`. It replaced a flat 50/50, which claimed that a
four-hour full-stack build and a ninety-minute Customer Success plan each
account for exactly half of what we know about someone. A seat leans toward the
CV when the assessment is narrow against the job, when the job's value is
accumulated rather than demonstrable in an afternoon (a deal sheet, a network,
years of incident command), or when the seat is accountable rather than
productive. It leans toward the assessment when the assessment *is* the job in
miniature, when the JD discounts credentials outright — Marketing's
"certification is a baseline, not proof of skill" — or when the seat hires on
aptitude rather than record, as the three fellowships do.

| Assessment / CV | Seats |
|---:|---|
| 75 / 25 | Full Stack Engineering (developer, product engineer) |
| 70 / 30 | Marketing · Analysts and AI Consulting · the three fellowships · the four build-shaped PM and designer assignments |
| 65 / 35 | Research and Data · Social Media |
| 60 / 40 | AI Training · Executive Operations Associate · AI Solutions Architect · Product and Brand Designer |
| 55 / 45 | IT Manager · AI Delivery Lead · Project Manager |
| 50 / 50 | EdTech Implementation · Recruitment Manager · anything unlisted |
| 45 / 55 | IT and Security (Director / CISO) · Chief of Staff |
| 40 / 60 | Customer Success · Investments · Partnerships |
| n/a | **AI Strategy** · **Social Media and Marketing Intern** — scored *inside* the grid, see below |

**Two families are the exception, and the `0.0` in that table means the
opposite of what it looks like.** Their rubrics score the record as a criterion
in the grid, with anchors, so the blend has to be off or the resume is paid for
twice — once inside the grid and again as a share of the total. Both AI
Strategist rubrics put it at 40 of their 100 points; the Social Media and
Marketing Intern rubric puts it at 10. Two consequences on either seat:
`CV_MISSING_POLICY` never fires, so an unreadable CV forfeits nothing and lands
on the row's own neutral 3 — "not stated anywhere scores 3, with a note" on the
Strategist grids, "an empty field scores 3, never 1" on the intern one; and
`rubric_score` and `score` are the same number on those cards. The verdict's
`weighting.background_criterion` is what tells a zero weight of this kind apart
from a seat where the CV genuinely scores nothing.

**The two zeroes are not the same decision.** On AI Strategy the record is 40
points because it decides the seat. On the intern seat it is 10 because it must
*not* decide the seat: an intern pool is mostly people with thin files, the
rubric's own words are "adds, never blocks" and "a candidate can advance at 75+
on work product alone", and a 0.50 blend would hand the portfolio back roughly
half the decision and undo that in one line. The arithmetic that makes it safe:
a candidate scoring 3 on the background row and 5 everywhere else lands at 94,
so an empty portfolio costs four points and nothing more.

Keys are portal slugs first, then pack grid keys, so a pack family and a
derived grid each land on their own number. `CV_WEIGHT_OVERRIDE=0.5` forces one
weight everywhere — for comparing a run against the old flat split, or reverting
in one line. Every verdict stores `cv_weight`, `rubric_weight` and
`cv_weight_source` (`seat`, `default` or `override`), and the candidate drawer
spells the split out under the scored grid, because a reviewer comparing an
Investments 72 with a Full Stack 72 is comparing two different mixtures.

**When the grader skips the CV.** Distinct from having no CV, and it happens:
on the first five gradings after the weights went in, the model returned three
nulls and "no CV available" for a candidate whose CV it had just described
accurately in `cv_check`. That is our failure, so it is never charged to the
candidate — `_blend` ignores `CV_MISSING_POLICY` in this case and scores them on
the assessment alone, and the verdict carries `cv_unmarked: true` so the
dashboard can say the CV was never judged and the rate stays measurable. The
prompt now writes its CV rule from `has_cv` rather than asking the model to
decide whether a CV is present.

**Auto-fails must not be guesses.** An auto-fail removes a candidate from the
ranking, so a hedged one is discarded: any finding whose rule or evidence
contains "likely", "appears to", "may be" and friends lands in
`disputed_auto_fails`, is shown to the reviewer, and does not touch the band.
This caught a false cap breach — *"likely over 225 words"* against a triage note
of 147 and a teacher response of 103 — while leaving a real one intact on a
14,229-word submission. The prompt also receives the submission's true word
count, because counting by inspection is the one thing the model cannot do.

**Read the ceiling table before changing a weight.** `CV_MISSING_POLICY`
defaults to `forfeit`, so a candidate with no readable CV loses that share
outright and is capped at `(1-w) × 100`: **75** on a full-stack seat, **40** on
Customer Success and Investments — the bottom band, whatever they wrote. That is
38% of candidates, and the cause is our extraction failing on a private Drive
file or a scan with no text layer, not anything they did. `CV_MISSING_POLICY=rescale`
scores those candidates on the assessment alone, rescaled to 100, and is the
recommended setting now that the weights run this high.

Alongside the score, the CV also produces `cv_check`: `consistent`,
`contradicted`, or `no_cv`, with a one-line note. It is worth knowing why that
is a named output field rather than something the model mentions when relevant.
The first build did it the implicit way — CV in the prompt, model told to cite
it in a criterion's evidence where it mattered. Handed a real process-analysis
submission alongside a CV describing a retail floor assistant with no software
experience of any kind, it reported nothing at all: no fraud tell, no mention,
and a score marginally *higher* than the same answer with no CV. A signal left
to emerge on its own from prose does not emerge; asked for by name, it comes
back correctly on the same test.

One measurement worth carrying: the grader's own run-to-run spread on a single
unchanged candidate is **13 points**. Any comparison smaller than that is noise,
whatever caused it. That figure is also why the CV is scored explicitly rather
than left to influence the marking — when it was only background, the measured
gap between candidates with and without a readable one was +2.5 points against a
standard error of 9.1, i.e. nothing.

**Grading.** Candidates are marked against the **Ajaia Assessment Scoring
Rubrics** pack (version 2026-08-12), which lives in `rubric_pack.py`: fourteen
rubric units, seventeen scoring grids, covering all 36 live Workable postings.
The grid for a role's assessment is looked up by slug — no model call — and the
candidate's `submission_markdown` is marked against it once.

The fifteen portal assessments the pack covers never pay for a rubric. The
rest derive a grid of the same shape from their assessment text on first use,
written to `assessments/grid-<slug>.json`; hand edits are preserved, and
`--force-rubric` regenerates it.

Any OpenAI-compatible `/chat/completions` endpoint works — Groq, Together,
OpenRouter, or a local server. Set `LLM_BASE_URL`, `LLM_API_KEY` and
`LLM_MODEL` in `.env`. Ingest and the dashboard run fine without them; only
grading is blocked, and the dashboard says so.

### What grading actually costs

Measured on the real backlog, not estimated: the median pending answer is
~11,900 characters (~3,000 tokens). The grid, anchors, auto-fails, triage and
GIA proxies add a measured 1,670–2,900 tokens of prompt depending on the family
(Social Media is the leanest, IT and Security the heaviest, median ~2,100), and
the verdict comes back around 700. So one evaluation is roughly **5,800
tokens**, and across the 1,388 still pending, about **8M tokens**.

Pack-covered roles pay nothing for a rubric — their grid is code. Only the
fourteen uncovered portal assessments spend one derivation call each.

That number decides what is possible on a given key. Groq's free tier gives
llama-3.3-70b 100,000 tokens *per day* and 12,000 *per minute*, which is
~15 candidates a day — the full backlog would take months. Three things follow:

- **Each model has its own quota.** When the 70b's daily allowance ran out,
  `LLM_MODEL=openai/gpt-oss-120b python grade.py …` kept working. Every
  evaluation records the model that produced it, and a derived grid records it
  as `derived_by`, so a mixed run stays auditable.
- **The longest answers cannot be graded on a free tier at all.** A submission
  near the 90th percentile (~30k characters) makes a request larger than the
  per-minute cap, and the provider returns 413 rather than queueing it. Lower
  `MAX_ANSWER_CHARS` in `config.py` to force those into range — evaluations
  record `answer_truncated` when it bites.
- **A long `retry-after` is a stop, not a wait.** `LLM_MAX_BACKOFF` (120s)
  bounds how long a retry sleeps; past that, `evaluator.py` fails immediately
  saying how many minutes the quota has left, instead of retrying three times
  into a wall and reporting "gave up after 3 attempts".

### The scoring grid

Every grid scores 100 points in four fixed blocks:

| Block | Points | What it asks |
|---|---:|---|
| Work product | 70 | The assessment's actual tasks, weighted by JD emphasis |
| AI-forwardness | 10 | AI leverage with judgment: what was automated, what stayed human, how the output was verified |
| Communication and judgment | 10 | Executive readability, constraint compliance, sound tradeoffs |
| Family spike | 10 | The one differentiator that separates great from good in that seat |

**Three grids depart, deliberately, and all three buy the same fifth block —
Background and experience — where every other seat scores the resume *outside*
the grid and blends it in afterwards (see **The CV in grading**, above).**

| Grid | Split (WP / BG / AI / Comm / Spike) | Background worth |
|---|---|---:|
| AI Strategy, both tiers | 40 / 40 / 6 / 7 / 7 | 40 |
| Social Media and Marketing Intern | 55 / 10 / 10 / 13 / 12 | 10 |

A grid states its own split in `block_points`, validation holds it to 100, and
the two arrangements must never both be on: `config.CV_WEIGHT_BY_SEAT` pins
every one of these slugs and grid keys to `0.0` so the track record is not paid
for twice.

**The two prices buy opposite things, and the arithmetic is the reason.** On AI
Strategy the row is load-bearing: a background mark of 1 caps an otherwise
flawless submission at **68**, below the advance bar, so background *blocks*
rather than merely adds. On the intern seat it cannot: a candidate scoring 3
there and 5 everywhere else reaches **94**, and even a 1 still reaches **86**,
which is what the rubric means by "adds, never blocks" and by "a candidate can
advance at 75+ on work product alone". The intern grid's background row also
carries the neutral-3 rule — an empty portfolio scores **3, never 1**, and the 1
is reserved for links that do not open or work that contradicts the submission.
That grid is read *last*, not first, which is the reverse of the Strategist
instruction: at 40 points the risk is a polished deck inflating a thin record,
at 10 points it is a thin record dragging down good work.

**Tiers: one assessment, two standards.** Senior AI Strategist (four to seven
years) and AI Strategist (zero to three) sit the identical 90-minute exercise
and share one portal assignment, so the assignment cannot pick the standard and
the *posting* has to. `config.JOB_TIERS` maps Workable shortcode → tier,
`rubric_pack.for_slug(slug, tier)` picks the grid, and `tier_resolver.py` writes
the answer onto each submission by matching its email against both postings'
candidate lists — the portal's CSV export carries neither. What differs between
the two grids is exactly two things, which is what their source documents say:
the background row is scored on raw material and self-direction rather than
accomplishment, and polish is graded more gently. Triage, the build spec and
every seeded defect are identical, and validation enforces that the two mark
the same rows at the same weights so the dashboard's criterion columns still
line up. An unresolved candidate is graded against the senior grid — the
stricter of the two on background — because a false second look is cheaper than
a false advance. A reviewer can move one candidate across by hand
(`POST /api/evaluations/tier`), which both rubrics ask for in their section 10;
that swap is marked `manual` and no resolver run overwrites it.

**The dashboard shows this as two role cards, not one.** A tiered assignment
appears in the role grid once per *posting* — "Senior AI Strategist" and "AI
Strategist" — sharing a job id and differing in `tier`, which the page then
sends on every request about them. Candidate table, criterion columns, rubric,
Grade button and shortlist are all that posting's; the shortlist in particular
goes out as two mails with two attachments, because a rank is only meaningful
among people marked against the same anchors. Everything that addresses a
*submission* rather than a role — the pipeline board, the rejected list, review
links — is untouched: a submission belongs to exactly one tier however the
dashboard is split. Until `POST /api/evaluations/tiers/resolve` has run,
everyone sits on the default card and the header says how many are there by
fallback rather than by evidence.

Inside the blocks the criteria are **per family**, because the anchors are the
point. Each criterion carries behavioural anchors at 5, 3 and 1 quoting the
real task content — "post-money $13.3M shown as $2M / 0.15", "kill the
90-day-old service account key", "nested lists survive reopen" — so a mark is
something a second reviewer can check and overturn, rather than a vibe. A
criterion is rated 1–5 with one line of evidence, contributes `score × weight ÷
5`, and the total is the sum.

**The model is never asked for an overall score.** The headline number is
computed in `_parse_verdict()`, so a reviewer can add the grid up by hand and
get the same answer, and can disagree with one criterion instead of with an
opaque verdict. Investments, worked: 4 × 14/5 + 3 × 22/5 + 4 × 25/5 + 3 × 9/5 +
2 × 10/5 + 4 × 10/5 + 3 × 10/5 = **67.8 → Good**.

Bands are worded as a ranking rather than a verdict — the dashboard says how
strong a submission is, and a human decides. The advance bar is still 75, where
the interview system draws it, so an assessment score and an interview score
mean the same thing:

| Band | Score | |
|---|---|---|
| Best | 85+ | above the bar |
| Better | 75–84 | above the bar |
| Good | 60–74 | |
| Okay | below 60 | |

What stays comparable across families is the blocks and the bands — an
Investments 62 and a Marketing 62 are the same decision, and their
AI-forwardness rows asked both candidates the same question. `rubric_pack.py`
refuses to load a grid whose criteria do not sum to exactly 100, or whose
blocks do not sum to the split that grid declares, so a hand-edit cannot
silently rescale a family.

**Auto-fails are not low scores.** A cap violation, an off-scenario template,
fabricated data where the task supplied it, a missing AI disclosure, or any of
the family's own auto-fails ends the grading and takes the submission out of the
ranking — it reads **Not scored**, whatever the grid totalled. The model must point at the specific place in the submission
that trips one, and the computed score is still shown so a reviewer overturning
the finding can see what it would have been. **Fraud tells** — burner domains,
identity mismatches, JD-echo, all-caps template letters — are reported
separately for the fraud log and never touch a score.

A grid can decline the universal list with `"universal_auto_fails": False`, and
three do — the AI Strategist pair and the Social Media and Marketing Intern.
Those rubrics repeal them in their own words. There are no length caps in the
Strategist assessment, a missing task "scores that criterion 1, grade the rest
normally", and the only auto-fail is confirmed fraud. The intern assessment has
caps but *scores* them: format and scope compliance is a five-point row, a
written piece under 250 words scores 1, and the instruction is "Grade what is in
front of you" — never judging whether a video was re-recorded, whether AI wrote
the prose, or whether the candidate went over time. Prepending the universal
list in either place would have the grader end candidacies on rules the rubric
it is applying has withdrawn. Opting out is not leniency: fraud tells are a
separate list and still reach every grader.

**Triage runs first.** Six binary checks per family, written for that
assessment: 0–2 yes rejects without full grading, 3–4 goes to full review, 5–6
to priority. Triage never advances anyone on its own; it only orders the queue.

**The GIA overlay** sits outside the 100 and changes no points. Ajaia
administers no formal instrument today, so each grid lists the proxy signals
where aptitude shows through the work, plus the primary scales that seat reads
on and the rules for the day a formal test is added (breaks ties within 5
points, moves a candidate within 3 of a band edge one band, never two, never
over an auto-fail, never a rescue below 50).

If the model skips a criterion, that row stores `score: null` and the remaining
weights renormalise to 100 — a gap in the model's output should not cost the
candidate marks. The evaluation records `grid_complete: false` and the
dashboard says so.

Each evaluation stores the `grid_key`, `grid_source` (`pack` or `derived`),
`grid_version` and `pack_version` it was marked against. Editing an anchor or a
weight moves the bar for everyone in that family, so the hash tells you which
scores predate the change and need re-grading.

Evaluations recorded before the pack keep their old five-category `matrix` and
render in their own shape, labelled *pre-rubric-pack*, rather than being
redrawn as if they had been marked against anchors they never saw.

### Re-running is safe

Ingest overwrites everything the portal owns and touches nothing we own. A
decision a human made (`source: "manual"`) and any completed evaluation both
survive the next sync — so a reviewer pulling someone out of the reject box, or
an expensive grading run, is never silently discarded. Grading only ever picks
up candidates still in `pending`, so a run interrupted by a rate limit can just
be started again.

### Dashboard

`/evaluations.html` shows every role as a card with its status mix — each
labelled **pack grid**, **derived grid** or **no grid**, which says whether the
role can be graded at all — and opens a per-role candidate table sorted
best-score-first. Clicking a candidate opens a drawer
with:

- the **scored grid**, grouped by block with a subtotal on each, one row per
  criterion showing the 1–5 mark, the anchor it was marked against (on hover),
  the points it contributed, and the evidence line;
- **triage** — the six checks with a tick or a cross, the count, and where it
  routed;
- any **auto-fails tripped** and any **fraud tells**, each with what the model
  pointed at;
- the **GIA proxy read**, labelled as changing no points;
- the **hiring pipeline** controls — schedule an interview, mark them hired or
  rejected, and the history of every such move (see below);
- the full submission, links to their video and resume, and a button to move
  them between the reject box and the pending queue.

`#role=<id>` in the URL deep-links a role, and `#rejected` opens the rejection
list.

The **scoring rubric is deliberately not on the page.** The standard lives in
`rubric_pack.py`, the derived grid files and the grader that reads them; the
dashboard states only what came out of it — a score, a band, the per-criterion
marks a candidate earned and the evidence for each. Publishing the anchors
themselves to the same screen as the candidates invites marking against the
rubric by eye instead of reading what the grader actually found, and puts the
live assessment's marking scheme one screenshot away from leaving the building.

The role's grid is still fetched, quietly, because two things are read off it:
the criterion columns below take their labels from it, and the drawer quotes
the pack's own bands ("Better 75–84") rather than a copy kept in the page.
Neither renders the standard.

`GET /api/evaluations/rubric/<job_id>` and the derive endpoint behind it are
unchanged, so the grid is still inspectable by anyone who wants it. Deriving a
grid for a role the pack does not cover is a command-line job now rather than a
button — `python grade.py --job <id> --rubric-only`, or just grade the role,
which derives one first. Pack-covered roles never derive: their grid is
hand-authored against the live assessment, and nothing overwrites it with model
output — edit `rubric_pack.py` to move that bar.

**Criterion columns** in the candidate table is the same grid read the other
way. Ticking it adds one 1–5 column per criterion, each sortable, so "who
ranked risk best of these 320" is a click rather than opening 320 drawers. Off
by default: seven extra columns make the table wide, and most of the time the
total is what you want.

### Hiring pipeline: interview, hired, rejected

The score is not the end of the process, so the dashboard carries the rest of
it. The **Pipeline** tab of a role is the shortlist that is still waiting on a
decision, and under it a three-tab board across every role:

- **Interview** — everyone a hiring manager has invited, soonest first, with
  the time, the interviewer and a note. A row with no date sorts to the top,
  because the candidate has not booked into one yet.
- **Hired** — offers accepted, with the interview date they came through and
  the score that got them there.
- **Rejected** — turned down *after* being seen.

**Nobody enters the Interview stage from this dashboard’s own routes.** That
is the one door rule: an interview is invited by the hiring manager, over their
calendar, in words they wrote. `POST /api/pipeline` and `POST /api/pipeline/send`
both answer `403` to `stage: "interview"`, so the rule holds against a script as
well as against a button, and the drawer shows a sentence where the scheduling
controls used to be rather than a button that would fail.

What a signed-in hiring manager *does* get is the same door, opened from here:
the **Top candidates** panel at the top of the Pipeline tab drives their own
review workspace (`POST /api/managers/review-link`, entitled by being named on
the role) and sends through `POST /api/review/<token>/invite`. Same composer,
same preview builder, same audit trail — one way into the stage, reachable
from two surfaces. See *The top candidates, and the invitation* below and
*Managers deciding for themselves* for the emailed version of it.

The reason is that the invitation is the manager's message: signed with their
name, over their calendar, in words they wrote. A recruiter booking on their
behalf produces an email the manager has never read, pointing at a calendar they
may have since moved, and the candidate turns up to a meeting the interviewer
does not know about.

Everything else stays with recruiting. From the candidate drawer: **Mark hired**,
**Mark rejected**, **Remove from pipeline**. From the interview tab a row has
**Hired** and **Reject** on it directly, and a closed row has **Remove** for a
misclick — which puts the candidate back on the shortlist where the manager can
invite them again, rather than silently re-booking a meeting the manager was
never told about. When a candidate is at Interview the drawer shows a read-only
block: who invited them, any time suggested, and **whether the invitation
actually went** — a candidate can sit at this stage with an empty inbox after a
failed send, and nothing else on the card would say so.

Every move is kept in a history list on the drawer, so "booked on the 12th,
pulled back out, rebooked" reads back as what happened rather than being tidied
away. Each tab copies its addresses or downloads a CSV the same way the
rejection list does.

### The top candidates, and the invitation

Above the board, and only for an account named on that role’s **Hiring
managers** list, sits the shortlist that feeds it: the best-scoring candidates
who are still awaiting a decision, strongest first, by the same rule the
shortlist email uses (`store.top_candidates()` — scored, not
artefact-rejected, not already moved along the board). *Top 5 / 10 / 20 / 30 /
50* re-asks the server rather than slicing what is on screen, and a search box
narrows what is drawn without dropping anybody already ticked.

It sits here rather than on the Candidates tab because this is where the work
continues: tick the people worth meeting, write their invitation, and watch them
leave this list for the **Interview** board directly below it. The Candidates tab
is the whole funnel again for every account — no default cap, and its
**Top candidates & invite** button is a door to this section rather than a
second copy of it.

The composer is the review page’s, opened over the manager’s own
workspace: an editable subject and body on the left, the rendered email on the
right, re-rendered by the server as they type, the placeholder chips served by
the API, a suggested time offered for one candidate and refused for a batch, and
the booking button, fallback URL and signature appended after their words
whatever they typed. Everything in *The composer* under **Managers deciding for
themselves** applies unchanged, because it is the same code answering the same
two routes.

A row here opens the candidate card, so the score, the grid and the artefacts
are one click away while deciding. Scores are read from the role’s own
candidate list, not from the review payload — that payload never carries
one, and this panel is not the reason to start.

What it will not do: invite somebody already hired or turned down (the tickbox
is disabled — that is the one mis-click that cannot be walked back), and
send at all while `PIPELINE_EMAILS_ENABLED` is off or the manager has no booking
link. Both are said in a line above the button rather than as a red error after
it, and the second names the header button that fixes it.

Stages live in their own `pipeline` sub-document, next to `decision` and
`evaluation` rather than inside them, and for the same two reasons. Ingest
overwrites everything the portal owns on every run, and a hiring decision has to
survive that. And `decision.status` answers a different question — it says what
the *assessment* concluded, which is what the grading queue and the
missing-artefact reject list are built on. So a candidate rejected after an
interview keeps the score that earned them the interview, stays out of the
artefact-rejection mail merge below (a different email to a different
candidate), and can be read back later against what the assessment predicted.

Role cards gain a line of stage chips, the header stats gain **Interview
scheduled**, **Hired** and **Rejected after review**, and a candidate already in
the pipeline carries their stage as a badge beside their name in the role table.

The endpoints are `GET /api/pipeline?stage=&job_id=` and `POST /api/pipeline`
with `{submission_id, stage, interview_at?, interviewer?, note?, reason?}`;
`stage: null` returns someone to the shortlist. Interview times are stored as
the wall-clock string the interviewer typed and never reinterpreted against a
guessed timezone.

### Hiring managers and the top-20 hand-off

Grading produces a ranking; somebody still has to act on it. Every role can be
given the hiring managers who own that seat, and the dashboard mails them its
best candidates in one click.

Open a role and the **Hiring managers & shortlist** panel appears above the
rubric. It has two halves:

- **Assigned managers** — name, email and an optional title, added one at a
  time and shown as removable chips. Edits are local until **Save**, so adding
  three managers is one round trip; everything that sends is disabled while
  there are unsaved edits, because a send that used the server's older list
  would silently ignore what is on screen. Anyone already on another role is
  offered as a suggestion, so the same person is not spelt two ways across the
  board.
- **Top candidates** — the rows that would be sent, in the order they would be
  sent, with **Download Excel**, **Preview email** and **Send to managers**.
  The default is 20 and the box goes up to `SHORTLIST_MAX`.

Each manager gets their own copy: the greeting is by first name, and one
manager should not be able to read the others' addresses off the header of a
mail about candidates. Replies go to `BREVO_SENDER_EMAIL` — "can I see number
four first?" is the point of the message. Send shares the run lock with
grading, ingest and grid derivation, so two clicks cannot put the same twenty
people in an inbox twice.

**The email does not carry the score.** Not the number, not the band, not the
verdict, not the per-criterion marks. It carries rank position, the candidate's
name and email, and links to the CV, the submitted answers and the video —
everything needed to form an independent view. The rank is included because the
order *is* the recommendation and hiding it would make the list arbitrary; the
magnitude is not, because a "78" beside a name in an inbox — with no grid, no
anchors and nothing to open — decides the interview before the manager has read
a word of the work. `SHORTLIST_SHOW_SCORES=true` reverses that, and it is a
policy change rather than a display tweak.

**The dashboard is the other answer**, and the difference is the point: a
manager who signs in reads the score with the whole rubric under it. See
[Accounts](#accounts-who-sees-which-roles). The two surfaces are set
independently, and the token-only review page carries no score under either.

The assessment link is the submission's own page on the portal
(`/admin/submissions/<id>`), so a manager reads the real answers rather than a
summary of them — which does mean they need a portal login.

The attached spreadsheet is the same rows with real hyperlinks behind short
labels, frozen header and filters on. **Download Excel** hands you the identical
file for a manager who is not in the system yet.

Managers, and the record of what was sent to them, live on the role document as
`hiring_managers` and `shortlist_sends` — never written by the crawler, so an
ingest cannot wipe them, the same containment `decision` and `evaluation` get
on a submission. Role cards carry the owner's name, a "sent" chip once a
hand-off has gone out, and a **no manager** flag when a role has candidates and
nobody to send them to: a role can be fully graded and still be a dead end,
which is invisible from a card that only counts submissions.

Candidates already moved along the pipeline are left out — someone booked,
hired, or turned down after an interview is not news to the manager who made
that call. So are pending and artefact-rejected rows, which have no standing to
be on a manager's desk.

Endpoints: `GET`/`POST /api/roles/<job_id>/managers`,
`GET /api/shortlist/<job_id>?limit=&preview=&note=`,
`GET /api/shortlist/<job_id>/xlsx?limit=`, and
`POST /api/shortlist/send` with `{job_id, limit?, note?, to?}`. `to` overrides
the stored managers for a test send to yourself without changing who owns the
role. The preview renders through the same function that sends, so what you
read before clicking is byte-for-byte what is delivered.

### Telling the candidate: interview invitations and rejections

Two messages reach a candidate, and they leave from two different places.

**The interview invitation is written and sent by the hiring manager**, in the
composer on their review link. It carries their own **cal.com link**, so the
candidate books a slot that is genuinely free instead of starting a thread about
times. The link belongs to the manager, not to the role: it is pasted once, into
the *Booking link* field under **Hiring managers**, and reused on every seat they
own. Where a time has been pencilled in, the mail says so *and* still offers the
calendar, so "that slot doesn't work" is one click.

**An invitation with no booking link anywhere is refused**, before any board
move is written. An invitation with no calendar in it is the one thing this
system will not send, and writing the stage first would leave the board saying
"booked" for a candidate who was never given a way to book. The manager's page
warns about it on load rather than after the click.

**The rejection is sent by recruiting**, from the drawer, exactly as before.

**Rejected** sends the turn-down. It carries no score, no band and no criterion
breakdown — the candidate is told the outcome and that a person actually read
their work, which is the part that is true and the part they are owed.

**Hired sends nothing.** An offer is a conversation someone has, not a template
a board click fires.

Two rules worth knowing:

- **The internal note never leaves the dashboard.** The drawer has two boxes —
  *Note* (or *Reason*), which is for the next reviewer, and *Message to the
  candidate*, which is what they read. Only the second one is ever sent. The
  composer works the same way: what the manager types in *Message* is the email,
  and the note they attach to the decision is not appended to it.
- **A rejection is sent once.** Every send is recorded on the submission under
  `pipeline.emails`, and a repeat move is suppressed with a line saying when the
  first one went. A *rescheduled* interview is not a duplicate: a new booking
  link or a new time is a genuine second message, and suppressing it would leave
  the candidate holding a calendar that is no longer right.

The move is committed before the send and is never rolled back if the send
fails. Where a candidate stands is a fact about the process; a Brevo outage
should not silently un-reject someone. A failed send comes back as a warning on
a move that did happen — "marked rejected, not emailed: no address on record" —
and is written to the history as a failure, because a rejection that bounced is
a candidate still waiting to hear.

**Preview rejection** in the drawer renders the real message through the
function that sends it; the composer does the same for the invitation. Set
`PIPELINE_EMAILS_ENABLED=0` to run everything with candidate email switched off
— decisions are still recorded, and both surfaces say plainly that nobody was
written to.

Endpoints: `POST /api/pipeline` takes `{notify?, manager_email?, email_note?,
resend?}` and refuses `stage: "interview"` with a `403`;
`GET /api/pipeline/preview?submission_id=&stage=` renders either mail without
sending it.

### Managers deciding for themselves

The shortlist email carries a private link per manager, and it is now the whole
job rather than three buttons. It opens a live page listing only the candidates
that manager was sent, where they can:

- **Narrow the list down** — a search box, a *Top 3 / 5 / 10* filter that cuts
  on the rank they were mailed, and *hide the ones I have decided*. A manager
  with twenty names and four slots works top-down anyway; this makes it one
  click instead of twenty judgements.
- **Tick the people worth meeting** and invite them in one go. Selections
  survive filtering, and the count says so when a filter is hiding part of the
  selection.
- **Write the invitation** in a composer: an editable subject and body on the
  left, the rendered email on the right, re-rendered by the server as they type.
- **Mark hired** or **Not proceeding** on any row, with a note.

All of it writes straight to the hiring pipeline the recruiter's board reads.

**The composer.** The manager gets our default copy as plain text and can
rewrite all of it. What they cannot touch is the shell: the Ajaia header, the
booking button carrying their cal.com link, the fallback URL under it and their
signature are appended after their words, every time, whatever they typed. A
manager editing raw HTML could delete the booking button, and an invitation with
no way to book is the one message this system exists to prevent; a manager
editing prose cannot.

One message goes to everyone they picked, so it is written in placeholders —
`{first_name}`, `{name}`, `{role}`, `{manager}`, `{when}` — served by the API so
the chips the page offers cannot drift from what the server substitutes. The
preview resolves them against the first candidate picked while the box still
shows the placeholder, which is the only honest way to show what a batch of
twelve will say. Anything unrecognised is left exactly as typed, so a manager who
writes `{salary}` sees `{salary}` in the preview and notices.

A suggested time is offered for a single invitation and refused for a batch:
one pencilled-in slot cannot be right for twelve people, and writing it onto all
of them is how twelve candidates get told to come at two on Thursday.

**This page has no login, deliberately, and it is the one that does not.** A
hiring manager will not sign in to answer one email, and if they had to, the
link would stop being a thing they can act on from a phone in a taxi. So the
link *is* the credential, and everything below follows from that:

(A manager who *also* has a dashboard account gets both: the review link for
deciding on a shortlist they were mailed, and the dashboard for looking at their
roles in full. Neither depends on the other.)

- The token is 32 bytes from `secrets.token_urlsafe` — nothing guessable, not a
  role slug, not a submission id.
- It is scoped to **one role and one manager**, and to the exact candidate list
  that was emailed. A manager acting on a submission id they guessed gets a 403
  even when that candidate is on the same role.
- Each manager gets their **own** link. A shared one could not be revoked for
  someone who left without cutting off the colleague still working the role, and
  no board move could say which of them made it.
- It expires after `REVIEW_LINK_DAYS` (30). A fresh send mints a fresh link.
- Scores never reach it. `submissions_for_review()` projects `evaluation` out at
  the database, and the API builds each row from a fixed allowlist of fields, so
  there is nothing in the payload to leak by accident.
- Responses carry `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and
  `X-Robots-Tag: noindex`. Without the first, a click through to a candidate's
  Google Drive CV would hand the whole review URL — token included — to Google.

Every move is stamped `source: "manager"` and `by: "Name <email>"`, on the
document and in the history, so the board can say who decided. Rows a manager
moved carry a **via manager** chip.

**What the candidate hears.** The invitation goes out on Send, through the same
`candidate_mail` builder everything else uses — including its duplicate
suppression, so a second identical invite sends nothing and says when the first
one went. Re-inviting on purpose is a tickbox the page only shows when somebody
in the selection has already had one. **Nothing is sent for `hired`** — an offer
is a conversation someone has, not a templated mail a board click fires.

The invitation sends even while `PIPELINE_AUTO_EMAIL` is off, and that is not a
hole in the manual mode; it is the manual mode arriving at its point. The pause
exists so that a person reads the message before a candidate does, and here that
person is the one who wrote it, in the composer, one click earlier. What the
switch still governs is everything that is not that: board moves on the
dashboard, and this page's own hired and rejected buttons.
`PIPELINE_EMAILS_ENABLED` is absolute either way.

Each candidate is moved and mailed on their own, so one missing address does not
cost the other eleven their invitation; every row comes back with its own
outcome and the summary counts them. The board move is written first and the
email second, and a failed email does *not* fail the request — a manager who saw
a red error would click again and invite the same person twice. A transport
failure is reported as a plain sentence and logged in full: our provider's API
errors are internal detail, and showing them to someone outside the company is
both useless and leaky.

Endpoints: `POST /api/review/<token>/invite/preview` renders the invitation as
currently written; `POST /api/review/<token>/invite` moves the picks to
interview and sends. `POST /api/review/<token>/decision` handles hired and
rejected only, and answers `400` to `interview` pointing at the composer. All
three re-derive what the token may touch — a submission id the manager guessed
gets a `403` even when that candidate is on the same role.

Under **Hiring managers & shortlist → Review links** the recruiter sees every
link that exists: who it went to, whether it has been opened, how many decisions
came through it, and **Revoke**, which kills it immediately without touching
anything already decided.

#### Letting managers actually reach it

`server.py` asks for a sign-in, but review links do not, and `/api/run` and
`/api/shortlist/send` are not endpoints to put on the internet on the strength
of a password form. So exposure is still a separate process:

```bash
python server.py                                       # the dashboard, private
python server.py --review-only --host 0.0.0.0 --port 5051   # managers, public
```

In `--review-only` mode every path outside `/review/`, `/api/review/` and three
named static files returns 404 — the same answer an unknown URL gets, so a scan
cannot tell a dashboard exists behind it. It is an allowlist rather than a
denylist of dangerous routes, because a denylist fails open and the next
endpoint anyone adds would be exposed until somebody remembered it.

Put TLS in front of it and set `PUBLIC_BASE_URL` to the address managers reach.
The token travels in the URL, so plain HTTP hands it to the network. Leave
`PUBLIC_BASE_URL` unset and the links are built from `127.0.0.1`: they work for
whoever clicked Send and are dead for every manager — which is why the dashboard
warns both before and after a send rather than letting you discover it when
nobody replies.

Endpoints: `GET /review/<token>` (the page), `GET /api/review/<token>`,
`POST /api/review/<token>/decision` with `{submission_id, stage, note?,
interview_at?}`, plus `GET /api/shortlist/<job_id>/links`,
`POST /api/shortlist/links/revoke` and `POST /api/managers/cal-link` on the
recruiter's side.

### Sending rejection emails

The **Rejection emails** panel (Pipeline tab) is two lists side by side:

| Still to tell | Already got the email |
|---|---|
| Rejected, and has not heard | Rejected, and has |

**They are the same list exactly once — the first time.** After that they
diverge, and one list becomes actively dangerous: next month twenty new people
land beside two hundred who were mailed last month, "select all" takes all two
hundred and twenty, and two hundred people get a second rejection out of a list
that looked correct. There is no undo for that.

The work is unchanged: tick the left column, **Copy for BCC**, send from your
own mail client. Then press **Mark as emailed →** and they move across, out of
the left list and out of its "select all". **← Move back** is the undo.

Nothing on this panel sends or un-sends anything. It records who you have
already written to.

#### Where that record lives

One collection, `rejections`, keyed by email address — not by submission,
because most of these people never sat an assessment and there is no row to
flag. Every rejection path reads it before sending and writes to it after,
**including the pipeline board's own rejection**, so someone marked here will
not be offered a turn-down from the board months later either.

A `failed` row is *not* treated as told: we tried, it bounced, and that
candidate is still waiting.

#### The left column starts empty

Deliberately. The single list this replaced only fed a clipboard, so nothing
was lost by pre-ticking everything. This one has *Mark as emailed* under it,
and one stray click on a pre-ticked list marks the whole company as already
told. One click on **Select all** is the right price for that.

#### There is a bulk sender, and nothing calls it

`backend/notifications/rejections.py` plus `/api/rejections/parse`,
`/preview` and `/send` will mail everyone one personalised copy in the
background, respecting the ledger and the opt-out list, recording each as it
goes. It is complete and tested and **no part of the UI reaches it** — sending
happens in your own mail client, and the dashboard's job is only to remember
who. It is left in place because rebuilding it from nothing is the expensive
part; delete it with `tests/test_rejections.py` if it is not wanted.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

No database, no network, no credentials — window arithmetic, the
dedupe key, portal job-id matching, unsubscribe tokens, the exposure guards,
the background send, and the bulk rejection (parsing a pasted list, rendering
the message, and deciding who is actually mailed). They run on every commit in
CI.

The rejection suite exists for one failure in particular: sending somebody a
second rejection. It is unrecoverable and invisible from our side — nothing
bounces, no count is wrong, the candidate simply reads it twice — so every test
about `plan()` and `send_bulk()` is really about that.

`tests/test_access.py` is **not** collected by pytest, and that is deliberate
(see [pytest.ini](pytest.ini)). It is the access-rule regression test, it is the
most important test here, and it needs a real populated MongoDB — it borrows an
existing hiring manager's address and a role they do not own. Collected
automatically it would either fail on a machine with no database or, worse,
skip its way to green against an empty one. Run it by hand against staging:

```bash
python test_access.py
```

## Candidate opt-outs

Reminder emails carry a `List-Unsubscribe` header — the header Gmail, Yahoo and
Outlook read to decide whether we are a sender worth delivering. A few hundred
near-identical reminders is the most bulk-shaped thing this system does, and
bulk mail without one is scored down.

The header is only half of it. An unsubscribe link that records nothing means
the next run mails that person again, which is worse than never offering one:
they asked, we said yes, and then we did it anyway. So
[unsubscribe.py](backend/notifications/unsubscribe.py) owns the token, the
suppression list and the check, and `send_batch` filters the opt-out list in
bulk before the loop while `send_reminder_email` checks again per candidate — a
run is minutes long, and somebody who unsubscribes mid-batch must not be mailed
by the tail of it.

A few things worth knowing:

- **The link only appears over https.** A one-click endpoint on plain http is
  an unsubscribe anyone on the path can forge, and a loopback `PUBLIC_BASE_URL`
  builds a link that opens on nobody's machine but ours. Until TLS is in front
  of this, `UNSUBSCRIBE_MAILTO` is the only way out a candidate has — so it has
  to be a mailbox somebody reads.
- **`GET` asks, `POST` acts.** Mail clients and scanners follow links in
  messages to see where they go, and an opt-out caused by a prefetch is a
  decision the candidate never made. That is why RFC 8058 one-click is a POST.
- **`/unsubscribe/` is served in review-only mode too.** That is the process
  `PUBLIC_BASE_URL` points at, so it is the host every link in every candidate
  email resolves to.
- **Opting out stops reminders and rejections, not the application.** Both are
  bulk-shaped — the same words to a few hundred people — so both carry the
  header and both are skipped for anyone on the list. An interview invitation
  is not: that is a message about a meeting the candidate is being offered, and
  withholding it would cost them the interview. The unsubscribe page says which
  is which.
- **The suppression check fails closed.** Every other database read here fails
  open, because the cost of an unreachable Mongo is a page that does not draw.
  The cost here is mailing somebody who asked us to stop, which cannot be taken
  back.

## Sending is a background job

`POST /api/run` with `mode=live` answers **202** with a job id, and the page
polls `GET /api/run/status/<id>`. It used to run the whole batch inside the
request, which meant the recruiter's browser waited minutes and any proxy in
between gave up first — showing a failure while the send carried on, because a
dropped client connection stops nothing. "Failed" on screen and hundreds of
emails delivered is the worst pairing available, and the obvious reaction is to
click send again.

`preview` and `dry-run` stay synchronous: neither touches the network. The run
lock is acquired by the request and released by the worker, so a second click
still gets the same 409 it always did.

## Deploying

`python server.py` ends in Werkzeug's development server, and Werkzeug prints a
warning into the log saying not to use it for anything else. It means it: one
request at a time, no queue, no request timeouts, and no way to restart a
worker that has wedged. It is the right thing on a laptop and the wrong thing
anywhere a hiring manager can reach.

Anything that is not a laptop runs gunicorn against `wsgi:app` instead:

```bash
gunicorn -c gunicorn.conf.py wsgi:app
```

`wsgi.py` exists because the startup work — logging, account indexes, seeding
the first admin, choosing the mode — lives in `server.py`'s `main()`, next to
the argparse, and a WSGI server never calls `main()`. Importing
`backend.web.server:app` directly gets you a dashboard with no logging
configured and no admin account. `wsgi.py` is `main()` minus the argparse and
minus `app.run()`, so both ways of starting the process agree on everything
except who owns the socket.

### Two processes, not one

The split that `--review-only` exists for does not go away in a container; it
becomes the deployment shape. The mode is an environment variable, because
there is nowhere to type a flag at a container:

| `REVIEW_ONLY` | What it serves | Where it belongs |
|---|---|---|
| `0` (default) | The full dashboard: every role, every candidate's address, every send button | Private. Loopback, a VPN, or behind a proxy you configured on purpose |
| `1` | Only `/review/<token>`, its API, and the files those pages are built from. Everything else 404s | This is the one that may face the internet — behind TLS |

`PUBLIC_BASE_URL` must point at the review process, and `DASHBOARD_BASE_URL` at
the dashboard one. They are different hostnames once this is split, and the
review server does not serve `evaluations.html`.

### On Vercel — the review surface only

`vercel.json` and `api/index.py` deploy this repo as a serverless function.
**`REVIEW_ONLY` is set in `vercel.json`, not in Vercel's dashboard** — one
source of truth, no precedence question. It is currently `0`, so the whole
dashboard is served there.

That is a deliberate trade. A serverless function is frozen the moment it
returns a response, so anything that reports progress by polling a background
thread breaks in a way that costs real emails — those parts must be run from a
machine with a real process:

| | on Vercel |
|---|---|
| `/review/<token>` and its API | works |
| `/unsubscribe/<token>` | works — and every candidate email links here |
| `/healthz`, the review pages' static files | works |
| Reminder run (`/api/run`) | **thread is killed mid-batch** — some candidates mailed, no record shown |
| Bulk rejection send | same |
| Progress polling | 404s — the job id lives in one instance's memory |
| Two concurrent sends | both proceed — `_run_lock` is per-instance |
| Grading, portal ingest | exceed the function timeout |

With `REVIEW_ONLY=0` the read-and-decide half of the dashboard works fine —
signing in, roles, candidates, scores, the pipeline board, the rejection panel,
minting review links, and single rejection or shortlist sends are all
request-shaped Mongo work. Only the batch jobs in the bottom half of that table
are broken, and they stay on a laptop or on the `Dockerfile` (Cloud Run,
Render, Fly, App Service) where there is a real process and a writable disk.

**Two things follow from serving the dashboard publicly.** Every candidate's
name, address, CV and score sits behind nothing but the login form, so
`AUTH_ENABLED` must stay on and the admin password is the whole boundary. And
`REVIEW_ONLY` must never go *missing* from `vercel.json` — `_review_only()`
reads it with a falsy default, so an absent key deploys the full dashboard by
accident and looks identical to deploying it on purpose. `tests/test_guards.py`
pins its presence for that reason.

Set these in Vercel's environment (Project → Settings → Environment Variables):

| Variable | Why |
|---|---|
| `MONGO_URI` | **Required.** Defaults to `127.0.0.1:27017`, which is nothing inside a lambda. Point it at Atlas and allow-list Vercel's egress. |
| `PUBLIC_BASE_URL` | `https://<your>.vercel.app`. Review and unsubscribe links are built from it, and the one-click unsubscribe header is only emitted over https. |
| `APP_SECRET` | Signs unsubscribe links. Minted into Mongo if unset, which is fine — but pin it if two deployments share candidates. |
| `BREVO_API_KEY` | Only if managers send interview invitations from their review link. |
| `SESSION_COOKIE_SECURE=1` | Vercel terminates TLS upstream, so this cannot be inferred. |

`REVIEW_ONLY=1` is already set in `vercel.json`; it does not need repeating in
the dashboard.

#### Logging has no file there

`logs/` is read-only on Vercel, and `setup_logging()` used to open a rotating
file handler *at import* from `wsgi.py` — which took the process down before
Flask existed and made every request a 500 with nothing in it naming a log
directory. It now falls back to stdout and says so once. That is the right
behaviour for any container: platforms collect stdout, not files inside an
image. `tests/test_guards.py` pins it, because a laptop always has a writable
`logs/` and will never reproduce it.

### With Docker

```bash
cp .env.example .env          # fill it in first
docker compose up -d --build
docker compose exec dashboard python manage_users.py add you@ajaia.ai --admin
```

That brings up both processes and a Mongo, with the dashboard bound to the
host's loopback on `:5000` and the review surface on `:5051`. One image, two
containers, one environment variable apart.

### One worker. Not negotiable yet.

`gunicorn.conf.py` pins `workers = 1`, and that is a correctness constraint
rather than a tuning default. Two things depend on there being exactly one
process:

- **`_run_lock`** (`backend/web/server.py:88`) is a `threading.Lock`, and its
  own comment says what it is for: *"Only one scan or send may run at a time.
  Without this, two clicks could double-send: both would pass the dedupe check
  before either recorded it."* A lock inside one process cannot see a second
  process. Two workers means two locks, each certain it holds the only one.
- **The reminder dedupe log** is a JSON file written with a truncate-then-
  rewrite that has no lock and no atomic swap (`backend/core/utils.py:73-75`).
  Two writers lose each other's records silently, and a lost record is a
  candidate who gets emailed again on the next run.

Concurrency comes from threads inside the single worker, where the lock still
means something. CI fails the build if `workers` is not 1. Raise it only after
the reminder state is in Mongo behind an atomic upsert **and** the run lock is
something both processes can see.

### The dedupe state is in Mongo, not on disk

`state/` used to hold `reminder_log.json` — the only thing standing between a
candidate and a second copy of the same email — as an unlocked JSON file. That
made the container filesystem a correctness problem: every instance kept its own
divergent copy and a redeploy started from an empty one, which made everybody in
the window eligible again.

It now lives in the `reminders` collection behind an atomic claim (see
[Reminder dedupe](#reminder-dedupe) below), so the database is what has to be
durable and `state/` holds only a cached portal scan the next Sync rebuilds. A
volume is still worth mounting so the Logs panel survives a restart; nothing
about correctness depends on it.

### Long requests

Portal sync, batch sends and grading all run synchronously inside the request —
a grading sweep over a full role is minutes, not seconds, at
`LLM_CONCURRENCY=1`. `gunicorn.conf.py` sets a 30-minute timeout for that
reason.

Gunicorn is not the only clock. Most platform proxies impose their own request
ceiling — App Service is around 230 seconds, and Cloud Run's is configurable up
to 60 minutes but defaults far below a full grading run. A sweep that outlives
the proxy is killed after the LLM has been billed and before the result is
written back. Grade in smaller batches, or move grading out of the request
path, before pointing a platform proxy at it.

### Health

`GET /healthz` answers `{"status": "ok"}` with no session and no token, in both
modes. It is liveness only, and deliberately does not check Mongo: the app is
built to stay up and explain a database outage on the page rather than fall
over in one, and a probe that reported unhealthy on the same condition would
have the platform restart the container instead — turning a database that is
briefly unreachable into a crash-loop that cannot tell anyone why.

### What CI checks

`.github/workflows/ci.yml` compiles the tree, imports the app with nothing
configured (a module that reads a credential at import time turns a missing
secret into a container that will not start), asserts both exposure modes still
fail closed, asserts `workers == 1`, builds the image, starts it without a
Mongo to confirm it comes up anyway, and greps the built image for a leaked
`.env` or candidate CSV.

It does **not** run `tests/test_access.py`. That suite wants a populated Mongo —
it borrows a real hiring manager's address and a role they do not own — and
against an empty database it would skip exactly the checks worth running and
report green. Run it by hand against staging before a release.

## Project layout

The Python modules live in `backend/`, grouped by the job they do. The commands
are unchanged: each CLI keeps a thin launcher at the root, so `python
reminder.py --scan-only` and the crontab entry still work exactly as before.

```
assessment-reminder/
├── reminder.py  server.py  ingest.py  grade.py  regrade.py
├── calibrate.py  cv_role.py  manage_users.py  migrate_db.py  test_access.py
│       └── launchers. Three lines each; the code is in backend/.
│
├── backend/
│   ├── core/            config.py, utils.py
│   │                    Config, job definitions, timing rules, PROJECT_ROOT.
│   │                    Imports nothing else in the project — which is what
│   │                    keeps the dependency graph acyclic.
│   ├── database/        mongo_store.py, migrate_db.py
│   │                    The only modules that talk to MongoDB.
│   ├── scraping/        portal_scraper.py, portal_crawler.py,
│   │                    workable_client.py, workable_scanner.py,
│   │                    workable_candidates.py, resume_reader.py
│   │                    Everything that reads the outside world — and so
│   │                    everything that can fail because someone else's
│   │                    service is down.
│   ├── grading/         rubric_pack.py, evaluator.py, cv_evaluator.py,
│   │                    tier_resolver.py
│   │                    Decides what a submission is worth. Not when to
│   │                    score one — that is pipeline/.
│   ├── pipeline/        ingest.py, grade.py, regrade.py, calibrate.py,
│   │                    cv_role.py
│   │                    The stages that get run.
│   ├── notifications/   brevo_client.py, reminder.py, candidate_mail.py,
│   │                    shortlist.py, rejections.py, unsubscribe.py
│   │                    If a message leaves this system, it leaves from here.
│   │                    One directory to read before changing anything a real
│   │                    person receives.
│   ├── accounts/        auth.py, manage_users.py
│   │                    Who may sign in, and which roles they see.
│   └── web/             server.py
│                        The Flask dashboard and every endpoint behind it.
│
├── wsgi.py              What a real server imports. server.py is the laptop.
├── gunicorn.conf.py     Worker config — read the one-worker note before editing
├── Dockerfile  docker-compose.yml  Procfile  .dockerignore
│       └── deployment. One image, two processes: dashboard and review-only.
│
├── frontend/            Both dashboards — plain HTML/CSS/JS, no build step
├── assessments/         Crawled assessments and derived grids
├── tests/               test_access.py — the access-rule regression test
├── tools/               Scratch tools. Nothing in the pipeline imports them.
├── state/               Runtime state (auto-created, gitignored)
└── logs/                Run logs (auto-created, gitignored)
```

Every path in the project is resolved from `config.PROJECT_ROOT`, never from
the current working directory, so cron, a systemd unit and a shell sitting
anywhere all find the same `.env`, `assessments/` and `state/`.

## Files

| File | Purpose |
|---|---|
| `backend/core/config.py` | All configuration, job definitions, timing rules, and `PROJECT_ROOT` |
| `backend/core/utils.py` | Business day math, reminder state tracking |
| `backend/notifications/unsubscribe.py` | Opt-out tokens, the suppression list, and the `List-Unsubscribe` headers |
| `backend/database/mongo_store.py` | MongoDB access; keeps portal-owned and our-own fields apart |
| `backend/database/migrate_db.py` | Copies an older database into the one `MONGO_DB` points at |
| `backend/scraping/portal_scraper.py` | Logs into the portal and downloads the CSV export |
| `backend/scraping/portal_crawler.py` | Crawls roles and their live assessment markdown |
| `backend/scraping/workable_client.py` | Workable API: auth, rate limiting, 429 back-off, pagination |
| `backend/scraping/workable_scanner.py` | Selects candidates inside the reminder window |
| `backend/scraping/resume_reader.py` | Fetches a candidate's resume file and extracts its text (PDF/DOCX) |
| `backend/scraping/workable_candidates.py` | Builds gradeable records from a Workable posting that has no assessment |
| `backend/grading/rubric_pack.py` | The Ajaia Assessment Scoring Rubrics pack as data: 17 grids, validated to 100 points each |
| `backend/grading/evaluator.py` | Grid resolution, anchor scoring, auto-fails and triage, provider-agnostic |
| `backend/grading/cv_evaluator.py` | Marks a candidate on their record alone, for roles with no work sample |
| `backend/grading/tier_resolver.py` | Which of two postings a candidate applied to, where that decides which tier of a family's rubric marks them |
| `backend/pipeline/ingest.py` | Portal → MongoDB, and the missing-artefact screening rule |
| `backend/pipeline/grade.py` | CLI for grading a role or the whole backlog |
| `backend/pipeline/cv_role.py` | CLI for a CV-only role: fetch from Workable, read the resumes, grade |
| `backend/pipeline/regrade.py` | Re-scores submissions that already carry a verdict |
| `backend/pipeline/calibrate.py` | Checks the grader is using the whole scale, not just detecting missing sections |
| `backend/notifications/reminder.py` | Main orchestration: portal, Workable window, cross-reference, send |
| `backend/notifications/brevo_client.py` | Sends reminder emails via Brevo, and the transport under the shortlist send |
| `backend/notifications/candidate_mail.py` | The two candidate-facing outcome emails: the interview invitation with the manager's cal.com link, and the rejection after a human review |
| `backend/notifications/shortlist.py` | Builds a role's top-N hand-off: the rows, the email and the spreadsheet, and sends it to its hiring managers |
| `backend/accounts/auth.py` | Accounts, password hashing, sessions, and `visible_job_ids()` — the whole access rule |
| `backend/accounts/manage_users.py` | CLI for accounts: the first admin, and the way back in when nobody can sign in |
| `backend/web/server.py` | Dashboard backend: serves `frontend/` plus the reminder and evaluation endpoints, and enforces who may see which role |
| `wsgi.py` | WSGI entry point — what gunicorn imports; `server.py`'s `main()` without the argparse or `app.run()` |
| `gunicorn.conf.py` | Worker, timeout and logging config. `workers = 1` is a correctness constraint, not a default |
| `Dockerfile` / `docker-compose.yml` / `Procfile` | Deployment. One image, two processes: dashboard and review-only |
| `tests/test_access.py` | Regression test for the access rules — run it after adding any route that names a role |
| `tools/llm_latency_bench.py` | Scratch LLM latency benchmark. Spends real tokens; run it deliberately, never imported |
| `tools/make_favicon.py` | Cuts the tab icon out of the wordmark. Re-run it if `assets/ajaia-logo.png` is ever replaced — nothing else keeps the two in step |
| `frontend/login.html` | Sign-in, and the first-time password change |
| `frontend/session.js` | The signed-in account on both dashboards: CSRF header, expiry handling, the account chip |
| `frontend/review.html` | The hiring manager's review page — token-scoped, deliberately no login, no scores |
| `frontend/` | Both dashboards — plain HTML/CSS/JS, no build step |
| `frontend/assets/` | The wordmark in both themes, and `ajaia-mark.png` — the square mark every page uses as its tab icon |
| `assessments/` | Crawled assessments (`<slug>.md`) and grids derived for roles the pack does not cover (`grid-<slug>.json`) |
| `state/reminder_log.json` | Tracks which reminders have been sent (auto-created) |
| `logs/reminder.log` | Run logs (auto-created) |

## Failure modes worth knowing

**The portal is the safety catch.** If it returns no records, `reminder.py`
aborts rather than treating every candidate as "never started" and emailing all
of them. Any change to the portal that breaks the download fails loudly instead
of sending a mass mailing.

**Workable returns 429 below its documented rate.** The published limit is 10
requests per 10 seconds; in practice it throttles sooner. `workable_client.py`
runs at 8 and retries with back-off. A swallowed 429 would otherwise look like
"this candidate has no data" and silently drop them.

**Matching is by email address alone.** A candidate who applies with one address
and takes the assessment with another reads as "never started" and will be
reminded. There is no per-candidate token in the assessment link to match on.

**Manual reminders may already be going out.** Some candidates' activity logs
show follow-up messages two and four days after the invite. Confirm whether that
is still happening before enabling this, or those candidates get chased twice.
