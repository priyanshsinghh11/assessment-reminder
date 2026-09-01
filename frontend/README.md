# Frontend

The dashboard for the assessment reminder system. Plain HTML/CSS/JS — no build
step, no dependencies, no framework.

```
frontend/
  index.html   markup
  styles.css   styling (Ajaia design system, light + dark)
  app.js       fetch, filter, sort, select, render
```

Served by `server.py`:

```bash
python server.py     # http://127.0.0.1:5000
```

Opening `index.html` directly still works — it falls back to the `MOCK` object
at the bottom of `app.js` when the API is unreachable, so the page never renders
blank.

## What it shows

- **Stat row** — in window, eligible now, reminded, max reminders, started, submitted.
- **Candidate table** — sortable on every column, filterable by job, status, and
  a name/email search.
- **Send bar** — row checkboxes, a send cap, "Send to all eligible" and "Send to
  selected". Both live sends confirm first.
- **Recent log** — tail of `logs/reminder.log`.

The evaluations page (`evaluations.html`) adds the role grid, the scoring
rubric, the candidate table, the hiring pipeline board, the rejection list, and
the **Hiring managers & shortlist** panel — the hand-off that mails a role's
best candidates to whoever owns the seat. Role cards carry the owner's name, a
"sent" chip once a hand-off has gone out, and a **no manager** flag when a role
has candidates and nobody to send them to.

**One assignment, two dashboards.** Where two Workable postings at different
seniorities sit the same portal assessment — the AI Strategist pair, and
nothing else today — the role grid shows a card per *posting* rather than one
merged card: **Senior AI Strategist** and **AI Strategist**, same job id, same
assignment, two scoring grids. Opening one adds `&tier=` to every request the
page makes, so the candidate table, the criterion columns, the rubric, the
Grade button and the shortlist are all that posting's. A segmented switch in
the role header moves between them.

Which posting a candidate applied to is not in the portal's export — only
Workable knows — so until it has been looked up, everyone sits on the default
(senior) card, which is also the grid they would be graded against. The header
says how many are there by fallback and offers **Match to postings**
(`POST /api/evaluations/tiers/resolve`). That changes no scores: anyone already
graded keeps the verdict the old tier produced until they are re-graded. A
reviewer can also move one candidate across by hand
(`POST /api/evaluations/tier`), which both AI Strategist rubrics ask for — a
strong new graduate on the senior posting is marked on the associate grid
rather than rejected on senior background anchors. Roles with one grid are
untouched everywhere: no tier, no switch, no extra card.

A role's **Pipeline** tab opens on **Top candidates** — the best-scoring people
still awaiting a decision — with the interview composer behind its Invite
button, and the three-stage board underneath. That panel is drawn only for an
account named on the role's hiring-manager list, and it drives that manager's
own review workspace (`/api/managers/review-link`, then the same
`/api/review/<token>/invite*` routes the emailed review page uses), so the
interview stage still has exactly one door into it. See `loadTopCandidates()`
and `openComposer()` in `evaluations.js`.

Only candidates who can actually be sent to get a checkbox: those who have not
started and have not used up their reminders. Everything else is display-only
and dimmed.

Status is derived on the client from raw fields, mirroring `reminder.py`:

| Status | Condition |
|---|---|
| `submitted` | `portal_status == "submitted"` |
| `started` | `portal_status == "in_progress"` |
| `maxed` | `reminders_sent >= max_reminders_per_candidate` |
| `reminded` | `reminders_sent > 0` |
| `eligible` | everything else — would get a reminder on the next run |

There is no `waiting` status any more: the 3–7 business day window is applied
server-side, so everything returned is already old enough.

## API

Every endpoint below needs a session cookie, and every one that names a role is
checked against the roles that session may see. A signed-out page request
redirects to `/login.html?next=…`; a signed-out `fetch` gets `401` with
`{"auth": "required"}`, which `session.js` turns into that same redirect.

**A role outside an account's scope answers `404`, not `403`** — the same
message a job id that never existed gets. Admin-only endpoints answer `403`
with `{"auth": "forbidden"}`; they are marked below.

State-changing requests need the `X-CSRF-Token` header, matching the readable
`ajaia_csrf` cookie. `session.js` adds it to every non-GET on this origin, so
page code never has to.

### `POST /api/auth/login`

`{email, password, next?}` → sets the session cookie and returns
`{user, csrf, next, home}`. `home` is `/` for an admin and `/evaluations.html`
for a hiring manager. A wrong password and an unknown address return the same
`401` and the same message.

If `user.must_change` is true the account has a temporary password: it can reach
`/api/auth/me`, `/api/auth/password` and `/api/auth/logout` and nothing else
until it has set one.

### `GET /api/auth/me`

`{auth_enabled, user, csrf, role_count, home}`. `role_count` is `null` for an
admin (every role) and a number for a manager. **This is what the page draws
with, never what it is allowed to do** — the routes decide that.

### `POST /api/auth/password`

`{current_password, new_password}`. Needs the old one even though a session
exists. Every session for the account is ended and this one is reissued, so the
tab that made the change stays signed in and the others do not.

### `POST /api/auth/logout`

Ends the session server-side and clears both cookies.

### `GET` / `POST /api/auth/users`, `PATCH` / `DELETE /api/auth/users/<email>`, `POST /api/auth/users/<email>/password`

**Admin only.** The Accounts panel. `POST` creates an account and returns a
generated `password` exactly once. The reset endpoint mints a new temporary
password, marks the account must-change and ends its open sessions. All of them
refuse to remove or demote the last working admin.

### `POST /api/auth/users/<email>/roles`

**Admin only.** `{job_ids: [15, 29]}` — makes the account a hiring manager on
exactly those roles and no others.

The other direction of `POST /api/roles/<job_id>/managers`: that one edits one
role's people, this one edits one person's roles, and **both write the same
`hiring_managers` field on the role**. There is no per-account permission
anywhere, which is why the two screens cannot drift apart.

Per-element `$push`/`$pull`, so a recruiter mid-edit on the same role's manager
list does not lose their work, and adding the same person twice is a no-op
rather than a second shortlist email. Job ids that are not roles are reported in
the message rather than written. Returns the refreshed `users` list and
`stale_roles: true`, since the role cards behind the panel now show a different
owner chip.

### `GET /api/state`

**Admin only** — it is every candidate on every role with their email address.

Returns everything the page needs, out of the last scan. **It does not scan.**
Pass `?refresh=1` — what the Sync portal button sends — to run a live scan
(~15 s) and replace it. Automatic scanning is paused; see the main README.

```json
{
  "last_run": "2026-08-07T02:39:18+00:00",
  "scanned": true,
  "stale": false,
  "config": {
    "reminder_after_business_days": 3,
    "reminder_until_business_days": 7,
    "max_reminders_per_candidate": 2,
    "days_between_reminders": 2
  },
  "portal": { "total": 727, "submitted": 434, "in_progress": 293, "under_review": 61 },
  "jobs": [{ "shortcode": "0C6BA6AAA9", "label": "Full Stack Developer" }],
  "candidates": [
    {
      "candidate_id": "2742d822",
      "name": "Viral Chovatiya",
      "email": "chovatiyaviral222@gmail.com",
      "job_shortcode": "0C6BA6AAA9",
      "job_title": "Full Stack Developer",
      "stage": "Applied",
      "applied_at": "2026-07-28T01:07:35Z",
      "business_days_elapsed": 7,
      "portal_status": null,
      "portal_under_review": false,
      "reminders_sent": 0,
      "last_reminder_at": null,
      "assessment_url": "https://candidateassessments.ajaia.ai/apply/ajaia/..."
    }
  ]
}
```

`portal_status` is `null`, `"in_progress"`, or `"submitted"`. `applied_at`
doubles as "invite sent" — see the main README for why.

`portal_under_review` is true when the portal's own review columns
(`review_status`, `screener_rating`, `reviewed_at`) show the submission has
been picked up. Those candidates report `portal_status: "submitted"` whatever
the submission column says, so the table already renders them as Submitted —
the flag is there to say *why*.

`scanned` is `false` before anyone has synced: `last_run` is `null`,
`candidates` is empty and `message` explains why. That is a normal 200 — the
portal has not been asked yet, which is not an error. `stale` marks a scan older
than 15 minutes; the page labels it and a live send refuses to work from it.

`?refresh=1` returns 502 if the portal is unreachable, rather than an empty
candidate list that would look like "nobody has started".

### `GET /api/logs?limit=200`

**Admin only** — the log names candidates and roles across the whole funnel,
line by line.

```json
{ "lines": ["2026-08-07 02:39:18 [INFO] workable_scanner: In window: 242 …"] }
```

### `POST /api/run`

**Admin only.** A live run mails hundreds of candidates across every role.

```json
{ "mode": "dry-run", "limit": 20, "emails": ["a@example.com"] }
```

- `mode` — `"scan-only"`, `"dry-run"`, or `"live"`
- `limit` — optional cap on emails sent this run
- `emails` — optional; restrict the send to these candidates

Responds `{"message": "…", "totals": {…}}`, or a non-2xx with `{"error": "…"}`.
Returns 409 if a run is already in progress.

Sends work from the last scan and never start one. `"scan-only"` is the
exception — scanning is its entire job. A send with no scan to work from, or a
`"live"` send whose scan is over 15 minutes old, returns 409 asking for a Sync
portal click instead of quietly scraping.

**A selection cannot bypass the rules.** `emails` narrows the send; the window,
stage filter, portal check and dedupe are all still applied server-side, so
picking a checkbox can never send someone a third reminder or email someone who
already submitted.

### `GET /api/evaluations/rubric/<job_id>`

The standard a role's candidates are marked against: its grid from the Ajaia
rubric pack, or the derived grid file for a role the pack does not cover. Reads
`rubric_pack/` or `assessments/grid-<slug>.json`; derives nothing.

```json
{
  "exists": true,
  "covered_by_pack": true,
  "source": "pack",
  "unit": "AI Training",
  "grid_name": null,
  "version": "56f26bbd5a9a",
  "pack_version": "2026-08-12",
  "entity": "Ajaia",
  "assessment": "Ajaia AI Trainer Assessment (Director Level), 180 minutes",
  "spike": "Room command under skepticism",
  "seat": "The seat owns Workforce Training and Enablement…",
  "core_skill": "Teaching craft: turning an AI capability into a workflow…",
  "competencies": [{ "label": "Live demonstration", "asks": "…", "anchor": "…" }],
  "blocks": [
    {
      "key": "work_product", "label": "Work product", "points": 70, "asks": "…",
      "criteria": [
        {
          "key": "live_demo", "label": "Live demo execution", "weight": 20,
          "anchors": {
            "5": "Tool on screen running the taught workflow, input to output…",
            "3": "Pre-generated result walked through…",
            "1": "No tool on screen; slides read aloud…"
          }
        }
      ]
    }
  ],
  "auto_fails": ["Hard cap violation…", "No training and demo video."],
  "family_auto_fails": ["No training and demo video."],
  "red_flags": ["…"],
  "triage": [{ "key": "caps", "label": "Caps: video 5 to 8 minutes…" }],
  "tells": { "strong": "…", "weak": "…" },
  "gia": { "primary": ["Word Meaning", "Reasoning"], "secondary": ["Perceptual Speed"],
           "why": "…", "proxies": ["…"], "rules": { "…": "…" } },
  "reviewer": { "path": ["…"], "calibration": "…", "probes": ["…"] },
  "gaps": ["No numeric task here, so no proxy for Number Speed and Accuracy."],
  "architecture": {
    "blocks": [{ "key": "work_product", "label": "Work product", "points": 70 }],
    "bands": [{ "key": "best", "label": "Best", "min": 85, "advances": true }],
    "advance_min": 75,
    "routes": [{ "key": "priority", "label": "Priority review", "min": 5 }],
    "universal_auto_fails": ["…"],
    "fraud_tells": ["…"]
  }
}
```

`architecture` is always returned — the four blocks, the decision bands and the
triage routing are fixed across every grid in the pack, so a role with no grid
comes back with `exists: false` and just that, and the page can still show what
*will* be marked before anything is generated. `blocked` carries the reason a
family cannot be graded yet (the Implementation grid waits on an RDI brief that
does not exist). `repairs` lists any weight rescaling applied when a derived
grid was written.

### `POST /api/evaluations/rubric`

```json
{ "job_id": 4, "force": false }
```

Derives a pack-shaped grid from the role's crawled assessment — one model call,
written to `assessments/grid-<slug>.json` — and returns the same shape as the
GET plus a `message`. `force: true` overwrites an existing file, discarding hand
edits; the UI confirms first. **409 for a pack-covered role**: those grids are
hand-authored against the live assessment, and the endpoint will not replace one
with model output. 503 if no LLM credentials, 502 if the provider fails, 409 if
another run holds the lock.

### `POST /api/evaluations/grade`

Two modes.

```json
{ "job_id": 17, "limit": 5 }        // the role's pending queue, up to `limit`
{ "submission_id": 8336 }           // this one candidate, on demand
```

`job_id` walks the role's **pending** queue — the bulk path behind the "Grade
pending" button, capped at 25 per request because it runs inline.

`submission_id` grades one named candidate **whatever queue they are in**, which
is the point of it: the candidate a reviewer wants a score for is often the one
the queue will never reach — auto-rejected for a missing artefact, or already
scored and worth a second opinion after a rubric edit. Decision status is not
consulted, and re-grading overwrites the previous evaluation. This is the
drawer's "Evaluate now" / "Re-evaluate" button.

The one refusal is a submission with no answer text (400) — someone who started
and never submitted has nothing to mark, and the model would be paid to say so.
404 for an unknown submission or a role missing from Mongo, 503 without LLM
credentials, 502 if the provider fails, 409 if another run holds the lock.

Both modes return `{message, graded[], failed[], remaining}`; single-candidate
mode adds `submission`, the freshly-scored document, so the drawer can redraw
without a second request.

### `POST /api/evaluations/tiers/resolve`

```json
{ "job_id": 38, "force": false }
```

Matches each of a role's candidates to the Workable posting they applied to, so
the two dashboards on a tiered assignment stop being one pile behind a filter.
One paginated candidate list per posting, matched on email, written to
`rubric_tier` on the submission. Only the unmatched, unless `force`; a tier a
reviewer set by hand is never overwritten either way.

Returns `{message, written, unresolved, both, kept_manual, tier_counts}`.
`unresolved` is candidates Workable has never heard of, who stay on the default
tier. `both` applied to both postings and are graded at the default with a note
on the file. 409 on a role marked by a single grid, 502 if Workable cannot be
reached, 409 if another run holds the lock. **Changes no scores.**

### `POST /api/evaluations/tier`

```json
{ "submission_id": 8336, "tier": "associate", "note": "new grad, strong work" }
```

Moves one candidate to the other tier of their family's rubric by hand, marked
`manual` so no resolver run undoes it. `tier: null` clears the swap and hands
them back to the resolver. Does not regrade — re-run grading on that candidate
to mark them against the new grid. 400 for an unknown tier, 409 on a role with
one grid.

### `GET` / `POST /api/roles/<job_id>/managers`

**GET for anyone who can see the role; POST is admin only** — and this is the
endpoint where that matters most. This list *is* the access rule: an account
whose address is on it can open the role. A manager who could POST here could
add themselves to any role and read it a second later, which would make every
other check on these pages decorative.

The hiring managers who own a role. POST takes the **whole list** —
`{"managers": [{name, email, title}, ...]}` — and replaces what was there, so
removing someone is sending the list without them. The editor never has to
reason about a delete landing out of order with a save.

Addresses are lowercased and de-duplicated; a manager with no name falls back to
the local part of their address. An entry with no `@` is refused by name rather
than dropped quietly — a typo'd address that vanishes on save looks like the
save failed, and it would be typed again the same way. Returns the stored list
plus `known_managers`, everyone on record across all roles, which feeds the
name field's suggestions.

Stored on the role document as `hiring_managers`, which `portal_crawler` never
produces, so an ingest cannot overwrite it.

### `GET /api/shortlist/<job_id>?limit=&preview=&note=`

The top-N hand-off for a role: `candidates[]` in rank order, the `managers` it
would go to, and `last_send`. `preview=1` also returns `email` — subject, HTML
and plain text — rendered by the same function that sends, so the preview is
the message rather than a mock-up of it. `note` is the recruiter's own line,
dropped in above the table and escaped rather than templated.

Each row is `{rank, submission_id, name, email, resume_link, video_link,
assessment_url, submitted_at}`. **No score, band, verdict or criterion marks**,
by policy — see `SHORTLIST_SHOW_SCORES` in `config.py`. `assessment_url` is the
submission's page on the portal.

Only scored candidates appear (`evaluation.score` must be a number), and only
those not already moved along the pipeline. Mongo sorts a missing field below
any number, so without the type filter a thin role would pad its top 20 with
ungraded people who would read as ranked.

### `GET /api/shortlist/<job_id>/xlsx?limit=`

The same rows as an `.xlsx` attachment — byte-identical to what the email
attaches. Links are real hyperlinks behind short labels, header frozen, filters
on. 409 for a role with nothing scored, 503 if `openpyxl` is missing.

### `POST /api/evaluations/ingest`

**Admin only.** A full re-crawl of the portal on behalf of the whole company —
not a per-role action a manager fires from their own seat.

### `GET /api/evaluations/rejected?job_id=`

Every rejected candidate, de-duplicated by address, for the queue on the
Pipeline tab.

**Each row carries `already_told`, plus `told_at` and `told_how`,** answered
from the rejection ledger in one query — and the response splits the totals
into `already_told` and `waiting`. This is not decoration. The list answers
"who did the assessment reject", it gets read as "who is still owed an email",
and those coincide exactly once. The month after, ticking all of it would mail
two hundred people a second rejection out of a list that looked correct. The
page unticks on this flag and leaves those rows out of *select all*.

A `failed` ledger row is **not** `already_told`: we tried, it bounced, that
candidate has heard nothing.

### `GET /api/rejections?search=&status=&job_id=&limit=`

**Admin only**, like every route in this group — one click here mails several
hundred real people. The ledger, plus `stats`, plus the default subject and
message body the composer starts from.

> The three routes below — `/parse`, `/preview` and `/send` — are the bulk
> sender. **Nothing in the dashboard calls them.** Rejections are sent from the
> recruiter's own mail client; the UI only records who. Kept because rebuilding
> it is the expensive part. See the note above `_reject_jobs` in server.py.

### `POST /api/rejections/parse`

`{text | recipients, resend?}` → what a send *would* do, without doing any of
it. `{total, mailable, already, unsubscribed, unreadable[], over_cap}`.

Sends nothing, records nothing. The dashboard disables both action buttons
until this has answered, and re-disables them the moment the paste box is
edited — so there is no path from an edited list to a send that never re-read
it.

### `POST /api/rejections/import`

`{text | recipients, job_id?, note?}` → records them as already rejected.
**Emails nobody.** This is *Mark as emailed →* on the rejection panel, and the
migration path for the hundreds already mailed by hand out of a BCC field.
Idempotent on the address, so marking the same people twice is the same as
marking them once.

### `POST /api/rejections/send`

`{text | recipients, subject?, message?, job_id?, note?, resend?}` → **202** and
a job id; poll `GET /api/rejections/send/<job>` for `{state, done, total,
totals}`.

Started, not awaited, for the reason `/api/run` is. One personalised message
per candidate, never a BCC. Opt-outs and the ledger are applied inside
`rejections.send_bulk()` rather than here, so a CLI or a scheduled run cannot
reach a different answer. **409** when nobody on the list is new — with the
reason, rather than the lock's "a send is already in progress", which would be
a lie about why nothing happened.

Each candidate is written to the ledger as they are sent, not batched at the
end: a process that dies at message 300 leaves those 300 recorded, and the
re-run sends only the rest.

### `POST /api/rejections/preview`

`{subject?, message?, job_id?, name?, email?}` → the message as one candidate
would receive it, rendered by the same builder that sends. Given a real
`email`, the unsubscribe link in the footer is that recipient's own.

### `POST /api/rejections/remove`

`{emails: []}` → drops them from the ledger. *← Move back* on the rejection
panel. Un-sends nothing; it makes the system forget they were told, which puts
them back in the left column.

### `POST /api/shortlist/send`

```json
{ "job_id": 35, "limit": 20, "note": "Strong batch this round.", "to": null }
```

Mails the shortlist, one message per manager rather than one message with
everyone on it — the greeting is by first name, and a manager should not read
the others' addresses off the header of a mail about candidates.

`to` overrides the stored managers for a test send to yourself and does not
change who owns the role. **It is admin only** — it mails names, addresses and
CV links to whoever is named in it, and without it the send goes to the role's
own managers, which is the only address list this is for. A hiring manager may
send their own role's shortlist; they may not redirect it.

Partial success is reported, not raised: `{message, result: {sent[], failed[],
count}}`. Two of three landing is materially different from none landing, and a
recruiter about to click again needs to know which. **502 only when every
recipient failed.** 409 for a role with no manager or nothing scored, or if
another run holds the lock — grading, ingest, grid derivation and this share
one, so two clicks cannot deliver the same twenty people twice.

A successful send is recorded on the role as `shortlist_sends`, a growing
history rather than one overwritten stamp: "we already sent this role's twenty
on the 12th" is the question asked *before* clicking send, and a single field
cannot answer it after the second batch.

### `GET /review/<token>` and `GET /api/review/<token>`

The hiring manager's page and the data behind it. Reached with a token and
nothing else — no session, no login, no user id in the path — so each route
re-derives what the token may see rather than trusting anything the browser
sent alongside it.

`/review/<token>` serves the same HTML whatever the token is; the API decides
whether it is usable, so a dead link renders a sentence saying *which* kind of
dead rather than a bare 404. An unknown token (404), a revoked one (410) and an
expired one (410) are deliberately different answers: someone holding a
real-but-stale link needs to be told which they have.

The payload is `{role, manager, candidates[], expires_at, mailed_stages,
emails_enabled, can_book}`. `can_book` is resolved live against the role rather
than read off the token, because the commonest fix for "we have no booking link
for you" is the recruiter adding one afterwards — reading the snapshot would
leave the page insisting they still cannot book until somebody re-sent the whole
shortlist.

**No scores.** `evaluation` is projected out at the database and each row is
built from a fixed allowlist, so there is nothing in the response to leak.

### `POST /api/review/<token>/decision`

```json
{ "submission_id": 8215, "stage": "interview",
  "note": "Keen to dig into the incident write-up.", "interview_at": "2026-08-25T10:00" }
```

Writes to the pipeline with `source: "manager"` and `by: "Name <email>"`, then
emails the candidate through `candidate_mail` — the same path the dashboard
uses, duplicate suppression included.

403 if the candidate is not on the list this token was sent, whatever role they
are on. 409 for an interview when we have no booking link for this manager,
checked *before* the move so the board never says "booked" for someone who was
never given a way to book. 400 for a stage a manager may not set — returning
someone to the shortlist is the recruiter's undo, not theirs.

A failed candidate email does **not** fail the request, and its reason is
rewritten to a plain sentence: our mail provider's errors are internal detail,
and a hiring manager can do nothing with an API endpoint and an IP allowlist URL.

### `GET /api/shortlist/<job_id>/links` and `POST /api/shortlist/links/revoke`

The recruiter's view of what exists: per link, the manager, its state, when it
was created and first opened, how many views and how many decisions came through
it. Revoke is a flag, not a delete — the audit trail outlives the access it
granted.

### `POST /api/managers/cal-link`

`{email, cal_link}`. **A manager may set only their own**; an admin may set
anyone's. It is the link a candidate is sent to book with, so pointing somebody
else's at your own calendar would quietly take over their interviews.

Account-wide rather than per role: a manager has one
calendar, and a link stored per role would be typed three times and rot in two.
A link that was typed but did not survive normalisation is refused rather than
saved blank, because silently clearing it would show an empty box and no reason.

## Styling

`styles.css` is built on the Ajaia design system, declared as custom properties
at the top of the file: navy/sky/paper palette, Poppins for UI and Fragment Mono
for emails and log output, 8px and 2px radii only (no pills), and the site's
`cubic-bezier` easing curves at 180ms.

Dark navy is the default, matching ajaia.ai. A light "paper" variant kicks in
under `prefers-color-scheme: light` — both drive the same semantic tokens
(`--bg`, `--surface`, `--line`, `--text`), so nothing below the token block is
theme-aware.

The palette is monochrome blue, which leaves no obvious red/amber/green for
status. Instead the badges use emphasis: **`eligible` is the only filled badge**,
because it is the only state that needs a human to act. If you add states, keep
that hierarchy rather than reaching for new hues.

Poppins and Fragment Mono load from Google Fonts. To run fully offline,
download the two families into `frontend/fonts/`, swap the `<link>` in
`index.html` for `@font-face` rules, and the system fallbacks in `--font-sans`
cover the gap in the meantime.

## Notes

- `app.js` sets `const API = ""` (same origin). If you run the backend on a
  different port during development, set it and enable CORS there.
- **Every page needs an account.** `session.js` loads before `app.js` and
  `evaluations.js` on both dashboards: it wraps `window.fetch` so every request
  carries its CSRF header and a 401 sends the visitor to `/login.html?next=…`,
  and it draws the account chip in `.topbar-actions`. An admin sees every role;
  a hiring manager sees only the roles their address is listed on, and `/` and
  `app.js` redirect them to the evaluations page.
- **`is_admin` in the page decides what is DRAWN, never what is allowed.** Every
  endpoint makes its own check in `server.py`, and a role outside an account's
  scope answers 404. When adding an admin-only control, gate the route first and
  hide the button second — a control hidden with nothing behind it is not
  hidden.
- `server.py` still binds to `127.0.0.1` by default. A sign-in narrows who gets
  in; it does not make plain HTTP on a public interface safe, and a session
  cookie sent in the clear is a session anyone on the path can take.
- All interpolated values pass through `esc()`, so candidate names from Workable
  cannot inject markup.
- `review.html` / `review.js` / `review.css` are the only files an outsider ever
  loads. They share `styles.css` with the dashboards but deliberately not
  `evaluations.css`: the page is one task on a phone between meetings, not an
  operations console with the dangerous parts removed.
- `review.css` opens with `[hidden] { display: none !important; }`. Any class
  that sets `display` outranks the browser's own `[hidden]` rule, and the
  consequences here were not cosmetic — a shut dialog stayed laid out and its
  full-screen scrim silently ate every click on the page. Keep the rule.
- The shortlist email preview renders in an `<iframe srcdoc>` rather than inline.
  The message is a full document in its own light palette; letting it inherit the
  dashboard's dark tokens would show the recruiter something the manager will
  never see. The frame is grown to its content on load, so a twenty-row table
  does not scroll inside a drawer that also scrolls.
