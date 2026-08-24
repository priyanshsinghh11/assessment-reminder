# Assessment Reminder System — Audit Report

Date: 2026-08-24
Scope: whole repository (24 Python modules, 5 JS files, 3 HTML pages, config, git history)
Branch: `main` @ `9696f4c`
**No code was changed. This is a findings document only.**

---

## 1. How this was tested

Everything below was actually run against the working tree, not read off.

| Check | Command | Result |
|---|---|---|
| Syntax | `py_compile` on all 25 `.py` files | **0 errors** |
| Import | `importlib.import_module` on all 23 importable modules | **23/23 OK** |
| Static lint | `pyflakes *.py` (installed for this run) | **2 warnings** — see §5 |
| CLI smoke | `--help` on 10 entry points | 2 misbehave — see F-04, §4 |
| Access-control suite | `python test_access.py` (live Mongo + Flask test client) | **55 pass, 1 fail** — see F-08 |
| Live DB inspection | `mongo_store` — 29 roles, 7,900 submissions, 2 users, 9 review links | reachable |
| Scoring behaviour | direct calls into `evaluator._parse_verdict` with crafted verdicts | **defect reproduced** — F-03 |
| Email rendering | direct call into `shortlist.build_email` with a hostile link | **injection reproduced** — F-02 |
| Flag parsing | simulated `reminder.main()` argv handling | **defect reproduced** — F-04 |
| Git history | `git ls-files`, `git log` on env files | **secret leak found** — F-01 |
| Frontend XSS sweep | every `${...}` interpolation into `innerHTML` across 5 JS files | escaping is disciplined; one sink is not — F-02 |
| Route guard audit | script mapping all 41 Flask routes to their guard calls | **coverage is complete** — see §3 |

**What was NOT tested**, and should be said plainly:

- No live call was made to Workable, Brevo, the assessment portal, or the LLM provider. Doing so would spend money and could email real candidates. Everything about those paths below comes from reading the code, not exercising it.
- `AUTOMATION_ENABLED` is off, so no send path was executed end to end.
- There is no test coverage to run for scoring, CSV parsing, business-day maths, or dedupe — because none exists (§6, I-01).

**One thing I did that you should know about:** running `test_access.py` executes `cleanup()`, which deletes the account belonging to a **real hiring manager address borrowed from your live database** (see F-07). In this database that address (`jordanmiller@ajaia.ai`) had no account, so nothing was destroyed — I verified the `users` collection afterwards and both real accounts (`priyanshs@ajaia.ai`, `priyanshsingh855@gmail.com`) are intact. But the hazard is real and unconditional, and it is why F-07 is in this report.

---

## 2. Verdict up front

This is a **well-built codebase**. That is not a courtesy sentence — it is the finding that shapes everything else. The access-control layer is correct on all 41 routes, exception handling is disciplined (zero bare `except:`, one intentional `except: pass`), the frontend escapes consistently, and the reasoning behind nearly every non-obvious decision is written down next to the code that implements it. `pyflakes` finds two cosmetic warnings across 28,000 lines.

The defects that exist are therefore not sloppiness. They are **gaps at the seams** — places where one module's careful invariant is not carried by the module next to it:

- `shortlist.py` has an `_esc()` and uses it everywhere except one helper (F-02).
- `auth.py` hashes session tokens; `mongo_store.py` stores review tokens in the clear (F-13).
- Nine CLIs use `argparse`; the one that sends email hand-rolls `sys.argv` (F-04).
- The evaluator records `grid_complete: false`; the shortlist query and the manager's page never read it (F-03).

That pattern is the actionable takeaway: the risk in this system is not in any single file, it is in the handoffs.

---

## 3. What is genuinely good (so it does not get refactored away)

- **Access control is complete.** I mapped every one of the 41 routes to its guard calls. Every route that names a `job_id` calls `_role_guard`, every route that names a submission calls `_submission_guard`, and every machinery route calls `_require_admin`. There are no gaps. The `visible_job_ids()` "None means all, and a forgotten check fails closed for admins rather than open for managers" design is genuinely good thinking.
- **404-not-403 for unowned roles.** A manager cannot enumerate roles they do not own. Verified by the test suite against a real database.
- **Session design.** Cookie holds a random token, Mongo holds only its SHA-256, no role is baked into the cookie, the account is re-read every request so revocation is immediate. Correct.
- **CSRF** on all state-changing methods, with `hmac.compare_digest`, plus `SameSite=Lax`. Two locks, correctly layered.
- **The portal "safety catch."** `gather_state()` raising `PortalUnavailable` on an empty portal response — rather than treating everyone as "never started" — is the single most valuable line of defensive code in the repo.
- **`REVIEW_BUCKETS` per-queue fetching**, with the reasoning and the measurements recorded. This is real engineering against a real observed failure.
- **Allowlist-shaped `--review-only` mode.** Choosing an allowlist over a denylist because "a denylist fails open, and the next endpoint anyone adds is exposed" is exactly right.
- **Frontend escaping.** I checked every `${...}` that reaches `innerHTML` across all 5 JS files. The `esc()` discipline holds everywhere except the one server-side sink in F-02.
- **The documentation.** The README's "Why this approach" section, with measured numbers behind each decision, is better than most production systems have.

---

## 4. Defects found

Severity is about **consequence if it fires**, and each entry says whether it can fire today.

---

### F-01 — CRITICAL — Live credentials are committed to git

**Where:** `.env.bak-before-mongo`, tracked in commit `9696f4c`
**Fires today:** Yes. Already fired — the secrets are in history now.

`.gitignore` correctly excludes `.env`, but not `.env.bak*`. The backup file is tracked and contains what look like real, live credentials:

| Key | Value length |
|---|---|
| `WORKABLE_API_TOKEN` | 44 chars |
| `BREVO_API_KEY` | 89 chars |
| `PORTAL_PASSWORD` | 8 chars |
| `LLM_API_KEY` | 70 chars |
| `PORTAL_EMAIL` | 18 chars |

(`.env.example` was checked separately and correctly holds only placeholders.)

**Consequence:** the Brevo key can send mail as your domain. The Workable token reads every candidate record. The portal password reaches an admin surface holding 8,606 submissions. Anyone with repository access — now or in the future, including anyone who ever cloned it — has all four.

**Note that removing the file is not sufficient.** It is in history. The credentials must be **rotated**, and rotation is the fix; scrubbing history is cleanup afterwards.

---

### F-02 — CRITICAL — HTML injection in the shortlist email, landing in an unsandboxed dashboard iframe

**Where:** `shortlist.py:302` (`_link`), rendered by `frontend/evaluations.html:645` (`#mailFrame`)
**Fires today:** Yes, if any candidate has ever put a crafted string in a resume or video link field.

`shortlist.py` defines `_esc()` and uses it correctly in nine places — candidate names, emails, the recruiter note, review URLs. But `_link()` does not:

```python
def _link(url: str, label: str) -> str:
    if not url:
        return '<span style="color:#98a2b3;">—</span>'
    return (f'<a href="{url}" style="color:#0b2e8e;font-weight:600;'   # <-- url unescaped
            f'text-decoration:none;">{label}</a>')
```

It is called three times, at `shortlist.py:368,370,372`, with `row['resume_link']`, `row['assessment_url']` and `row['video_link']` — all **candidate-supplied**, typed into the assessment form and carried through the portal CSV.

**Reproduced:**

```
input  resume_link = '" onerror="alert(1)" x="'
output <a href="" onerror="alert(1)" x="" style="color:#0b2e8e;...">CV</a>

input  video_link  = 'javascript:alert(document.domain)'
output <a href="javascript:alert(document.domain)" style="...">Video</a>
```

**Why this is critical rather than cosmetic.** The generated HTML has two destinations:

1. The hiring manager's inbox. Most mail clients strip script, but attribute-breaking still permits link hijacking and phishing inside a mail that carries your branding.
2. **`#mailFrame` on the recruiter's dashboard**, via `frame.srcdoc = data.email.html` (`evaluations.js:2281`). And `#mailFrame` — unlike `#previewFrame` at `evaluations.html:611` and `review.html:178`, both of which correctly carry `sandbox=""` — has **no sandbox attribute**. `srcdoc` without `sandbox` inherits the parent origin. Injected script runs with the authenticated admin's session, on the page that holds every candidate's address and every send button.

**Important detail for whoever fixes this:** the missing `sandbox` is not an oversight, it is load-bearing. The `frame.onload` handler at `evaluations.js:2286` reads `frame.contentDocument` to grow the iframe to fit, and a `sandbox=""` iframe has an opaque origin where `contentDocument` is `null`. So "just add `sandbox`" silently breaks the grow-to-fit. The correct fix is to escape in `_link` (and validate the scheme is `http`/`https`); `sandbox="allow-same-origin"` would additionally close the sink without breaking the measurement.

---

### F-03 — HIGH — A truncated model reply produces a full-looking score, and the hiring manager cannot see it

**Where:** `evaluator.py:1845` (documented intent), `:1961` (renormalisation), `:2080` (the flag); `mongo_store.py:535` (`top_candidates`), `:1178` (`submissions_for_review`)
**Fires today:** Yes, whenever the model truncates — which the code itself documents as a known occurrence.

`_parse_verdict` drops criteria the model skipped and renormalises the remaining weights to 100. The reasoning is stated and defensible in isolation: *"one missing row should cost that candidate nothing, since the omission is the model's fault and not theirs."*

The problem is that there is **no floor on how few rows may remain**. The only guard is `if not marked: raise` — one criterion out of seven is enough.

**Reproduced** against the `ai_training` grid (7 criteria, weights 25/15/20/10/10/10/10):

```
marked 1/7 (25 of 100 weight)  ->  rubric_score = 100.0
marked 2/7 (40 of 100 weight)  ->  rubric_score = 100.0
marked 3/7 (60 of 100 weight)  ->  rubric_score = 100.0
marked 7/7 (complete)          ->  rubric_score = 100.0
```

A verdict covering a quarter of the rubric is numerically indistinguishable from a complete one.

**The mitigation exists but does not reach the person who acts on it.** `evaluator.py:2080` records `grid_complete: false`, and `evaluations.js:3162` renders an honest note about it. But:

- `mongo_store.top_candidates()` (`:535`) filters on `evaluation.score` being a number, `decision.status`, `pipeline.stage` and tier — **not** on `grid_complete`. A partial-verdict candidate ranks by that inflated score and lands on the shortlist.
- `mongo_store.submissions_for_review()` (`:1178`) projects `evaluation` out entirely — deliberately, so managers never see scores. Correct for its purpose, but it means the `grid_complete` warning **cannot** reach the manager's review page.

So the warning is visible only in the recruiter's drawer, on a page nobody is required to open, while the ranking it should qualify goes to the hiring manager unannotated. `calibrate.py`'s own docstring records "eight candidates in one role scored exactly 100" — worth re-checking those eight against `grid_complete` to see whether this already happened.

---

### F-04 — HIGH — `reminder.py` parses flags by hand; a typo'd flag is a live send

**Where:** `reminder.py:346-360` (`_arg_value`, `main`)
**Fires today:** No — `AUTOMATION_ENABLED` is off. Fires the moment automation is re-enabled, or immediately with `--force`.

Nine of the ten CLIs in this repo use `argparse`. The one that sends email to hundreds of people does not — it does `"--dry-run" in sys.argv`. Unrecognised flags are silently ignored, so an unrecognised flag is not an error, it is a **default to live send**.

**Reproduced:**

```
['--dry-run']              -> dry_run=True   SENDS_REAL_EMAIL=False
['--dryrun']               -> dry_run=False  SENDS_REAL_EMAIL=True
['--dry_run']              -> dry_run=False  SENDS_REAL_EMAIL=True
```

`--dryrun` and `--dry_run` are the two most plausible typos for the flag whose entire job is to stop mail going out.

Three further consequences of the same root cause:

- **`--help` does not work.** `python reminder.py --help` prints the "Automated runs are paused" warning and exits 0. The usage block in the docstring is unreachable. Confirmed.
- **`--limit abc`** raises an uncaught `ValueError` traceback rather than a message. Confirmed.
- **`--limit` with no value** silently becomes `None` — an unlimited run where the operator asked for a cap. Confirmed.

Also: `main()` never calls `sys.exit()` with a non-zero code. `PortalUnavailable` and an empty `ASSESSMENT_JOBS` both `return` after logging, so the process exits 0 and **cron cannot tell a failed run from a successful one**.

---

### F-05 — HIGH — The reminder state file is written non-atomically, and corruption silently means "remind everyone again"

**Where:** `utils.py:73` (`_save_state`), `utils.py:66` (`_load_state`)
**Fires today:** Yes, on any crash, kill, disk-full or power loss during a send.

```python
def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))   # truncate, then write
```

`write_text` truncates the file before writing. Interrupted, it leaves truncated JSON. And `_load_state` handles that by starting over:

```python
except (json.JSONDecodeError, OSError):
    log.warning("Corrupt state file. Starting fresh.")
return {}
```

An empty state means every candidate's `reminders_sent` reads as 0, so **every candidate in the window becomes eligible again**. The whole dedupe guarantee — "a maximum of 2 reminders, 2 business days apart" — rests on this one file.

This is made worse by frequency: `record_reminder` rewrites the entire file **once per email sent**, so a 200-email run has 200 truncate-and-rewrite windows.

The presence of `state/reminder_log.json.pre-incident-backup` (10 Aug, 58 KB, against a current file of 173 KB) strongly suggests this class of problem has already caused an incident.

The fix is the standard one: write to a temp file in the same directory, `os.replace()` onto the target. And a corrupt state file should probably be a **hard stop**, not a fresh start — "I have lost track of who was already emailed" is exactly the condition under which sending nothing is correct.

---

### F-06 — HIGH — SSRF: candidate-supplied URLs are fetched with no validation, over the authenticated portal session

**Where:** `resume_reader.py:171-197` (`fetch`), called from `ingest.py:343`
**Fires today:** Yes, on every `ingest.py --resumes` run.

`fetch()` takes a `resume_link` straight from the portal submission — a URL the **candidate typed** — passes it through `direct_url()` (which returns unrecognised hosts unchanged, `resume_reader.py:122`) and issues:

```python
getter.get(url, timeout=FETCH_TIMEOUT, stream=True,
           allow_redirects=True, headers={"User-Agent": USER_AGENT})
```

No scheme allowlist. No host allowlist. No private/link-local address block. Redirects followed.

A candidate can therefore direct your server at `http://169.254.169.254/…` (cloud metadata), at `http://127.0.0.1:5000/…`, or at any host reachable from the deployment network. The response is stored as `resume_text`, shown in the dashboard drawer, and fed into the LLM prompt.

**Two mitigating facts, stated honestly:**

- `_sniff()` (`resume_reader.py:125`) requires PDF or DOCX magic bytes before anything is extracted or stored, so arbitrary text cannot be exfiltrated this way. That substantially limits the blast radius — it is a probe primitive, not a general read primitive.
- The deployment is currently loopback-only (`PUBLIC_BASE_URL=http://127.0.0.1:5000`), so the reachable internal surface is small right now.

**One thing that deserves a second look regardless:** `ingest.py:343` passes the **authenticated portal session** into `fetch()`. The docstring says it is "only ever used for its connection pool today." `requests.Session` scopes cookies by domain, so credentials do not leak to arbitrary hosts — but it does mean a candidate who submits a `candidateassessments.ajaia.ai/admin/...` link gets that URL fetched **with your portal admin cookie attached**. The magic-byte check is the only thing standing between that and stored admin data. Using a clean session for candidate-supplied URLs costs nothing and removes the question entirely.

---

### F-07 — MEDIUM — `test_access.py` deletes a real hiring manager's account

**Where:** `test_access.py:82-88` (`cleanup`), called at `:73` and `:75`
**Fires today:** Yes, every time the test is run.

The suite borrows a real hiring-manager address from the live database so the manager account owns real roles — a good idea. But `cleanup()` then does:

```python
def cleanup(manager_email: str) -> None:
    for address in (ADMIN, manager_email):
        try:
            auth.delete_user(address)
        except Exception:
            pass
```

`auth.delete_user` removes the account outright and ends every session it holds. This runs **before** the test (to clear leftovers) and again after. So if the borrowed address belongs to somebody who actually has a dashboard account, running the regression suite **destroys that account and signs them out**, silently, with the exception swallowed.

In this database the borrowed address was `jordanmiller@ajaia.ai`, which had no account — I checked the `users` collection after running, and both real accounts are intact. But that is luck, not design: the address is chosen by `next(iter(sorted(owned_by)))`, so it changes as managers are assigned, and it will eventually land on somebody real.

The suite should create its own throwaway address and grant it roles, or refuse to run if the borrowed address already has an account.

---

### F-08 — MEDIUM — The access-control suite has a permanently failing assertion

**Where:** `test_access.py:143`
**Fires today:** Yes — every run.

```
FAIL  the admin gets every role    30/29
1 FAILED: the admin gets every role
```

The assertion is `len(as_admin["roles"]) == len(every)`, where `every` counts documents in the `roles` collection (29). But `/api/evaluations/roles` calls `_split_by_tier` (`server.py:1252`), which renders the tiered AI Strategist role as **two cards**, giving 30. The endpoint is right; the test's expectation predates the tier feature.

This matters more than a stale assertion normally would. This suite is, by its own docstring, *"the thing that notices"* when a route is added without a guard — the failure mode it exists to catch is silent by definition. A suite that has failed for weeks is a suite whose next real failure gets waved through. It exits 1, so it also breaks any CI that runs it.

**A latent second problem in the same area:** `test_access.py:145-147` asserts `sorted(r["id"] for r in as_manager["roles"]) == owned`. Because split roles emit two cards with the same `id`, this will break the same way the moment a manager is assigned to a tiered role.

---

### F-09 — MEDIUM — `brevo_client` does no HTML escaping at all

**Where:** `brevo_client.py:193` (`_build_html`)

`shortlist.py` has `_esc()`. `candidate_mail.py` has `_esc()`. `brevo_client.py` has neither — `name`, `role_title` and `assessment_url` are interpolated raw into the reminder email's HTML at lines 215, 222 and 224.

`role_title` and `assessment_url` come from `config.py` and are trusted. `name` is a **candidate name from Workable**. A candidate called `Ann <the> O'Brien` produces broken markup in the mail they receive.

Lower severity than F-02: this mail goes to the candidate themselves, and the dashboard preview for reminders prints the **plain-text** part to the terminal (`reminder.py:print_email_preview`), so there is no iframe sink here. It is a rendering-correctness bug and a consistency gap, not a live XSS. But it is the third email builder in the repo and the only one without escaping, which is exactly the kind of drift worth closing.

---

### F-10 — MEDIUM — `/api/logs` reads the entire log file into memory on every request

**Where:** `server.py:914`

```python
lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
return jsonify({"lines": lines[-limit:]})
```

To return the last 200 lines it reads and splits the whole file. `logs/reminder.log` is already **1.7 MB**, and `setup_logging` (`reminder.py:69`) uses a plain `logging.FileHandler` — **no rotation**, so it only grows. The dashboard polls this endpoint.

Two related points:

- Werkzeug's access log writes into the same file as the application log, so HTTP request lines are interleaved with lines naming candidates and roles across the whole funnel. That is candidate PII in an unrotated, unbounded file.
- `logs/` is correctly `.gitignore`d, so this is an operational concern rather than a disclosure one.

`RotatingFileHandler` plus a seek-from-end tail fixes both halves.

---

### F-11 — MEDIUM — The password policy's "obvious password" check never fires

**Where:** `auth.py:144`

```python
flat = re.sub(r"[^a-z]", "", password.lower())
if flat and flat in ("password" * 6, "qwerty" * 6, "abcdefghijklmnop"):
    raise AuthError("Password is too easy to guess. Pick another.")
```

`"password" * 6` is the 48-character string `passwordpassword…`. The comparison is against the **whole** flattened password, so this rejects exactly three literal strings and nothing else.

The docstring two lines above states the intent: *"Length alone lets `passwordpassword` through, and that is the one a person picks when the only rule they were given was a number."* The check does not catch `passwordpassword` — `flat` would be `"passwordpassword"`, which is `"password" * 2`, not `* 6`.

So with `PASSWORD_MIN_LENGTH = 12`, the only rules that actually bite are length and "at least 5 distinct characters." `passwordpassword`, `qwertyqwerty` and `password1234` all pass.

The hashing itself is correct — PBKDF2-HMAC-SHA256, 240k iterations, per-password salt, parameters stored with the hash, `hmac.compare_digest` on verify. This is only the weak-password screen.

---

### F-12 — MEDIUM — Login lockout is per-account with no IP dimension

**Where:** `auth.py:483-497`

After `LOGIN_MAX_ATTEMPTS` (8) failures, the **account** is locked for `LOGIN_LOCKOUT_MINUTES` (15). There is no per-IP counter and no global throttle.

Two consequences:

- **Denial of service.** Anyone who knows an admin's email address can lock them out for 15 minutes, on demand, indefinitely, from anywhere. Since account creation is admin-only and the login form correctly refuses to confirm whether an address exists, an attacker needs to guess the address — but `@ajaia.ai` addresses are not hard to guess.
- **Password spraying.** One attempt against each of many accounts never trips any counter.

The account lockout is the right primitive and is correctly implemented; it just needs an IP-scoped counter beside it.

---

### F-13 — MEDIUM — Review tokens are stored in plaintext, unlike session tokens

**Where:** `mongo_store.py:997-1014` (`create_review_link`) vs `auth.py:159` (`_token_hash`)

`auth.py` is explicit and correct about why session tokens are hashed:

> *"only its SHA-256 is stored, so a dump of the sessions collection is a list of expired-looking hashes rather than a drawer full of live keys."*

`create_review_link` stores the token as the document `_id` **in the clear**. A review token is a real credential: it opens a hiring manager's shortlist with no login, and `/api/review/<token>/decision` and `/api/review/<token>/invite` will move candidates and email them.

So a read of `review_links` hands over live, working credentials — the exact thing the session design was written to prevent, one collection over. There are 9 such links in the database now.

The token itself is `secrets.token_urlsafe(32)`, so it is not guessable; this is purely about what a database dump or backup yields. Storing the hash and looking up by hash is the same change already made for sessions.

---

### F-14 — MEDIUM — Flask's development server is the production server

**Where:** `server.py:3427` — `app.run(host=host, port=args.port, debug=False)`

There is no `gunicorn`, `waitress` or `uwsgi` in `requirements.txt`. The Werkzeug dev server is not built for production traffic and warns as much itself.

This matters specifically because `server.py`'s own docstring documents `--review-only --host 0.0.0.0` as **the process that may face the internet**. That is the dev server on a public interface. The code around it is admirably careful — it logs loudly about TLS, `SESSION_COOKIE_SECURE`, and loopback defaults — but the server underneath is still the dev one.

Also worth noting for whoever deploys: `_run_lock` (`server.py:85`) is a `threading.Lock`, which is per-process. Under a multi-worker WSGI server it stops preventing the double-send it exists to prevent, and would need to become a Mongo-backed lock.

---

### F-15 — LOW — No Flask error handlers registered

**Where:** `server.py` — four hooks registered (2× `before_request`, 2× `after_request`), **zero** `errorhandler`.

An unhandled exception in any route returns Werkzeug's default HTML 500 page. The frontend's `api()` helper (`evaluations.js:158-172`) handles this gracefully — it wraps `resp.json()` in a `try/catch` and falls back to `HTTP ${resp.status}` — so it degrades to a toast reading "HTTP 500" rather than breaking. That is fine but uninformative, and a stack-trace-bearing HTML page is not the right thing to serve from an authenticated surface.

---

### F-16 — LOW — Deployment configuration is still local-only

Resolved values, read from the live `config.py`:

| Setting | Value | Note |
|---|---|---|
| `SESSION_COOKIE_SECURE` | `False` | session cookie will travel over plain HTTP |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:5000` | **review links minted today are dead for any manager** |
| `PORTAL_ADMINS` | `()` | no bootstrap admin configured |
| `PORTAL_ADMIN_PASSWORD` | unset | — |

`shortlist.is_loopback()` already warns about the second one at startup in review-only mode, which is good. Flagged here as a pre-launch checklist item rather than a code defect.

---

### F-17 — LOW — Housekeeping

| Item | Where | Note |
|---|---|---|
| `_hedge_tmp.py` is committed | repo root, tracked | A scratch LLM benchmark. Reads the live `LLM_API_KEY` and spends tokens if run. Not imported by anything. |
| Unused import | `candidate_mail.py:38` | `PIPELINE_AUTO_EMAIL` imported, never used (pyflakes) |
| f-string with no placeholders | `test_access.py:293` | cosmetic (pyflakes) |
| Duplicate README row | `README.md:1256-1257` | `candidate_mail.py` listed twice with different descriptions |
| README Files table incomplete | `README.md:1242` | omits `calibrate.py`, `regrade.py`, `_hedge_tmp.py`, `frontend/app.js`, `frontend/evaluations.*`, `crontab.example` |
| `requirements.txt` unpinned | all `>=`, no upper bounds, no lockfile | An 8-file dependency set with no reproducible install |
| `get_db()` init race | `mongo_store.py:43-46` | Two threads can both build a `MongoClient`; one is dropped. Benign, leaks one pool. |
| Portal sessions never closed | `portal_scraper.py` | `_login()` returns a `Session` that is never `.close()`d |

---

## 5. Static analysis results in full

`pyflakes` across all 25 root `.py` files — **2 findings, both cosmetic**:

```
candidate_mail.py:38:1: 'config.PIPELINE_AUTO_EMAIL' imported but unused
test_access.py:293:11: f-string is missing placeholders
```

Also checked and **clean**:

- Bare `except:` — **0 occurrences**
- `except Exception: pass` — **1 occurrence**, `test_access.py:86`, intentional and commented
- `TODO` / `FIXME` / `HACK` / `XXX` — **0 occurrences** across all Python and JS
- Mutable default arguments — none found
- SQL/NoSQL string-built queries — none; all Mongo queries are dict literals with parameterised values

For a 28,000-line codebase this is an unusually clean result and is worth stating alongside the defects above.

---

## 6. The callback and hook surface

You asked specifically about callbacks. Here is the complete inventory.

### Server-side: Flask request hooks

Four hooks, and **execution order is load-bearing**:

| Order | Hook | Function | Purpose |
|---|---|---|---|
| 1 | `@app.before_request` | `_guard_review_only` (`server.py:118`) | 404s everything outside the review allowlist when `--review-only` |
| 2 | `@app.before_request` | `_guard_auth` (`server.py:227`) | Resolves the session onto `g.user`, enforces 401/CSRF/`must_change` |
| 3 | `@app.after_request` | `_protect_review_urls` (`server.py:134`) | `Referrer-Policy`, `Cache-Control`, `X-Robots-Tag` on review paths |
| 4 | `@app.after_request` | `_protect_dashboard` (`server.py:298`) | `nosniff`, `X-Frame-Options`, CSP `frame-ancestors`, `no-store` on auth |

The ordering dependency is real and is documented in `_guard_auth`'s docstring: it runs *after* `_guard_review_only` and deliberately skips the review surface, because that surface carries its own credential in the URL. Note that `before_request` order in Flask is **registration order**, so this is coupled to the physical order of the decorators in the file — moving a function would change behaviour.

**Missing:** no `@app.errorhandler`, no `@app.teardown_request`, no `@app.teardown_appcontext`. See F-15.

### Server-side: the guard functions the routes call

These are conventional calls rather than framework callbacks, but they are the actual access-control surface, so they belong in this inventory:

| Guard | `server.py` | Used by |
|---|---|---|
| `_require_admin()` | 366 | 6 routes — machinery only |
| `_role_guard(job_id)` | 424 | 13 routes that name a role |
| `_submission_guard(sub, id)` | 439 | 7 routes that name a candidate |
| `_review_guard(token)` | 2527 | 4 token-authenticated review routes |
| `_mongo_guard()` | 1060 | 30 routes |
| `_scope()` / `_is_admin()` | 405 / 400 | list endpoints that narrow rather than refuse |

I audited all 41 routes against this table. **Coverage is complete** — see §3.

### Concurrency callbacks

- `ThreadPoolExecutor` + `as_completed` — `server.py:2147`, bounded by `LLM_CONCURRENCY`, per-future exceptions caught individually so one failed grading does not kill the batch. Correct.
- `threading.Lock` `_run_lock` — `server.py:85`, non-blocking `acquire`, released in `finally`. Correct **within one process** — see the F-14 note.

### Client-side callbacks

| Kind | Count / location |
|---|---|
| `addEventListener` | 112 total — `evaluations.js` 74, `app.js` 12, `review.js` 17, `session.js` 6, `login.js` 3 |
| `iframe.onload` | 2 — `evaluations.js:2286`, `:3100`; both grow `#mailFrame` to fit by reading `contentDocument` (this is why it is unsandboxed — F-02) |
| `setTimeout` debounce | `evaluations.js:1603`, `review.js:501` — 500 ms debounce on invite-preview refresh |
| `setTimeout` toast dismiss | `app.js:379`, `evaluations.js:154`, `review.js:73` |
| `setInterval` | **none** — no polling timers |
| Central `fetch` wrapper | `api()` — `evaluations.js:158`, injects the CSRF header, attaches `status` and `body` to thrown errors |

The invite-preview flow uses a **sequence-number guard** (`compose.seq`, `evaluations.js:1655`) so a slow response from a superseded request cannot overwrite a newer preview. That is the correct pattern and it is worth pointing out that it was done properly.

### External callbacks

**There are none, and that is a deliberate design property worth stating.** No webhooks are registered or received — not from Workable, not from Brevo, not from the portal. Every external interaction is outbound and poll-shaped: the portal is scraped, Workable is paginated, Brevo is fired and forgotten, the LLM is called synchronously.

The consequences are worth being explicit about:

- **No inbound attack surface** from third parties. Nothing external can trigger work in this system.
- **No delivery feedback.** Brevo bounces, spam complaints and unsubscribes never reach this system. `record_reminder` is called on the API call succeeding (`reminder.py:325`), which means "Brevo accepted it," not "the candidate received it." A candidate whose address hard-bounces is recorded as reminded and will never be chased again — and nobody will know.
- **Staleness is structural**, which the system handles honestly: the dashboard serves the last scan and says how old it is, and a live send refuses to work from a scan older than 15 minutes.

---

## 7. Improvements worth making

Separate from defects — these are things that are not broken but would make the system meaningfully better.

### I-01 — There are no tests except the access-control suite

`test_access.py` is the only test in the repo, and it tests exactly one thing (who can see which role). It tests that thing well and for a well-argued reason.

But the following have **zero automated coverage**, and several are pure functions that would be trivial and fast to test with no database, no network and no spend:

| Untested | Why it matters |
|---|---|
| `utils.business_days_between` / `_since` / `_ago` | The reminder window is defined by these. An off-by-one emails the wrong cohort. |
| `utils.should_send_reminder` | This is the entire "max 2 reminders, 2 days apart" guarantee. |
| `evaluator._parse_verdict` | Produces the score every hiring decision rests on. F-03 would have been caught by one test. |
| `evaluator._json_object` | Handles malformed model output — the path most likely to see novel input. |
| `evaluator._grounded` / `_contiguous` | Quote-grounding, which `calibrate.py` reports a rate for. |
| `portal_scraper` CSV parsing + `get_portal_emails` | Feed the "has this person started?" decision. |
| `shortlist.build_email` / `_esc` / `_link` | F-02 would have been caught by one test. |
| `resume_reader._sniff` / `direct_url` | The only thing limiting F-06's blast radius. |
| `tier_resolver` | Decides which standard a candidate is judged against. |

The pattern to copy is already in the repo: `test_access.py` uses `server.app.test_client()` with no live server. A `tests/` directory with pytest, exercising the pure functions above, would take an afternoon and would have caught two of the four HIGH findings in this report.

### I-02 — Give the state file the same durability the rest of the system has

Beyond the atomicity fix in F-05: `state/reminder_log.json` is a single JSON blob holding the send-dedupe guarantee, while everything else of consequence lives in MongoDB. Reminder history arguably belongs in Mongo too — it would get atomic per-candidate writes, concurrent safety, and would survive a file-level accident. The 477 entries currently in it would migrate in one pass.

### I-03 — Stop re-reading the state file per candidate

`should_send_reminder`, `get_reminder_count` and `record_reminder` each call `_load_state()`, which re-reads and re-parses the whole file.

Measured on the current file (173 KB, 477 entries): **1.33 ms per call**, so 200 candidates costs ~270 ms of pure re-parsing. Not a crisis today — but it is O(filesize) per candidate and the file only grows, and `load_reminder_state()` already exists specifically to be called once. `gather_state()` uses it correctly; `send_batch()` does not.

### I-04 — Put a coverage floor on renormalisation, and carry the flag to where it is read

The fix for F-03 has two halves and both are needed:

1. Refuse (or re-request) a verdict below some fraction of total weight — say 80%. `if not marked: raise` is too low a bar for a number that drives hiring.
2. Make `grid_complete` consequential rather than merely displayed: exclude incomplete verdicts from `top_candidates()`, or surface the flag on the manager's review row. Right now the system knows the score is unreliable and tells only the one person who does not act on it.

### I-05 — Add a first-class "operational readiness" check

Several config values are only wrong at the moment they matter — `PUBLIC_BASE_URL` on loopback is invisible until a manager cannot open their link. `shortlist.is_loopback()` already does this for one value at startup. A `python check_config.py` that verifies `PUBLIC_BASE_URL` is reachable, `SESSION_COOKIE_SECURE` matches the scheme, an admin account exists, Mongo is up, and every `ASSESSMENT_JOBS` entry has a `portal_job_id` (all 92 currently do — verified) would turn a class of silent misconfiguration into one command.

### I-06 — Split the log stream

Werkzeug access lines and application lines share `logs/reminder.log`. Separating them makes the application log readable, makes rotation policy independent, and keeps candidate PII in one place rather than two.

### I-07 — Reconsider whether managers should be able to spend tokens

`/api/evaluations/grade` is guarded by `_role_guard` but **not** `_require_admin` — a hiring manager can trigger grading on a role they own, up to 25 submissions per call. That is defensible (it is their role), but it spends real money on a shared quota, and the surrounding design is otherwise consistent that "the machinery" is recruiting-team-only. Worth a deliberate decision rather than leaving it implicit. Not filed as a defect because it may well be intended.

### I-08 — `server.py` is 3,430 lines

It is well-organised and heavily sectioned, so this is not urgent. But it holds 41 routes, the auth layer, the guards, the tier logic, and the scan cache. Splitting the route groups into blueprints (auth / evaluations / pipeline / shortlist / review) would make the guard-coverage property in §3 checkable at a glance instead of by script.

---

## 8. Suggested order of work

**Before anything else — today:**

1. **F-01** — rotate the Workable token, Brevo key, portal password and LLM key. They are in git history; rotation is the fix, history scrubbing is cleanup afterwards. Add `.env*` (with a `!.env.example` exception) to `.gitignore`.

**Before the next shortlist is sent:**

2. **F-02** — escape and scheme-validate `url` in `shortlist._link`; add `sandbox="allow-same-origin"` to `#mailFrame`.
3. **F-03** — coverage floor in `_parse_verdict`, and make `grid_complete` filter `top_candidates()`.

**Before `AUTOMATION_ENABLED` is turned back on:**

4. **F-04** — move `reminder.py` to `argparse`; add non-zero exit codes.
5. **F-05** — atomic write for the state file; treat corruption as a stop, not a fresh start.

**Next sprint:**

6. **F-07, F-08** — fix the test suite so it neither deletes real accounts nor fails permanently. It is the guard on your guards; it needs to be trustworthy.
7. **F-06** — scheme/host allowlist and a clean session in `resume_reader.fetch`.
8. **F-11, F-12, F-13** — password screen, IP-scoped lockout, hash review tokens.
9. **I-01** — a `tests/` directory covering the pure functions. Two of the four HIGH findings here would have been caught by it.

**Before public deployment:**

10. **F-14, F-16** — real WSGI server, TLS, `SESSION_COOKIE_SECURE=1`, real `PUBLIC_BASE_URL`, and replace `_run_lock` with a cross-process lock.
11. **F-10** — log rotation and a seek-based tail.

---

## 9. Summary

| Severity | Count | IDs |
|---|---|---|
| Critical | 2 | F-01, F-02 |
| High | 4 | F-03, F-04, F-05, F-06 |
| Medium | 8 | F-07 … F-14 |
| Low | 3 | F-15, F-16, F-17 |
| Improvements | 8 | I-01 … I-08 |

Two of the four HIGH findings (F-03, F-04) and both CRITICALs are **single-line or single-function fixes**. F-05 is a five-line change. The engineering underneath is sound; what this report mostly describes is a set of small gaps between modules that are each individually careful.
