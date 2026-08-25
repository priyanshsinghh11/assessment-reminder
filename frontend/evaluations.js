/*
 * Evaluations dashboard.
 *
 * Roles come from Mongo in one request; a role's candidates are fetched only
 * when that role is opened. Loading all 4200 submissions up front would move
 * megabytes of answer text the user never looks at.
 */

const state = {
  roles: [],
  evaluatorConfigured: false,
  // What a stage move does to the candidate's inbox, straight from the server.
  // `auto: false` is the paused mode: moves are silent and every candidate
  // email leaves from the Send button in their card, after a preview.
  //
  // `interview_locked` is the server refusing this page the interview stage
  // altogether. It is not a preference and there is no override: an invitation
  // is signed by the hiring manager and points at their calendar, so it is
  // written and sent by them, on their review link. Everything here reads the
  // flag rather than hard-coding the rule, so the buttons on screen and the
  // routes behind them cannot drift apart.
  mail: { enabled: true, auto: false, interview_locked: true,
          interview_locked_reason: '' },
  activeRoleId: null,
  // Which posting's half of a role is open, or null for a role that has only
  // one. An assignment sat by two postings at different seniorities is shown
  // as two cards over one pile of submissions -- same job id, same portal
  // assignment, two standards -- so the id alone stops identifying what is on
  // screen and this rides beside it through every request the page makes.
  activeTier: null,
  activeRole: null,
  // Which of the open role's four sections is on screen, and which of them
  // have already fetched. The shortlist and the board are separate requests,
  // and a reviewer who only wanted the scores should not pay for them.
  tab: 'candidates',
  loaded: {},
  candidates: [],
  rejected: [],
  rejectedReasons: {},
  sort: { key: 'score', dir: 'desc' },
  // The open role's grid. Never rendered as a standard -- it is here so the
  // criterion columns can label themselves and the drawer can quote the pack's
  // own bands. See the rubric section.
  rubric: null,
  showMatrix: false,
  // The board below the roles: what happened to a candidate after the score.
  // One stage is loaded at a time -- the tabs are three different questions,
  // and only one of them is ever on screen.
  pipeline: {
    stage: 'interview',
    rows: [],
    counts: { interview: 0, hired: 0, rejected: 0 },
  },
  // The hand-off to the hiring manager: who owns the open role, and the top-N
  // that would be mailed to them. Fetched the first time its tab is opened for
  // a role, and not again while that role stays open.
  shortlist: {
    // The review links minted by past sends, and whether the address they were
    // built from is one a manager can actually reach.
    links: [],
    unreachable: false,
    publicBase: '',
    managers: [],
    rows: [],
    // Scored but unrankable: a part-filled grid, held out of the ranking by
    // top_candidates and listed here so nobody is dropped silently.
    heldBack: [],
    lastSend: null,
    // Whether this hand-off's spreadsheet carries the AI score. The tick is
    // the recruiter's, per send; the server owns the default and answers it in
    // `show_scores`, so the box is not set from a guess made here.
    showScores: false,
    // Dirty until saved. The chip list is edited locally so adding three
    // managers is three keystrokes and one save, not three round trips.
    dirty: false,
  },
  // The top N a hiring manager is working, and the invitation they write for
  // them. It lives on the Pipeline tab, one panel above the board it feeds:
  // the people still awaiting a decision, then the people who have had one.
  //
  // `token` is the manager's own review workspace for the open role, minted by
  // /api/managers/review-link. Everything the composer does goes through it,
  // which is what keeps ONE door into the interview stage -- the dashboard's
  // own /api/pipeline still refuses that move, and this page is not a second
  // way round it. It is also the authority on WHO may be invited: the picks
  // this section offers are the token's own list, so a row on screen can never
  // be a row the server would refuse.
  top: {
    token: null,
    jobId: null,
    limit: 20,
    rows: [],
    picked: new Set(),
    canBook: false,
    emailsEnabled: true,
    placeholders: [],
    manager: null,
    empty: '',
    loading: false,
  },
  knownManagers: [],
  shortlistSize: 20,
  shortlistMax: 100,
  // Who is signed in, straight from the server on every roles load.
  // `isAdmin` decides what is DRAWN and nothing else -- the server refuses
  // what it refuses whatever this page thinks. Default true so a page that
  // somehow renders before the answer arrives is not silently stripped of
  // recruiting controls for the recruiting team.
  account: null,
  isAdmin: true,
  // Whether the score column is drawn. Its own flag rather than `isAdmin`,
  // because a hiring manager reads scores here too unless the server has been
  // set otherwise -- MANAGER_DASHBOARD_SCORES. Straight from the roles payload
  // on every load, and true by default for the same reason isAdmin is.
  scoresVisible: true,
  // How much of the candidate list is on screen. null until the account is
  // known, because the sensible default is not the same for both -- see
  // applyAccountView().
  topN: null,
  users: [],
  // Which account's role picker to put the cursor back into after the
  // list re-renders. Adding a role costs a round trip and a redraw, and
  // people add roles in threes; without this each one starts by hunting
  // for the box again. See pickRole().
  focusPicker: null,
};

const $ = (id) => document.getElementById(id);

/* Show or hide by id, tolerating an id that is not on the page.
 *
 * applyAccountView() runs inside loadRoles()'s try, so a `null.hidden = ...`
 * there does not fail loudly -- it is caught as "Could not load roles" and the
 * grid, the stats and the hash restore below it never run. The whole dashboard
 * goes blank because one button was missing.
 *
 * It goes missing for one boring reason: a browser holding a cached
 * evaluations.html against a freshly deployed evaluations.js. Neither file is
 * fingerprinted, so the two do drift apart for a reload or two after a deploy,
 * and the elements this page hides by account are exactly the ones added most
 * recently. Draw what is there; skip what is not. */
function setHidden(id, hidden) {
  const el = $(id);
  if (el) el.hidden = hidden;
  else console.warn(`setHidden: #${id} is not on this page (stale HTML?)`);
}

/* --- helpers ---------------------------------------------------------- */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('is-error', isError);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, isError ? 8000 : 4500);
}

async function api(path, options) {
  const resp = await fetch(path, options);
  let body = {};
  try { body = await resp.json(); } catch { /* non-JSON error page */ }
  if (!resp.ok) {
    // The status and the reply travel with the error. Most callers only want
    // the sentence, but a few have to tell one refusal from another -- "we do
    // not have your booking link" (409, needs: cal_link) is something the
    // manager can fix on this page, and a red toast is the wrong place to say
    // so. See openComposer() and loadTopCandidates().
    const err = new Error(body.error || `HTTP ${resp.status}`);
    err.body = body;
    err.status = resp.status;
    throw err;
  }
  return body;
}

const postJson = (path, payload) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

function shortDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

const STATUS_LABEL = {
  scored: 'Scored',
  pending: 'Pending',
  rejected: 'Rejected',
  in_progress: 'Not submitted',
};

const STATUS_CLASS = {
  scored: 'badge-scored',
  pending: 'badge-pending',
  rejected: 'badge-rejected',
  in_progress: 'badge-inprogress',
};

// The portal's own review queue, which is a different thing from our decision
// status above: it says what a human on the portal has done, not what we have.
// Only Pending Review is worth a badge -- "new" is the default queue and would
// mark nearly every row. These candidates were invisible here until the ingest
// started fetching that queue, so the badge is mostly there to say "this one
// is already sitting in someone's review pile" before you act on the score.
const PORTAL_QUEUE_LABEL = { pending: 'In portal review' };

const portalQueue = (c) =>
  PORTAL_QUEUE_LABEL[String(c.review_status || '').trim().toLowerCase()] || '';

// Why a candidate landed in the reject box, in words rather than a DB enum.
const REASON_LABEL = {
  missing_video: 'No video submitted',
  missing_resume: 'No resume submitted',
  missing_video_and_resume: 'No video or resume',
  manual_override: 'Set by hand',
  awaiting_evaluation: 'Awaiting AI evaluation',
  ai_evaluated: 'Evaluated by AI',
  not_submitted: 'Started but never submitted',
};

// Coloured on the pack's bands, not on a separate visual scale, so the colour
// and the word can never disagree: Best 85+, Better 75-84, Good 60-74, Okay
// below 60. Best and Better share the strong colour -- they are one side of the
// advance bar, and a second green would read as a second bar.
function scoreClass(score) {
  if (score == null) return 'score-none';
  if (score >= 75) return 'score-strong';
  if (score >= 60) return 'score-mid';
  return 'score-low';
}

/* Whether the AI stopped part-way through this candidate's rubric.
 *
 * `score_provisional` arrived with the coverage fields; `grid_complete` has
 * been stored since the grids went in, so verdicts marked before either field
 * existed still answer this correctly. */
const isProvisional = (ev) => !!ev
  && (ev.score_provisional === true || ev.grid_complete === false);

/* A partial grid is renormalised to 100 by the scorer, so nothing about the
 * NUMBER says it was built from part of the rubric -- one row marked 5 comes
 * out at exactly 100.0, the same figure a flawless full grid produces. The
 * number therefore never appears anywhere in this dashboard without this mark
 * beside it, and the mark is what a recruiter sorts, filters and re-grades on.
 *
 * Kept deliberately small and unmissable rather than a colour change: the score
 * colours already mean "how good", and overloading them with "how sure" is how
 * a recruiter reads a caveat as a band. */
/* The score column, or its absence.
 *
 * DRAWN FOR A HIRING MANAGER TOO, unless the server says otherwise. The number
 * is safe here in a way it is not in their inbox: the row it sits on opens a
 * drawer holding the grid that produced it, the anchors each mark was given
 * against, the brief and the CV read. A "78" you can open and disagree with is
 * the opposite of a "78" that decides the interview on its own. Turning it off
 * is `MANAGER_DASHBOARD_SCORES=0`, which withholds `evaluation` from the
 * payload as well -- see MANAGER_SUBMISSION_FIELDS.
 *
 * With it off, the column says nothing rather than "—", which would read as a
 * grading failure and send them asking why half the candidates are unmarked.
 * `grading_incomplete` still reaches them and still shows: it is the one fact
 * about the marking they need, because it is why somebody sits where they sit. */
function scoreCell(ev, row) {
  if (!state.scoresVisible) {
    return `<td class="num dim" title="Scores are the recruiting team's. `
      + `Rank and the work itself are what this page shows you.">${
        row?.grading_incomplete ? partialBadge() : '·'}</td>`;
  }
  return `<td class="num"><span class="score-cell ${scoreClass(ev?.score)}">${
    ev ? fmtScore(ev.score) : '—'}</span>${provisionalMark(ev)}</td>`;
}

/* The same mark provisionalMark() draws, for a reader who has no verdict
 * object to derive it from. */
function partialBadge() {
  return `<span class="provisional-mark" title="Our AI grader did not finish `
    + `this candidate's rubric, so their position is not a like-for-like `
    + `comparison. Read their answers before you decide.">partial</span>`;
}

function provisionalMark(ev) {
  if (!isProvisional(ev)) return '';
  const of = ev.grid_of;
  const marked = ev.grid_marked;
  const detail = (typeof marked === 'number' && typeof of === 'number')
    ? `Only ${marked} of ${of} criteria were marked${
        typeof ev.grid_coverage === 'number'
          ? ` — ${Math.round(ev.grid_coverage * 100)}% of the rubric's weight`
          : ''}, and the total you see is those rows scaled up to 100.`
    : 'The AI did not mark every criterion, and the total you see is the '
      + 'marked rows scaled up to 100.';
  return `<span class="provisional-mark"
    title="${esc(detail)} It is not comparable with a fully marked score and is held off shortlists until it is re-graded.">partial</span>`;
}

/* Which required artefacts this candidate never submitted.
 *
 * Written by the evaluator from our own records rather than from the model's
 * reply, so an empty list means "they handed everything in", not "the model
 * did not mention it". Absent entirely on every verdict marked before the
 * field existed, which reads the same way as an empty one and is correct:
 * those candidates were auto-rejected before grading, so none of them can be
 * one of these. */
const gradedWithout = (ev) => (ev && Array.isArray(ev.graded_without))
  ? ev.graded_without.map((f) => f.replace('_link', '')) : [];

/* The counterpart to provisionalMark, and there for the same reason: nothing
 * about the NUMBER says it was produced without a video.
 *
 * These candidates are auto-rejected at ingest and never reach a bulk grading
 * run, so a score on one of them exists only because a reviewer pressed
 * "Evaluate now" on purpose. That is a deliberate act, and the mark is what
 * tells the next person reading the row that it happened -- and that the
 * missing artefact was already paid for in the grid rather than being an
 * oversight they need to chase. */
function withoutMark(ev) {
  const missing = gradedWithout(ev);
  if (!missing.length) return '';
  const names = missing.join(' and ');
  return `<span class="badge badge-without"
    title="No ${esc(names)} was submitted. The written work was graded normally; where this rubric prices a recording, that row was marked at its 1 anchor. The absence never triggered an auto-fail.">no ${esc(names)}</span>`;
}

/* Evaluations recorded under the old vocabulary. The band is a pure function
 * of the score, so relabelling them is faithful rather than a rewrite -- and
 * leaving one "Hold" in a column of Good/Better reads as a different thing
 * having happened to that candidate. Nothing is written back. */
const LEGACY_REC = {
  Advance: 'Better', Hold: 'Good', Reject: 'Okay', 'Auto-fail': 'Not scored',
};

// Strongest first. Drives the Verdict column's sort, so the order is the one
// the words claim rather than the alphabet's.
const REC_RANK = ['Best', 'Better', 'Good', 'Okay', 'Not scored'];

/* The word for a verdict, and the band it belongs to. Old records are mapped
 * by label; anything the pack stops returning falls through unchanged rather
 * than disappearing. */
function recLabel(ev) {
  const raw = String(ev?.recommendation || '').trim();
  if (!raw) return '';
  if (LEGACY_REC[raw]) return LEGACY_REC[raw];
  // A pre-pack "Strong yes" style verdict, or a band this build has not heard
  // of: shown as recorded.
  return raw;
}

/* The bands come with the rubric so the page never states a bar the server
 * disagrees with. This fallback only covers the moment before that response
 * lands, and matches BANDS in rubric_pack.py. */
const FALLBACK_BANDS = [
  { key: 'best', label: 'Best', min: 85 },
  { key: 'better', label: 'Better', min: 75 },
  { key: 'good', label: 'Good', min: 60 },
  { key: 'okay', label: 'Okay', min: 0 },
];

const bandList = () => (state.rubric?.architecture?.bands?.length
  ? state.rubric.architecture.bands : FALLBACK_BANDS);

/* "85+", "75–84", "below 60" — read off the neighbouring cuts, so adding a band
 * to the pack does not need a second edit here. Bands are ordered high to low. */
function bandSpan(list, i) {
  const above = list[i - 1];
  if (!above) return `${list[i].min}+`;
  return list[i].min <= 0 ? `below ${above.min}` : `${list[i].min}–${above.min - 1}`;
}

/* The band a total landed in, as the reviewer's own words plus the numbers
 * that put it there: "Better 75–84". */
function bandRange(score) {
  const list = bandList();
  const i = list.findIndex((b) => score >= b.min);
  const at = i === -1 ? list.length - 1 : i;
  return `${list[at].label} ${bandSpan(list, at)}`;
}

// Totals land on a tenth (score x weight / 5 rarely comes out whole), and a
// trailing ".0" in a dense table is noise.
const fmtScore = (score) => (typeof score === 'number'
  ? (Number.isInteger(score) ? String(score) : score.toFixed(1)) : '—');

const recClass = (rec) => 'badge-' + String(rec || '').toLowerCase().replace(/\s+/g, '-');

/* --- roles ------------------------------------------------------------ */

async function loadRoles() {
  $('runMeta').textContent = 'Loading roles…';
  try {
    const data = await api('/api/evaluations/roles');
    state.roles = data.roles;
    state.evaluatorConfigured = data.evaluator_configured;
    state.knownManagers = data.known_managers || [];
    state.account = data.user || null;
    state.isAdmin = data.auth_enabled === false || Boolean(data.is_admin);
    state.scoresVisible = data.scores_visible !== false;
    applyAccountView();
    if (data.candidate_emails) state.mail = data.candidate_emails;
    if (data.shortlist_size) {
      state.shortlistSize = data.shortlist_size;
      // Only seed the box while it still holds the default -- a recruiter who
      // typed 30 and then hit Refresh should still be sending 30.
      const box = $('shortlistLimit');
      if (!box.dataset.touched) box.value = data.shortlist_size;
      box.max = data.shortlist_max || 100;
    }
    state.shortlistMax = data.shortlist_max || 100;
    renderKnownManagers();
    // The board's own totals ride along with the roles, so the tab counts are
    // right before the first board request lands.
    if (data.pipeline) renderPipelineCounts(data.pipeline);
    renderStats();
    renderRoles();
    const totals = state.roles.reduce((n, r) => n + r.counts.total, 0);
    // A hiring manager is looking at 2 roles out of 26 and has no way to know
    // that from a grid of 2. Say it in the line that is already there rather
    // than adding a banner they would learn to skip.
    const whose = state.isAdmin ? 'roles' : 'of your roles';
    $('runMeta').textContent =
      `${state.roles.length} ${whose} · ${totals.toLocaleString()} submissions in MongoDB` +
      (state.evaluatorConfigured ? '' : ' · AI evaluation not configured');

    renderRejectedRoleOptions();
    renderPipelineRoleOptions();

    // Restore the role -- and the section of it -- named in the URL.
    const params = new URLSearchParams(location.hash.slice(1));
    const linked = Number(params.get('role'));
    if (linked && state.activeRoleId === null) {
      const tab = allowedTab(params.get('tab'));
      openRole(linked, false, tab, params.get('tier') || null);
    } else if (state.activeRoleId !== null) {
      // Refresh while a role is open: re-tally its header, and put the two
      // cross-role selects back on this role -- rebuilding their options just
      // dropped whichever one was chosen.
      const open = activeCard();
      renderRoleStats(open);
      renderHeroCal(open);
      scopeRoleSelect('pipelineRole', state.activeRoleId, open);
      scopeRoleSelect('rejectedRole', state.activeRoleId, open);
    }
  } catch (err) {
    $('runMeta').textContent = 'Could not load roles.';
    toast(err.message, true);
  }
}


/* --- what this account is shown ---------------------------------------
 *
 * NONE OF THIS IS THE ACCESS RULE. The server decides what an account may
 * read and refuses the rest; every function here only stops a hiring manager
 * being offered a button that would answer 403. Add the server check first,
 * then come here — a control hidden with nothing behind it is not hidden.
 */

function applyAccountView() {
  const admin = state.isAdmin;

  // Portal sync is a full re-crawl on behalf of the whole company, and the
  // reminders dashboard lists candidates across every role. Neither belongs
  // to one seat.
  setHidden('syncBtn', !admin);
  for (const link of document.querySelectorAll('a[href="/"]')) {
    link.hidden = !admin;
  }

  // Adding somebody to a role is adding somebody to this dashboard. Managers
  // read the list; they do not write it.
  setHidden('mgrForm', !admin);
  setHidden('accountsBtn', !admin);

  // The whole Shortlist section, not just its editor. What is left of it for a
  // manager is a list they cannot change and a table of the same people the
  // Candidates tab already ranks -- a third tab that answers nothing the other
  // two do not. Their booking link, the one control on it that was theirs,
  // moves to the role header; see renderHeroCal().
  setHidden('tabShortlist', !admin);

  // Everyone opens on everyone. The twenty a manager was sent are a section of
  // their own now -- the top of the Pipeline tab, above the board they feed --
  // so a cap here would only hide candidates from the one list whose job is to
  // show all of them, and the reader who wants the shortlist has somewhere
  // better to read it. See loadTopCandidates().
  if (state.topN === null) {
    state.topN = 0;
    $('topN').value = String(state.topN);
  }
  // Already sitting on it when the account loaded -- a pasted URL, or a
  // refresh -- so move off it rather than leaving an open tab with no button.
  if (!admin && state.tab === 'shortlist' && state.activeRoleId !== null) {
    setRoleTab('candidates');
  }
  if (admin && !state.users.length) loadUsers();

  // An account nobody has put on a seat yet. Not an error and not an empty
  // filter, so neither of the two messages already on this page is right.
  const stranded = !admin && state.roles.length === 0;
  const empty = $('roleEmpty');
  empty.hidden = !stranded && visibleRoles().length > 0;
  if (stranded) {
    empty.textContent =
      'You are not listed as a hiring manager on any role yet, so there is '
      + 'nothing here to show. Ask the recruiting team to add you to the roles '
      + 'you own — the same list their shortlist emails go to.';
  } else {
    empty.textContent = 'No roles match these filters.';
  }
}

/* --- accounts (admin only) --------------------------------------------- */

async function loadUsers() {
  try {
    const data = await api('/api/auth/users');
    state.users = data.users || [];
    renderUsers();
  } catch (err) {
    // A manager reaching this would be a bug, not a thing to shout about.
    if (!/recruiting-team account/i.test(err.message)) toast(err.message, true);
  }
}

/* --- opening and closing --------------------------------------------------
 *
 * The drawer sits over whatever is on screen and unloads none of it, so
 * closing puts you back on the same role, the same tab and the same scroll
 * position. That is the whole reason this moved out of the roles view: you
 * decide somebody should be on a role while you are looking at the role.
 */

function openAccounts() {
  $('accountsDrawer').hidden = false;
  toggleNewUserForm(false);
  // Opened fresh each time. A filter left over from the last visit hides
  // accounts that are still there, which reads as accounts that are gone.
  $('userSearch').value = '';
  if (state.users.length) renderUsers(); else loadUsers();
  $('userSearch').focus();
}

function closeAccounts() {
  closeRolePicker();
  $('accountsDrawer').hidden = true;
  $('accountsBtn')?.focus();
}

/* Three empty fields above the list of people you came here to edit are three
 * fields in the way, so the create form is folded until asked for. */
function toggleNewUserForm(open) {
  const form = $('newUserForm');
  const show = open === undefined ? form.hidden : Boolean(open);
  form.hidden = !show;
  const btn = $('newUserToggle');
  btn.setAttribute('aria-expanded', String(show));
  btn.textContent = show ? 'Cancel' : 'New account';
  if (show) $('newUserEmail').focus();
}

/* --- the list ------------------------------------------------------------ */

/* The search matches role titles as well as people, because the question asked
 * of this screen is at least as often "who is on Chief of Staff?" as it is
 * "what can Anita see?". Every word has to match something; two words narrow
 * rather than widen. */
function matchesUserSearch(user, needle) {
  if (!needle) return true;
  const hay = [user.email, user.name,
    user.is_admin ? 'recruiting admin' : 'hiring manager']
    .concat((user.roles || []).map((r) => r.title))
    .join(' ').toLowerCase();
  return needle.split(/\s+/).every((word) => hay.includes(word));
}

// plural() is the one further down this file, beside the shortlist counts.

function userInitials(user) {
  const parts = String(user.name || user.email || '').split(/[\s.@_-]+/)
    .filter(Boolean);
  return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function renderUsers() {
  const all = state.users;
  const needle = $('userSearch').value.trim().toLowerCase();
  // Recruiting first, then managers, each A-Z. The admin list is the short one
  // and the one somebody scanning for "who can see everything" wants on top.
  const rows = all.filter((user) => matchesUserSearch(user, needle))
    .slice()
    .sort((a, b) => (Number(b.is_admin) - Number(a.is_admin))
      || a.email.localeCompare(b.email));

  const admins = all.filter((user) => user.is_admin).length;
  $('accountsCount').textContent = all.length
    ? `${plural(all.length, 'account')} · ${admins} recruiting · `
      + `${plural(all.length - admins, 'hiring manager')}`
    : 'Nobody has an account yet.';

  const empty = $('usersEmpty');
  empty.hidden = rows.length > 0;
  // Two different nothings: an empty server, and a search that found nobody.
  empty.textContent = all.length
    ? 'No account matches that search.' : 'No accounts yet.';

  $('usersBody').innerHTML = rows.map(userCard).join('');

  for (const btn of $('usersBody').querySelectorAll('[data-reset]')) {
    btn.addEventListener('click', () => resetUserPassword(btn.dataset.reset));
  }
  for (const btn of $('usersBody').querySelectorAll('[data-role]')) {
    btn.addEventListener('click', () => {
      // The half of the switch that is already on. Clicking it should be the
      // no-op it looks like, not a confirm dialog offering the state you are
      // already in.
      if (btn.classList.contains('is-on')) return;
      setUserRole(btn.dataset.role, btn.dataset.to);
    });
  }
  for (const btn of $('usersBody').querySelectorAll('[data-del]')) {
    btn.addEventListener('click', () => removeUser(btn.dataset.del));
  }
  for (const btn of $('usersBody').querySelectorAll('[data-unassign]')) {
    btn.addEventListener('click', () =>
      unassignRole(btn.dataset.unassign, Number(btn.dataset.job)));
  }
  for (const input of $('usersBody').querySelectorAll('.role-picker-input')) {
    input.addEventListener('focus', () => openRolePicker(input));
    input.addEventListener('input', () => openRolePicker(input));
    input.addEventListener('keydown', (event) => rolePickerKey(event, input));
    input.addEventListener('blur', () => closeRolePicker());
  }

  restorePickerFocus();
}

function userCard(user) {
  const flags = [];
  if (!user.active) flags.push('<span class="flag">disabled</span>');
  if (user.must_change) flags.push('<span class="flag">password not set</span>');
  const meta = [
    esc(user.name) || '<span class="flag">no name</span>',
    `last signed in ${user.last_login_at
      ? esc(shortDate(user.last_login_at)) : 'never'}`,
  ].concat(flags).join(' · ');

  return `
    <article class="user-card">
      <div class="user-card-head">
        <span class="user-avatar" aria-hidden="true">${esc(userInitials(user))}</span>
        <div class="user-id">
          <div class="user-email">${esc(user.email)}</div>
          <div class="user-meta">${meta}</div>
        </div>
        <!-- Access as a two-way switch. The old button named where you would
             end up and the cell beside it named where you were, so telling a
             recruiter from a manager meant reading both. -->
        <div class="access-toggle" role="group"
             aria-label="Access for ${esc(user.email)}">
          <button type="button" class="${user.is_admin ? '' : 'is-on'}"
                  data-role="${esc(user.email)}" data-to="manager"
                  ${user.is_admin ? '' : 'aria-current="true"'}
                  title="Sees only the roles listed on this card">Hiring manager</button>
          <button type="button" class="${user.is_admin ? 'is-on' : ''}"
                  data-role="${esc(user.email)}" data-to="admin"
                  ${user.is_admin ? 'aria-current="true"' : ''}
                  title="Sees every role and every button on these pages">Recruiting</button>
        </div>
        <div class="user-actions">
          <button class="btn btn-ghost" data-reset="${esc(user.email)}"
                  title="Set a new temporary password and sign them out">
            Reset password</button>
          <button class="btn btn-ghost btn-danger" data-del="${esc(user.email)}"
                  title="Take away their sign-in">Remove</button>
        </div>
      </div>
      <div class="user-card-roles">${roleCell(user)}</div>
    </article>`;
}

/* The roles an account is on, and the two controls that change it.
 *
 * Writes the SAME list as the Hiring managers editor on the role itself --
 * `hiring_managers` -- rather than a permission of its own. Two doors onto one
 * list; there is no second answer to who owns a seat. Which also means every
 * change here changes who a shortlist is emailed to, so the copy says so.
 */
function roleCell(user) {
  const on = user.roles || [];
  const onIds = new Set(on.map((r) => r.id));
  const who = esc(user.name || user.email);
  const chips = on.map((r) => `
    <span class="role-chip">${esc(r.title)}<button type="button"
      data-unassign="${esc(user.email)}" data-job="${r.id}"
      title="Take ${who} off ${esc(r.title)}"
      aria-label="Remove from ${esc(r.title)}">&times;</button></span>`).join('');

  // An admin sees every role whatever this says, so for them the list means
  // something different -- it is who gets the shortlist email, not who can
  // look. Saying "Every role" and then offering to add one would read as a
  // contradiction, so the sentence above the chips says which it is.
  const note = user.is_admin
    ? '<p class="dim">Every role, as an admin. What is listed here only '
      + 'decides which shortlists are emailed to them.</p>'
    : on.length ? '' : '<p class="dim">Not on any role yet — there is nothing '
      + 'for them to open until one is added.</p>';

  // Only roles they are not already on. An option that does nothing is an
  // option somebody picks twice wondering why nothing happened.
  const left = state.roles.filter((r) => !onIds.has(r.id)).length;

  return `${note}<div class="role-chips">${chips}</div>
    <div class="role-picker" data-picker="${esc(user.email)}">
      <input type="text" class="role-picker-input" autocomplete="off"
             role="combobox" aria-expanded="false" aria-autocomplete="list"
             aria-label="Add ${esc(user.email)} to a role"
             placeholder="${left
               ? 'Add to a role — type to filter' : 'On every role already'}"
             ${left ? '' : 'disabled'}>
      <div class="role-picker-menu" hidden></div>
    </div>`;
}

/* --- the role picker ------------------------------------------------------
 *
 * A <select> of forty roles is forty roles to scroll past, and the recruiter
 * already knows the title they are looking for. This filters as they type and
 * hands focus back after a pick, so putting somebody on three roles is three
 * words rather than three trips through the same list.
 */

function openRolePicker(input) {
  const picker = input.closest('.role-picker');
  const menu = picker.querySelector('.role-picker-menu');
  const user = state.users.find((u) => u.email === picker.dataset.picker);
  const onIds = new Set(((user && user.roles) || []).map((r) => r.id));
  const needle = input.value.trim().toLowerCase();

  const options = state.roles
    .filter((r) => !onIds.has(r.id))
    .filter((r) => !needle || r.title.toLowerCase().includes(needle));

  menu.innerHTML = options.length
    ? options.map((r, i) => `<button type="button" data-job="${r.id}"
        class="${i === 0 ? 'is-active' : ''}">${esc(r.title)}</button>`).join('')
    : '<p class="role-picker-none">No role matches that.</p>';
  menu.hidden = false;
  input.setAttribute('aria-expanded', 'true');

  // mousedown rather than click: a click fires after the input has blurred,
  // which has already closed the menu out from under the pointer. Preventing
  // the default also keeps the caret where it was.
  for (const btn of menu.querySelectorAll('[data-job]')) {
    btn.addEventListener('mousedown', (event) => {
      event.preventDefault();
      pickRole(picker.dataset.picker, Number(btn.dataset.job));
    });
  }
}

function closeRolePicker() {
  for (const menu of document.querySelectorAll('.role-picker-menu')) {
    menu.hidden = true;
  }
  for (const input of document.querySelectorAll('.role-picker-input')) {
    input.setAttribute('aria-expanded', 'false');
  }
}

function rolePickerKey(event, input) {
  const picker = input.closest('.role-picker');
  const menu = picker.querySelector('.role-picker-menu');

  if (event.key === 'Escape') {
    // Swallowed only while the menu is up, so it shuts the menu and nothing
    // else -- otherwise the page-level handler would close the whole drawer on
    // the same keystroke. With no menu open there is nothing here to close and
    // Escape should mean what it means everywhere else on the page.
    if (menu.hidden) return;
    event.stopPropagation();
    closeRolePicker();
    return;
  }
  if (menu.hidden && (event.key === 'ArrowDown' || event.key === 'Enter')) {
    openRolePicker(input);
  }

  const options = Array.from(menu.querySelectorAll('[data-job]'));
  if (!options.length) return;
  const at = Math.max(0,
    options.findIndex((btn) => btn.classList.contains('is-active')));

  if (event.key === 'Enter') {
    event.preventDefault();
    pickRole(picker.dataset.picker, Number(options[at].dataset.job));
    return;
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
  event.preventDefault();
  const to = (at + (event.key === 'ArrowDown' ? 1 : options.length - 1))
    % options.length;
  options[at].classList.remove('is-active');
  options[to].classList.add('is-active');
  options[to].scrollIntoView({ block: 'nearest' });
}

/* Adding asks nothing: it is visible on the card the moment it lands, and the
 * x beside it is the undo. Dropping still asks -- see unassignRole() -- because
 * it takes a role away from somebody who may be halfway through reviewing it,
 * and takes them off its shortlist mail with it. */
function pickRole(email, jobId) {
  state.focusPicker = email;
  closeRolePicker();
  assignRole(email, jobId);
}

function restorePickerFocus() {
  const email = state.focusPicker;
  state.focusPicker = null;
  if (!email) return;
  for (const picker of $('usersBody').querySelectorAll('.role-picker')) {
    if (picker.dataset.picker !== email) continue;
    const input = picker.querySelector('.role-picker-input');
    if (input && !input.disabled) input.focus();
    return;
  }
}

async function setUserRoles(email, jobIds, verb) {
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}/roles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: jobIds }),
    });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
    // The role cards carry an owner chip and the "nobody to send to" warning,
    // so the grid above this panel is now out of date.
    if (data.stale_roles) loadRoles();
  } catch (err) {
    toast(err.message, true);
  }
}

function currentRoleIds(email) {
  const user = state.users.find((u) => u.email === email);
  return (user ? user.roles || [] : []).map((r) => r.id);
}

function assignRole(email, jobId) {
  setUserRoles(email, currentRoleIds(email).concat([jobId]), 'added');
}

function unassignRole(email, jobId) {
  const role = state.roles.find((r) => r.id === jobId);
  if (!window.confirm(
    `Take ${email} off ${role ? role.title : `role ${jobId}`}?

`
    + 'They lose access to it immediately, and its shortlist will no longer be '
    + 'emailed to them.')) return;
  setUserRoles(email, currentRoleIds(email).filter((id) => id !== jobId),
    'removed');
}

/* A password is on screen once. Rendered into the page rather than a toast,
 * which disappears on its own after four seconds. */
function showSecret(who, password) {
  if (!password) return;
  $('secretWho').textContent = who;
  $('secretPassword').textContent = password;
  $('newUserSecret').hidden = false;
  $('newUserSecret').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function createUser(event) {
  event.preventDefault();
  const email = $('newUserEmail').value.trim().toLowerCase();
  if (!email.includes('@')) {
    toast('That is not an email address.', true);
    return;
  }
  const btn = $('newUserBtn');
  btn.disabled = true;
  try {
    const data = await api('/api/auth/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        name: $('newUserName').value.trim(),
        role: $('newUserRole').value,
      }),
    });
    state.users = data.users || state.users;
    renderUsers();
    $('newUserForm').reset();
    toggleNewUserForm(false);
    showSecret(`Password for ${email}`, data.password);
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function resetUserPassword(email) {
  if (!window.confirm(
    `Reset the password for ${email}?\n\n`
    + 'They will be signed out everywhere and asked to set a new one. '
    + 'The temporary password is shown once.')) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}/password`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: '{}' });
    state.users = data.users || state.users;
    renderUsers();
    showSecret(`New password for ${email}`, data.password);
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

async function setUserRole(email, role) {
  const what = role === 'admin'
    ? `Give ${email} a recruiting account?\n\nThey will see every role, every `
      + 'candidate, and the buttons that email them.'
    : `Make ${email} a hiring manager?\n\nThey will see only the roles their `
      + 'address is listed on, and will be signed out now.';
  if (!window.confirm(what)) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

async function removeUser(email) {
  if (!window.confirm(
    `Remove the account for ${email}?\n\n`
    + 'They lose access immediately. This does not take them off any role — '
    + 'the shortlist emails still go to them.')) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}`,
      { method: 'DELETE' });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderStats() {
  const t = { total: 0, scored: 0, pending: 0, rejected: 0, in_progress: 0 };
  for (const role of state.roles) {
    for (const key of Object.keys(t)) t[key] += role.counts[key] || 0;
  }
  // The pipeline totals sit in the same row rather than a second one: the
  // funnel is one story, and a candidate's whole journey — submitted, scored,
  // seen, hired — should be readable left to right without scrolling.
  const p = state.pipeline.counts;
  // Labels are short enough that all eight sit on one line, because read as
  // one line they are the funnel. The long version is the tooltip.
  const cards = [
    ['Submitted', t.scored + t.pending + t.rejected, true, 'Assessments actually handed in'],
    ['Scored', t.scored, false, 'Marked against the grid'],
    ['Pending', t.pending, false, 'Submitted, waiting on AI evaluation'],
    ['Missing artefact', t.rejected, false, 'Rejected for arriving without a video or a resume'],
    ['Not submitted', t.in_progress, false, 'Started but never handed in'],
    ['Interview', p.interview || 0, false, 'Booked in for an interview'],
    ['Hired', p.hired || 0, true, 'Offers accepted'],
    ['Rejected', p.rejected || 0, false, 'Turned down after being seen'],
  ];
  $('stats').innerHTML = cards.map(([label, value, accent, title]) => `
    <div class="stat${accent && value ? ' is-accent' : ''}" title="${esc(title)}">
      <div class="stat-value">${value.toLocaleString()}</div>
      <div class="stat-label">${esc(label)}</div>
    </div>`).join('');
}

/* A card is a role, or one posting's half of one.
 *
 * Two cards can share a job id -- the AI Strategist pair sits one assignment
 * at two seniorities -- so anything that has to find "the card that is open"
 * matches on both. Roles with a single grid carry no tier and both sides of
 * the comparison are null, which is why this works unchanged for the other
 * twenty-odd seats. */
const roleCard = (id, tier = null) => state.roles.find(
  (r) => r.id === id && (r.tier || null) === (tier || null));

const activeCard = () => roleCard(state.activeRoleId, state.activeTier);

// `?tier=` for a GET, or nothing at all. Kept as one function so a route that
// forgets it is a bug in one place rather than a card quietly showing the
// other posting's candidates.
const tierParam = (prefix = '?') =>
  (state.activeTier ? `${prefix}tier=${encodeURIComponent(state.activeTier)}` : '');

function visibleRoles() {
  const term = $('roleSearch').value.trim().toLowerCase();
  const filter = $('roleFilter').value;
  return state.roles.filter((role) => {
    if (filter === 'active' && role.counts.total === 0) return false;
    if (filter === 'published' && !role.published) return false;
    if (term && !`${role.title} ${role.slug}`.toLowerCase().includes(term)) return false;
    return true;
  });
}

function renderRoles() {
  const roles = visibleRoles();
  // applyAccountView() owns this line when a manager owns no roles at all --
  // "no roles match these filters" would be a lie about a filter they never
  // touched.
  const stranded = !state.isAdmin && state.roles.length === 0;
  $('roleEmpty').hidden = !stranded && roles.length > 0;

  $('roleGrid').innerHTML = roles.map((role) => {
    const c = role.counts;
    const total = c.total || 0;
    // Widths as percentages of the role's own total, so every bar fills.
    const seg = (n, cls) => (n > 0
      ? `<span class="${cls}" style="width:${(n / total * 100).toFixed(2)}%"></span>` : '');
    const legend = [
      ['scored', c.scored, 'Scored'],
      ['pending', c.pending, 'Pending'],
      ['rejected', c.rejected, 'Rejected'],
      ['in_progress', c.in_progress, 'Not submitted'],
    ].filter(([, n]) => n > 0)
     .map(([key, n, label]) =>
       `<span><i class="seg-${key}"></i>${n.toLocaleString()} ${esc(label.toLowerCase())}</span>`)
     .join('');

    // Where this role's people got to after the score. Counted outside the bar
    // rather than inside it: an interviewee is still a scored submission, and
    // a bar whose segments stopped summing to the total would read as a bug.
    const stages = role.pipeline || {};
    const chips = [['interview', 'booked'], ['hired', 'hired'], ['rejected', 'rejected']]
      .filter(([key]) => stages[key] > 0)
      .map(([key, label]) =>
        `<span class="stage-chip stage-${key}">${stages[key]} ${label}</span>`)
      .join('');

    // Who owns the seat. Shown on the card rather than only inside the role,
    // because "which of these has nobody to send to" is a question about the
    // whole grid. A role with submissions and no manager says so in as many
    // words -- it can be fully graded and still be a dead end.
    const managers = role.managers || [];
    const owner = managers.length
      ? `<span class="stage-chip">${esc(managers[0].name)}${
          managers.length > 1 ? ` +${managers.length - 1}` : ''}</span>`
      : (total ? '<span class="stage-chip stage-rejected">no manager</span>' : '');
    const sent = role.shortlist_last
      ? `<span class="stage-chip stage-hired">sent ${esc(shortDate(role.shortlist_last.at))}</span>`
      : '';
    const ownerRow = owner + sent;

    // A tiered card names the posting, so the slug line has to say which
    // assignment it belongs to or two cards read as two unrelated seats. The
    // unresolved count sits here rather than inside the role, because it is
    // the caveat on the number above it: this many of these people are on this
    // card by fallback, not because anyone matched them to this posting.
    const isActive = role.id === state.activeRoleId
      && (role.tier || null) === (state.activeTier || null);
    const tierBits = role.tier
      ? ` · <b>${esc(role.tier)} tier</b>${
          role.unresolved ? ` · ${role.unresolved} unresolved` : ''}`
      : '';

    return `
      <button class="role-card${isActive ? ' is-active' : ''}"
              data-role="${role.id}" data-tier="${esc(role.tier || '')}" type="button">
        <div class="role-card-head">
          <div>
            <div class="role-name">${esc(role.title)}</div>
            <div class="role-sub">${esc(role.slug)}${role.published ? '' : ' · unpublished'}${
              role.rubric_source === 'pack' ? ' · <b>pack grid</b>'
              : role.rubric_source === 'derived' ? ' · derived grid'
              : ' · <i>no grid</i>'}${tierBits}</div>
          </div>
          <div class="role-total${total ? '' : ' is-zero'}">${total.toLocaleString()}</div>
        </div>
        ${total ? `<div class="role-bar">
          ${seg(c.scored, 'seg-scored')}${seg(c.pending, 'seg-pending')}
          ${seg(c.rejected, 'seg-rejected')}${seg(c.in_progress, 'seg-in_progress')}
        </div>
        <div class="role-legend">${legend}</div>`
        : '<div class="role-legend"><span>No submissions yet</span></div>'}
        ${chips ? `<div class="role-stages">${chips}</div>` : ''}
        ${ownerRow ? `<div class="role-stages">${ownerRow}</div>` : ''}
      </button>`;
  }).join('');

  for (const card of $('roleGrid').querySelectorAll('.role-card')) {
    card.addEventListener('click', () => openRole(
      Number(card.dataset.role), true, null, card.dataset.tier || null));
  }
}

/* --- rejected candidates, in two columns -------------------------------
 *
 * Left: who the assessment rejected and who has not heard about it.
 * Right: who has already had their rejection email.
 *
 * WHY IT IS TWO LISTS. They are the same list exactly once -- the first time.
 * After that they diverge, and a single list becomes actively dangerous:
 * twenty new people land beside two hundred who were mailed last month, "select
 * all" takes all two hundred and twenty, and two hundred people get a second
 * rejection out of a list that looked correct. There is no undo for that.
 *
 * Both columns come out of ONE request. /api/evaluations/rejected marks each
 * row `already_told` from the rejection ledger, and the split here is on that
 * flag -- so the two columns cannot disagree about where somebody is.
 *
 * Moving a candidate across sends nothing and un-sends nothing. Rightwards it
 * records "I have already written to this person" so the left list stops
 * offering them; leftwards it takes that back. The mail itself is still sent
 * from wherever you send it.
 */

// Ticked rows, per column. Emails rather than row indexes, so a tick survives
// a re-render and a change of role filter.
const waitingPicked = new Set();
const mailedPicked = new Set();

function renderRejectedRoleOptions() {
  const select = $('rejectedRole');
  const current = select.value;
  // Only roles that actually have rejections -- an empty option is a dead end.
  const withRejections = state.roles.filter((r) => r.counts.rejected > 0);
  select.innerHTML = '<option value="">All roles</option>' + withRejections
    .map((r) => `<option value="${r.id}">${esc(r.title)} (${r.counts.rejected})</option>`)
    .join('');
  select.value = current;
}

async function loadRejected() {
  const jobId = $('rejectedRole').value;
  $('rejectedCount').textContent = 'Loading…';
  try {
    const data = await api('/api/evaluations/rejected' + (jobId ? `?job_id=${jobId}` : ''));
    state.rejected = data.candidates;
    state.rejectedReasons = data.reasons;
    // A tick on somebody who is no longer in the column that held them is a
    // tick nobody meant. Dropped rather than carried across the move.
    const here = new Set(state.rejected.map((c) => c.candidate_email));
    for (const set of [waitingPicked, mailedPicked]) {
      for (const email of [...set]) if (!here.has(email)) set.delete(email);
    }
    renderRejected();
  } catch (err) {
    $('rejectedCount').textContent = 'Could not load rejected candidates.';
    toast(err.message, true);
  }
}

const waitingRows = () => (state.rejected || []).filter((c) => !c.already_told);
const mailedRows = () => (state.rejected || []).filter((c) => c.already_told);

/* The left column's ticked rows -- what Copy, CSV and "Mark as emailed" act on. */
const selectedRejected = () =>
  waitingRows().filter((c) => waitingPicked.has(c.candidate_email));

/* The counters, the buttons and the two tick-alls -- everything that reacts to
 * a tick, and nothing that requires redrawing a row.
 *
 * Split out of renderRejected() because a tick used to rebuild both tables.
 * With nine hundred rows on screen that is a visible stall on every click, and
 * it replaces the very checkbox being clicked -- so a run of quick ticks lands
 * on elements that have already been thrown away, and only the first one
 * registers. */
function refreshRejectCounts() {
  const waiting = waitingRows();
  const mailed = mailedRows();
  const chosen = selectedRejected();
  const picked = mailed.filter((c) => mailedPicked.has(c.candidate_email));

  $('rejectedCount').textContent = waiting.length
    ? `${chosen.length} of ${waiting.length} selected`
    : 'Nobody is waiting.';
  $('mailedStatus').textContent = mailed.length
    ? (picked.length ? `${picked.length} of ${mailed.length} selected`
      : `${mailed.length.toLocaleString()} moved across`)
    : '—';

  for (const id of ['copyEmailsBtn', 'copyBccBtn', 'exportCsvBtn', 'markMailedBtn']) {
    $(id).disabled = chosen.length === 0;
  }
  $('unmarkBtn').disabled = picked.length === 0;
  $('mailedCsvBtn').disabled = mailed.length === 0;

  $('waitingAll').checked = waiting.length > 0 && chosen.length === waiting.length;
  $('mailedAll').checked = mailed.length > 0 && picked.length === mailed.length;
}

function renderRejected() {
  const waiting = waitingRows();
  const mailed = mailedRows();

  // Phrased to read as a sentence, unlike the row labels ("No video submitted").
  const REASON_PHRASE = {
    missing_video: 'missing a video',
    missing_resume: 'missing a resume',
    missing_video_and_resume: 'missing both a video and a resume',
    manual_override: 'rejected by hand',
  };
  const reasons = Object.entries(state.rejectedReasons || {})
    .map(([reason, n]) => `${n} ${REASON_PHRASE[reason] || reason}`)
    .join(', ');

  $('waitingCount').textContent = waiting.length
    ? `— ${waiting.length.toLocaleString()}` : '';
  $('mailedCount').textContent = mailed.length
    ? `— ${mailed.length.toLocaleString()}` : '';

  $('rejectedHint').textContent = waiting.length
    ? `${reasons}. Addresses are de-duplicated, so a candidate who sat more `
      + `than one assessment appears once. Copy — paste into BCC, never To — `
      + `then move them across.`
    : 'Everyone rejected here has already had their email.';

  $('rejectedEmpty').hidden = waiting.length > 0;
  $('rejectedBody').innerHTML = waiting.map((c) => `
      <tr class="${waitingPicked.has(c.candidate_email) ? 'is-picked' : ''}">
        <td class="shrink"><input type="checkbox" data-email="${esc(c.candidate_email)}"
              ${waitingPicked.has(c.candidate_email) ? 'checked' : ''}></td>
        <td>
          <div class="cand-name">${esc(c.candidate_name || '—')}</div>
          <div class="cand-role">${esc(c.job_title || '')}</div>
        </td>
        <td class="email-cell">${esc(c.candidate_email)}</td>
        <td class="dim">${esc(REASON_LABEL[c.decision?.reason] || c.decision?.reason || '—')}</td>
      </tr>`).join('');

  $('mailedEmpty').hidden = mailed.length > 0;
  $('mailedBody').innerHTML = mailed.map((c) => `
      <tr class="${mailedPicked.has(c.candidate_email) ? 'is-picked' : ''}">
        <td class="shrink"><input type="checkbox" data-email="${esc(c.candidate_email)}"
              ${mailedPicked.has(c.candidate_email) ? 'checked' : ''}></td>
        <td>
          <div class="cand-name">${esc(c.candidate_name || '—')}</div>
          <div class="cand-role">${esc(c.job_title || '')}</div>
        </td>
        <td class="email-cell">${esc(c.candidate_email)}</td>
        <td class="nowrap dim">${shortDate(c.told_at)}</td>
      </tr>`).join('');

  wireRejectTicks('rejectedBody', waitingPicked);
  wireRejectTicks('mailedBody', mailedPicked);
  refreshRejectCounts();
}

/* One delegated listener per table, attached once to the tbody rather than one
 * per row. Nine hundred listeners is nine hundred closures held alive for a
 * list that is redrawn every time the role filter moves. */
function wireRejectTicks(bodyId, set) {
  const body = $(bodyId);
  if (body.dataset.wired) return;
  body.dataset.wired = '1';
  body.addEventListener('change', (event) => {
    const box = event.target;
    if (!box.matches('input[type=checkbox]')) return;
    if (box.checked) set.add(box.dataset.email);
    else set.delete(box.dataset.email);
    // The row it is in, not a redraw of the table it is in.
    box.closest('tr')?.classList.toggle('is-picked', box.checked);
    refreshRejectCounts();
  });
}

/* Tick or untick a whole column, without rebuilding it. */
function tickAll(bodyId, set, rows, on) {
  set.clear();
  if (on) for (const c of rows) set.add(c.candidate_email);
  for (const box of $(bodyId).querySelectorAll('input[type=checkbox]')) {
    box.checked = on;
    box.closest('tr')?.classList.toggle('is-picked', on);
  }
  refreshRejectCounts();
}

/* --- moving people across ---------------------------------------------- */

/* Rightwards: "I have already emailed these people."
 *
 * Writes them to the rejection ledger, which every rejection path reads before
 * sending -- so this also stops the pipeline board offering them a second
 * turn-down months later, not just this list. */
async function markAsEmailed() {
  const chosen = selectedRejected();
  if (!chosen.length) return;
  if (!confirm(
    `Move ${chosen.length} candidate(s) to "Already got the email"?\n\n`
    + 'No email is sent. This records that you have already written to them, '
    + 'so they stop appearing on the left.')) return;

  const btn = $('markMailedBtn');
  btn.disabled = true;
  try {
    const jobId = Number($('rejectedRole').value) || null;
    const result = await postJson('/api/rejections/import', {
      recipients: chosen.map((c) => ({ email: c.candidate_email,
                                       name: c.candidate_name || '' })),
      job_id: jobId,
      note: 'Emailed by hand, marked on the rejection list.',
    });
    toast(result.message);
    waitingPicked.clear();
    await loadRejected();
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
  }
}

/* Leftwards: the undo. It un-sends nothing -- it makes this system stop
 * believing these people were told, which puts them back in the left column. */
async function moveBackToWaiting() {
  const picked = mailedRows().filter((c) => mailedPicked.has(c.candidate_email));
  if (!picked.length) return;
  if (!confirm(
    `Move ${picked.length} candidate(s) back to "Still to tell"?\n\n`
    + 'This does not un-send anything. It means this system stops treating '
    + 'them as already emailed, so they appear on the left again.')) return;

  const btn = $('unmarkBtn');
  btn.disabled = true;
  try {
    const result = await postJson('/api/rejections/remove',
                                  { emails: picked.map((c) => c.candidate_email) });
    toast(result.message);
    mailedPicked.clear();
    await loadRejected();
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
  }
}

async function copyToClipboard(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(label);
  } catch {
    // Clipboard access needs a secure context; over plain http on anything
    // other than localhost it throws. Fall back to a manual copy.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-1000px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    toast(ok ? label : 'Could not copy — select the addresses manually.', !ok);
  }
}

function copyEmails(separator, label) {
  const chosen = selectedRejected();
  if (!chosen.length) return;
  copyToClipboard(chosen.map((c) => c.candidate_email).join(separator),
                  `${label} (${chosen.length} addresses)`);
}

/* Rows (header first) to a downloaded file. Shared by the reject list and the
 * pipeline board, which want the same file with different columns. */
function saveCsv(rows, filename) {
  // Quote every field: names contain commas, and a bare value beginning with
  // = or + is executed as a formula by Excel and Sheets.
  const cell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const body = rows.map((row) => row.map(cell).join(',')).join('\r\n');

  // ﻿ so Excel reads the file as UTF-8 and does not mangle accented names.
  const blob = new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const slugify = (s) => String(s || '').toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

const rejectedRoleSlug = () =>
  slugify($('rejectedRole').selectedOptions[0]?.textContent || 'all-roles');

function exportCsv() {
  const chosen = selectedRejected();
  if (!chosen.length) return;

  const rows = [['Name', 'Email', 'Role', 'Reason', 'Submitted']]
    .concat(chosen.map((c) => [
      c.candidate_name || '',
      c.candidate_email,
      c.job_title || '',
      REASON_LABEL[c.decision?.reason] || c.decision?.reason || '',
      c.submitted_at || '',
    ]));

  saveCsv(rows, `still-to-tell-${rejectedRoleSlug()}.csv`);
  toast(`Downloaded ${chosen.length} candidate(s).`);
}

function exportMailedCsv() {
  const mailed = mailedRows();
  if (!mailed.length) return;
  saveCsv(
    [['Name', 'Email', 'Role', 'Emailed']].concat(mailed.map((c) => [
      c.candidate_name || '', c.candidate_email, c.job_title || '',
      c.told_at || '',
    ])),
    `already-emailed-${rejectedRoleSlug()}.csv`);
  toast(`Downloaded ${mailed.length} candidate(s).`);
}

/* --- hiring pipeline ---------------------------------------------------
 *
 * The three things that happen to a candidate once the assessment has said its
 * piece: they get an interview, and then they are hired or they are not.
 *
 * It is a separate board from the status column above because it answers a
 * different question. `decision.status` says what the assessment concluded --
 * scored, pending, auto-rejected for a missing artefact. A stage here says what
 * a person then did about it. Someone rejected after an interview keeps the
 * score that earned them the interview, and stays out of the artefact-rejection
 * mail merge, which is a different email to a different candidate.
 */

const STAGE_LABEL = {
  interview: 'Interview scheduled',
  hired: 'Hired',
  rejected: 'Rejected after review',
};

const STAGE_CLASS = {
  interview: 'badge-stage-interview',
  hired: 'badge-stage-hired',
  rejected: 'badge-stage-rejected',
};

const stageOf = (c) => {
  const stage = c?.pipeline?.stage;
  return STAGE_LABEL[stage] ? stage : null;
};

/* An interview time is a wall-clock string from a datetime-local input
 * ("2026-08-20T14:30"), stored verbatim: it means the time the interviewer
 * typed, in their own day, and reinterpreting it against a timezone we guessed
 * would move real meetings. So it is formatted by hand rather than through
 * Date, which would apply one. */
function fmtWhen(value) {
  if (!value) return '';
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(value);
  const [, y, mo, d, hh, mm] = m;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = `${Number(d)} ${MONTHS[Number(mo) - 1]}`;
  const year = Number(y) === new Date().getFullYear() ? '' : ` ${y}`;
  return hh ? `${day}${year}, ${hh}:${mm}` : `${day}${year}`;
}

/* "in 2 days", "today", "3 days ago" -- an interview list is read for what is
 * imminent, and a date on its own makes you count. */
function whenRelative(value) {
  if (!value) return '';
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return '';
  const today = new Date();
  const days = Math.round(
    (new Date(when.getFullYear(), when.getMonth(), when.getDate()) -
     new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days === -1) return 'yesterday';
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

function renderPipelineRoleOptions() {
  const select = $('pipelineRole');
  const current = select.value;
  const withStages = state.roles.filter((r) => r.pipeline
    && (r.pipeline.interview + r.pipeline.hired + r.pipeline.rejected) > 0);
  select.innerHTML = '<option value="">All roles</option>' + withStages
    .map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join('');
  select.value = current;
}

function renderPipelineCounts(counts) {
  state.pipeline.counts = counts;
  for (const el of document.querySelectorAll('[data-count]')) {
    el.textContent = (counts[el.dataset.count] || 0).toLocaleString();
  }
}

async function loadPipeline() {
  const { stage } = state.pipeline;
  const jobId = $('pipelineRole').value;
  $('pipelineStatus').textContent = 'Loading…';
  try {
    const data = await api(`/api/pipeline?stage=${stage}` + (jobId ? `&job_id=${jobId}` : ''));
    state.pipeline.rows = data.candidates;
    renderPipelineCounts(data.counts.stages);
    renderPipeline();
  } catch (err) {
    $('pipelineStatus').textContent = 'Could not load the board.';
    toast(err.message, true);
  }
}

const PIPELINE_HEAD = {
  interview: ['Candidate', 'Role', 'Score', 'Interview', 'Interviewer', 'Note', ''],
  hired: ['Candidate', 'Role', 'Score', 'Hired', 'Interviewed', 'Note', ''],
  rejected: ['Candidate', 'Role', 'Score', 'Rejected', 'Interviewed', 'Reason', ''],
};

const PIPELINE_HINT = {
  interview: 'Everyone a hiring manager has invited, soonest first. A row with '
    + 'no date is one the candidate has not booked into yet. Read-only as to '
    + 'who is here — mark the outcome from this row or from their card.',
  hired: 'Offers accepted. The score and the grid that produced it stay on the '
    + 'record, so a hire can be read back against what the assessment predicted.',
  rejected: 'Turned down after being seen — kept apart from the '
    + 'missing-artefact list below, which is a different email to a different '
    + 'candidate.',
};

const PIPELINE_EMPTY = {
  interview: 'Nobody has been invited yet. Send a role\u2019s shortlist to its '
    + 'hiring manager from the Shortlist tab, and whoever they invite from '
    + 'their review link appears here.',
  hired: 'No hires recorded yet.',
  rejected: 'Nobody has been rejected after review.',
};

/* The move buttons on a row. What is worth offering depends on where the
 * candidate is: an interview needs an outcome, a closed stage needs a way back
 * if the outcome was recorded on the wrong person.
 *
 * "Back to interview" is gone with the rest of the interview stage. Undoing a
 * mis-recorded outcome is Remove, which puts them back on the shortlist where
 * the manager can invite them again -- and the manager finds out, which a
 * silent re-book on this page would not tell them. */
function stageActions(c) {
  const stage = stageOf(c);
  const btn = (to, label, cls = 'btn-ghost') =>
    `<button class="btn ${cls} btn-sm" data-move="${to}" data-id="${c.id}">${label}</button>`;
  if (stage === 'interview') {
    return btn('hired', 'Hired', 'btn-primary') + btn('rejected', 'Reject');
  }
  return btn('', 'Remove');
}

/* A board click that would reach a candidate's inbox asks first.
 *
 * While sending is manual it cannot: a move records where somebody is and
 * nothing leaves the building, so the row buttons are safe by construction and
 * a confirm on each of them would only teach people to click through dialogs.
 * The question comes back the moment the server says moves send again -- the
 * one irreversible thing a row button could do is tell someone they did not
 * get the job, and that is worth a sentence first. */
function boardMoveDetail(row, stage) {
  const who = row?.candidate_name || 'this candidate';
  if (stage === 'rejected' && state.mail.auto) {
    const ok = window.confirm(
      `Reject ${who} and email them the rejection now?\n\n`
      + 'Cancel to open their card instead, where you can add a message, '
      + 'preview the email, or record the rejection without sending one.');
    return ok ? {} : null;
  }
  // Moving someone back out of a closed stage is a correction of ours. The
  // candidate is not told, because there is nothing to tell them yet.
  if (stage === null) return { notify: false };
  return {};
}

function renderPipeline() {
  const { stage, rows } = state.pipeline;
  const head = PIPELINE_HEAD[stage];

  $('pipelineHead').innerHTML = head
    .map((h) => `<th${h === 'Score' ? ' class="num"' : ''}>${esc(h)}</th>`).join('');
  $('pipelineHint').textContent = PIPELINE_HINT[stage];
  $('pipelineEmpty').textContent = PIPELINE_EMPTY[stage];
  $('pipelineEmpty').hidden = rows.length > 0;

  const roleName = $('pipelineRole').selectedOptions[0]?.textContent || 'all roles';
  $('pipelineStatus').textContent = rows.length
    ? `${rows.length.toLocaleString()} candidate(s) · ${roleName}`
    : `Nothing at this stage · ${roleName}`;
  for (const id of ['pipelineCopyBtn', 'pipelineCsvBtn']) {
    $(id).disabled = rows.length === 0;
  }

  $('pipelineBody').innerHTML = rows.map((c) => {
    const p = c.pipeline || {};
    const ev = c.evaluation;
    // Column three onwards differs by stage: an interview is read for when it
    // is, a closed stage for when it was decided and what was said.
    const when = stage === 'interview'
      ? (p.interview_at
        ? `${esc(fmtWhen(p.interview_at))} <span class="dim">${
            esc(whenRelative(p.interview_at))}</span>`
        : '<span class="warn">No date set</span>')
      : `${esc(shortDate(p.at))} <span class="dim">${esc(whenRelative(p.at))}</span>`;
    const second = stage === 'interview'
      ? esc(p.interviewer || '—')
      : (p.interview_at ? esc(fmtWhen(p.interview_at)) : '<span class="dim">not interviewed</span>');
    const words = stage === 'rejected' ? (p.reason || p.note) : p.note;
    // Who decided. A hiring manager acting on their own review link is a
    // different fact from a recruiter moving someone on this page, and the
    // board could otherwise say a candidate was hired without ever saying by
    // whom -- the first question asked about any hire that goes wrong.
    const who = p.source === 'manager'
      ? `<span class="by-chip" title="${esc(p.by || '')}">via manager</span>` : '';

    return `
      <tr class="row-click" data-id="${c.id}">
        <td>
          <div class="cand-name">${esc(c.candidate_name || '—')}</div>
          <div class="cand-email">${esc(c.candidate_email || '')}</div>
        </td>
        <td class="dim">${esc(c.job_title || '—')}</td>
        ${scoreCell(ev, c)}
        <td class="nowrap">${when}</td>
        <td class="dim">${second}</td>
        <td><div class="brief-cell">${who}${esc(words || '')}</div></td>
        <td class="nowrap stage-actions">${stageActions(c)}</td>
      </tr>`;
  }).join('');

  for (const tr of $('pipelineBody').querySelectorAll('tr')) {
    tr.addEventListener('click', () => openDrawer(Number(tr.dataset.id)));
  }
  for (const btn of $('pipelineBody').querySelectorAll('[data-move]')) {
    btn.addEventListener('click', (e) => {
      // The row opens the drawer; a button on it must not do both.
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      const stage = btn.dataset.move || null;
      const detail = boardMoveDetail(rows.find((r) => r.id === id), stage);
      if (detail === null) { openDrawer(id); return; }
      moveStage(id, stage, detail);
    });
  }
}

/* Move one candidate. Everything that could be showing them -- the board, the
 * role tallies, the open role's table -- is refreshed from the server rather
 * than patched, so two views can never disagree about where someone is. */
async function moveStage(submissionId, stage, detail = {}) {
  try {
    const result = await api('/api/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId, stage, ...detail }),
    });
    // An email that did not go out is reported as a problem, not folded into
    // the cheerful line about the move: "rejected, not emailed -- no address
    // on record" is a candidate still waiting to hear.
    toast(result.message, Boolean(result.mail && result.mail.reason && !result.mail.sent
                                  && result.mail.reason !== 'Not requested.'));
    renderPipelineCounts(result.counts.stages);
    await loadPipeline();
    if (state.activeRoleId) await openRole(state.activeRoleId, false);
    await loadRoles();
    // Taking somebody back out of a stage puts them among the people awaiting
    // a decision again, which is exactly what the panel above the board lists.
    if (state.top.jobId !== null) await loadTopCandidates(true);
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  }
}

function selectStage(stage) {
  if (!STAGE_LABEL[stage]) return;
  state.pipeline.stage = stage;
  for (const tab of $('pipelineTabs').querySelectorAll('.tab')) {
    const on = tab.dataset.stage === stage;
    tab.classList.toggle('is-active', on);
    tab.setAttribute('aria-selected', String(on));
  }
  loadPipeline();
}

function copyPipelineEmails() {
  const rows = state.pipeline.rows;
  if (!rows.length) return;
  const emails = [...new Set(rows.map((c) => c.candidate_email).filter(Boolean))];
  copyToClipboard(emails.join('; '), `Copied ${emails.length} address(es)`);
}

function exportPipelineCsv() {
  const { stage, rows } = state.pipeline;
  if (!rows.length) return;
  const csv = [['Name', 'Email', 'Role', 'Score', 'Stage', 'Interview',
                'Interviewer', 'Decided', 'Note']]
    .concat(rows.map((c) => [
      c.candidate_name || '',
      c.candidate_email || '',
      c.job_title || '',
      c.evaluation?.score ?? '',
      STAGE_LABEL[stage],
      c.pipeline?.interview_at || '',
      c.pipeline?.interviewer || '',
      c.pipeline?.at || '',
      c.pipeline?.reason || c.pipeline?.note || '',
    ]));
  const role = $('pipelineRole').selectedOptions[0]?.textContent || 'all-roles';
  saveCsv(csv, `${stage}-${slugify(role)}.csv`);
  toast(`Downloaded ${rows.length} candidate(s).`);
}

/* --- the top N, and the invitation the manager writes -------------------
 *
 * The shortlist a hiring manager actually works, sitting one panel above the
 * board it feeds: the people still awaiting a decision, then the people who
 * have had one. It used to be a filter on the Candidates tab and a button that
 * navigated away; both were a step away from where the work continues.
 *
 * THE ONE DOOR IS UNCHANGED. A candidate enters the interview stage from a
 * hiring manager's review workspace and from nowhere else -- /api/pipeline
 * still refuses that move outright, and nothing here asks it to. What this
 * section does is open the caller's OWN workspace for the role they have on
 * screen (/api/managers/review-link, entitled by being named on the role) and
 * drive it from the dashboard, through the same two routes the emailed review
 * page uses. So there is still one composer, one preview builder and one place
 * the interview stage is written -- this is a second way in to it, not a
 * second copy of it.
 *
 * It also settles who may be picked. The rows on screen are the workspace's
 * own list, so a tick can never be a candidate the server would refuse.
 */

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/* Who can still be invited. Somebody hired or turned down cannot: inviting a
 * candidate who has been rejected is the one mis-click here that cannot be
 * walked back. Somebody already at interview can -- that is a reschedule, or a
 * resend to a candidate who says nothing arrived. */
const invitable = (c) => c.stage !== 'hired' && c.stage !== 'rejected';

const reviewApi = (suffix) =>
  `/api/review/${encodeURIComponent(state.top.token || '')}${suffix}`;

/* Whether this account is offered the section at all: named on THIS role's
 * manager list, which is the same test the server makes before it will mint a
 * workspace. Not "is a manager" -- an admin who really does own a seat here
 * passes, and an admin who does not cannot write an invitation in somebody
 * else's name over somebody else's calendar.
 *
 * NOT THE ACCESS RULE. /api/managers/review-link makes its own check and
 * answers 403; this only stops a recruiter being shown a panel that would. */
function topSectionMine() {
  return myManagerEntry(activeCard());
}

function renderTopVisibility() {
  setHidden('topPanel', !topSectionMine());
}

/* Fetch the open role's top N and the workspace that can invite them.
 *
 * Two requests, always in this order: the mint re-points the manager's one
 * live link at today's list (somebody scored since yesterday belongs on it),
 * and the read comes back with exactly what that link permits.
 *
 * `force` re-asks. Without it a return to the tab redraws what is already in
 * hand -- the panel is not a live board, and re-minting a credential on every
 * tab click is a lot of noise in the audit trail for no new names. */
async function loadTopCandidates(force = false) {
  const jobId = state.activeRoleId;
  const top = state.top;
  if (jobId === null || !topSectionMine()) {
    setHidden('topPanel', true);
    return;
  }
  setHidden('topPanel', false);
  if (top.loading) return;
  if (!force && top.jobId === jobId && (top.rows.length || top.empty)) {
    renderTop();
    return;
  }

  top.loading = true;
  top.jobId = jobId;
  $('topCount').textContent = 'Loading…';
  try {
    const mint = await postJson('/api/managers/review-link',
                                { job_id: jobId, limit: top.limit });
    const data = await api(`/api/review/${encodeURIComponent(mint.token)}`);
    // The reader clicked into another role while this was in flight. Their
    // screen is somebody else's list now, and painting this one over it would
    // put the wrong twenty names under the right title.
    if (state.activeRoleId !== jobId) return;

    top.token = mint.token;
    top.rows = data.candidates || [];
    top.canBook = Boolean(data.can_book);
    top.emailsEnabled = data.emails_enabled !== false;
    top.placeholders = data.placeholders || [];
    top.manager = data.manager || null;
    top.empty = '';
    // Ticks survive a refresh, but only for rows that are still there and can
    // still be invited -- a selection carrying somebody who has since been
    // hired is a Send button that would half fail.
    top.picked = new Set([...top.picked].filter((id) =>
      top.rows.some((c) => c.submission_id === id && invitable(c))));
  } catch (err) {
    top.token = null;
    top.rows = [];
    top.picked.clear();
    // "Nobody is waiting yet" is the empty state of this panel, not a failure,
    // so it is printed where the rows would have been rather than thrown at
    // the reader as a red toast.
    if (err.status === 409) {
      top.empty = err.message;
    } else if (err.status === 403) {
      // Taken off the role between the page loading and this click.
      setHidden('topPanel', true);
      return;
    } else {
      top.empty = '';
      toast(err.message, true);
    }
  } finally {
    top.loading = false;
  }
  renderTop();
}

function renderTop() {
  const top = state.top;
  const role = state.roles.find((r) => r.id === top.jobId);
  $('topRole').textContent = role ? `· ${role.title}` : '';

  const term = $('topSearch').value.trim().toLowerCase();
  const rows = top.rows.filter((c) => !term
    || `${c.name || ''} ${c.email || ''}`.toLowerCase().includes(term));

  $('topEmpty').hidden = rows.length > 0;
  $('topEmpty').textContent = top.rows.length
    ? 'Nobody on this list matches that search.'
    : (top.empty || 'Nobody on this role is waiting for a decision yet — '
       + 'candidates appear here once they have been scored.');

  // The verdict is not on the review payload and deliberately so: that surface
  // is the manager's emailed link, which never sees one. On this page the
  // reader is signed in and the Candidates tab is showing them the same rows
  // one tab away, so it is read back out of what this page already holds --
  // and the cell is simply empty until that request lands.
  //
  // The whole verdict rather than its number, so this column draws through
  // scoreCell() like every other: one place decides whether a score is shown
  // at all, and the partial-grading mark rides along instead of a bare figure
  // that does not say it was renormalised from half a rubric.
  const verdictOf = (id) =>
    state.candidates.find((c) => c.id === id)?.evaluation || null;

  const art = (href, label) => (href
    ? `<a class="artefact has" href="${esc(href)}" target="_blank"
          rel="noopener noreferrer" title="Open ${esc(label)}">${esc(label)}</a>`
    : `<span class="artefact missing" title="Not provided">${esc(label)}</span>`);

  $('topBody').innerHTML = rows.map((c) => {
    const picked = top.picked.has(c.submission_id);
    const can = invitable(c);
    const verdict = verdictOf(c.submission_id);
    // "Marked for interview" and "invitation sent" are different facts and are
    // shown as different words. A candidate can sit at interview with an empty
    // inbox -- the send failed, or mail is off -- and a manager who cannot see
    // that waits a week for a booking that was never asked for.
    const where = c.stage
      ? `<span class="badge ${STAGE_CLASS[c.stage] || ''}">${
          esc(STAGE_LABEL[c.stage] || c.stage)}</span>`
        + (c.stage === 'interview'
          ? (c.invited_at
            ? ` <span class="dim">emailed ${esc(shortDate(c.invited_at))}</span>`
            : ' <span class="warn-inline">not emailed yet</span>')
          : '')
      : (c.invited_at
        ? `<span class="dim">invited ${esc(shortDate(c.invited_at))}</span>`
        : '<span class="dim">awaiting a decision</span>');

    return `
      <tr class="top-row row-click${picked ? ' is-picked' : ''}${
          c.invited_at ? ' is-invited' : ''}" data-id="${c.submission_id}">
        <td class="top-pick">
          <input type="checkbox" data-pick="${c.submission_id}"
                 ${picked ? 'checked' : ''} ${can ? '' : 'disabled'}
                 aria-label="Pick ${esc(c.name)} for an interview"
                 title="${can ? `Pick ${esc(c.name)} for an interview`
                              : 'Already decided — nothing left to invite them to'}">
        </td>
        <td class="rank-cell">${c.rank}</td>
        <td>
          <div class="cand-name">${esc(c.name || '—')}</div>
          <div class="cand-email">${esc(c.email || '')}</div>
        </td>
        ${scoreCell(verdict, c)}
        <td><div class="artefacts">${art(c.resume_link, 'CV')}${
          art(c.assessment_url, 'Answers')}${art(c.video_link, 'Video')}</div></td>
        <td class="nowrap dim">${esc(c.submitted_at || '')}</td>
        <td>${where}</td>
      </tr>`;
  }).join('');

  for (const tr of $('topBody').querySelectorAll('tr')) {
    tr.addEventListener('click', (e) => {
      // The row opens the card. The tickbox and the artefact links on it are
      // their own actions and must not do both.
      if (e.target.closest('a, input, label')) return;
      openDrawer(Number(tr.dataset.id));
    });
  }
  for (const box of $('topBody').querySelectorAll('[data-pick]')) {
    box.addEventListener('change', () => {
      const id = Number(box.dataset.pick);
      if (box.checked) top.picked.add(id); else top.picked.delete(id);
      renderTop();
    });
  }

  renderTopBar(rows);
}

/* The send bar. Selections survive filtering rather than being cleared by it:
 * picking three people, then searching for a fourth, is one task, and a page
 * that quietly dropped the first three would be lying about what Send does.
 * So the count names the total, and only mentions the on-screen number when a
 * search is actually hiding some of it. */
function renderTopBar(rows) {
  const top = state.top;
  const pickable = rows.filter(invitable);
  const shown = pickable.filter((c) => top.picked.has(c.submission_id)).length;
  const total = top.picked.size;

  const all = $('topAll');
  all.checked = pickable.length > 0 && shown === pickable.length;
  all.indeterminate = shown > 0 && shown < pickable.length;
  all.disabled = pickable.length === 0;
  $('topAllLabel').textContent = all.checked ? 'Clear selection' : 'Select all';

  $('topCount').textContent = total
    ? `${plural(total, 'candidate')} picked`
      + (shown === total ? '' : ` · ${shown} of them on screen`)
    : (top.rows.length
      ? `${plural(top.rows.length, 'candidate')} waiting · nobody picked yet`
      : '—');

  const btn = $('topInviteBtn');
  btn.disabled = total === 0 || !top.canBook || !top.emailsEnabled;
  btn.textContent = total > 1 ? `Invite ${total} to interview` : 'Invite to interview';
  btn.title = !top.emailsEnabled ? 'Candidate emails are switched off.'
    : !top.canBook ? 'We do not have your booking link yet.'
    : total === 0 ? 'Tick the people you want to meet.'
    : 'Write their invitation and read it before it goes';

  // Said before the first click, not after it fails. A manager with no booking
  // link cannot invite anybody, and the fix is the button in this role's own
  // header -- so the sentence names it rather than telling them to email us.
  const warn = $('topWarn');
  if (!top.emailsEnabled) {
    warn.textContent = 'Candidate emails are switched off right now, so nobody '
      + 'can be invited from here. Decisions on the board below still record.';
    warn.hidden = false;
  } else if (!top.canBook) {
    warn.textContent = 'We do not have your booking link yet, so an invitation '
      + 'would reach the candidate with no way to pick a time. Add it from '
      + '“Add your booking link” at the top of this role and the '
      + 'button below comes alive.';
    warn.hidden = false;
  } else {
    warn.hidden = true;
  }
}

/* --- rubric ------------------------------------------------------------
 *
 * The marking standard is not shown on this page. It stays where it belongs --
 * `rubric_pack.py`, the derived grid files and the grader that reads them --
 * and the dashboard states only what came out of it: a score, a band, and the
 * per-criterion marks a candidate actually earned.
 *
 * The role's grid is still fetched, quietly, because two things on the page
 * are read off it and would otherwise have to guess: the criterion columns in
 * the candidate table take their labels from it, and the drawer's band ranges
 * ("Better 75-84") come from the pack's own cuts rather than a copy of them
 * kept here. Neither renders the standard itself.
 *
 * `/api/evaluations/rubric` and the derive endpoint behind it are untouched --
 * see the README. Deriving a grid for a role the pack does not cover is a
 * command-line job now rather than a button.
 */

async function loadRubric(jobId) {
  state.rubric = null;
  try {
    const rubric = await api(`/api/evaluations/rubric/${jobId}${tierParam()}`);
    // Someone can open a role, go back, and open another before this lands.
    if (state.activeRoleId !== jobId) return;
    state.rubric = rubric;
  } catch {
    // Nothing on screen depends on this arriving: the criterion columns stay
    // empty and the bands fall back to FALLBACK_BANDS, which is what they
    // already do for the moment before the response lands. Failing loudly for
    // a panel that no longer exists would be noise.
    return;
  }
  // The criterion columns take their labels from this response, and it lands
  // after the candidate table has already drawn. Redraw so switching roles
  // with the toggle on does not silently lose the columns.
  if (state.showMatrix && state.candidates.length) renderCandidates();
}

/* --- candidates ------------------------------------------------------- */

/* --- Hiring managers and the shortlist hand-off ------------------------
 *
 * The end of the funnel. A role's managers are edited here and stored on the
 * role in Mongo; the top-N is built server-side by the same function that
 * renders the email and the spreadsheet, so the table below is not a mock-up
 * of the send -- it is the send.
 *
 * NO SCORES ANYWHERE IN THIS SECTION. The manager sees rank, name, contact and
 * links; the number that produced the rank stays inside recruiting. See the
 * header of shortlist.py.
 */

function renderKnownManagers() {
  // Everyone already on a role, offered as suggestions. One person owning
  // three seats should be typed once; a name spelt two ways is how a
  // directory rots.
  $('mgrKnown').innerHTML = state.knownManagers
    .map((m) => `<option value="${esc(m.email)}">${esc(m.name)}</option>`).join('');
}

async function loadShortlist(jobId) {
  $('shortlistRole').textContent = '';
  $('shortlistCount').textContent = 'Loading…';
  $('shortlistBodyRows').innerHTML = '';
  $('mgrSave').hidden = true;

  try {
    // The tick is not sent on the very first load of a session: the server's
    // own default is the answer then, and it comes back in `show_scores`.
    const data = await api(
      `/api/shortlist/${jobId}?limit=${shortlistLimit()}${tierParam('&')}`
      + ($('shortlistScores').dataset.touched ? shortlistScoresParam() : ''));
    // A slow response for a role the user has already clicked away from must
    // not overwrite the one they are looking at now.
    if (state.activeRoleId !== jobId) return;
    state.shortlist.managers = data.managers || [];
    state.shortlist.rows = data.candidates || [];
    state.shortlist.heldBack = data.held_back || [];
    state.shortlist.lastSend = data.last_send || null;
    state.shortlist.showScores = !!data.show_scores;
    // Left alone once a recruiter has touched it, exactly like the size box:
    // switching roles should not silently undo the choice they just made.
    if (!$('shortlistScores').dataset.touched) {
      $('shortlistScores').checked = state.shortlist.showScores;
    }
    state.shortlist.dirty = false;
    $('shortlistRole').textContent = data.role.title || '';
    renderManagers();
    renderShortlist();
    loadReviewLinks(jobId);
  } catch (err) {
    $('shortlistCount').textContent = 'Could not load the shortlist.';
    toast(err.message, true);
  }
}

function renderManagers() {
  const list = state.shortlist.managers;
  const admin = state.isAdmin;
  // A hiring manager reads this list -- it is who else is on the seat -- but
  // cannot edit it, because editing it is editing who can open this role.
  // Their own booking link is the exception: it is theirs, it is what a
  // candidate is sent, and /api/managers/cal-link is scoped to their address.
  const mine = (m) => Boolean(state.account) && m.email === state.account.email;
  $('mgrList').innerHTML = list.length
    ? list.map((m, i) => `
        <li class="mgr-chip">
          <span>
            <span class="mgr-chip-name">${esc(m.name)}</span>
            ${m.title ? ` <span class="mgr-chip-title">${esc(m.title)}</span>` : ''}
            <span class="mgr-chip-email">${esc(m.email)}</span>
            ${m.cal_link
              ? `<a class="mgr-chip-cal" href="${esc(m.cal_link)}" target="_blank"
                    rel="noopener" title="${esc(m.cal_link)}"
                    onclick="event.stopPropagation()">booking link</a>`
              : (admin || mine(m))
                ? `<button class="mgr-chip-cal is-missing" type="button" data-cal="${i}"
                    title="Candidates cannot be invited to an interview by this manager until it is set">add booking link</button>`
                : '<span class="mgr-chip-cal is-missing">no booking link</span>'}
          </span>
          ${admin
            ? `<button class="mgr-remove" type="button" data-remove="${i}"
                  title="Remove ${esc(m.name)}" aria-label="Remove ${esc(m.name)}">&times;</button>`
            : ''}
        </li>`).join('')
    : '<li class="mgr-empty">Nobody assigned yet — this role’s shortlist has nowhere to go.</li>';

  for (const btn of $('mgrList').querySelectorAll('[data-remove]')) {
    btn.addEventListener('click', () => removeManager(Number(btn.dataset.remove)));
  }
  for (const btn of $('mgrList').querySelectorAll('[data-cal]')) {
    btn.addEventListener('click', () => promptCalLink(Number(btn.dataset.cal)));
  }
}

/* A manager filling in their own link, from the row that says it is missing.
 * Kept as an edit to the local list rather than its own request: it lands with
 * Save alongside every other change, which is the rule the rest of this editor
 * already follows. */
function promptCalLink(index) {
  const manager = state.shortlist.managers[index];
  if (!manager) return;
  const link = window.prompt(
    `Booking link for ${manager.name} — their cal.com page.\n`
    + 'Candidates moved to Interview by them are sent this link.',
    manager.cal_link || 'cal.com/');
  if (link === null) return;
  manager.cal_link = link.trim();
  renderManagers();
  if (state.isAdmin) {
    markDirty();
    return;
  }
  // A hiring manager has no Save button here -- the whole-list POST behind it
  // is admin-only. Their own link goes straight to the endpoint that is scoped
  // to their own address, and lands on every seat they own at once.
  saveOwnCalLink(manager);
}

async function saveOwnCalLink(manager) {
  try {
    const data = await api('/api/managers/cal-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: manager.email, cal_link: manager.cal_link }),
    });
    manager.cal_link = data.cal_link;
    renderManagers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
    loadShortlist(state.activeRoleId);   // put the list back as it is stored
  }
}


/* --- a manager's booking link, in the role header ----------------------
 *
 * The Shortlist tab is not drawn for a manager any more, and this was the one
 * thing on it they could change. It is not an incidental setting: a candidate
 * moved to Interview is sent this URL, and the invite composer refuses to send
 * without it (review.js answers `needs: 'cal_link'`). So it is a control on
 * the first screen of the role, saying which of the two states it is in,
 * rather than a field somebody has to go looking for after a send has failed.
 *
 * Read off the roles payload, which already carries every role's manager list
 * -- no request of its own, and nothing to load before the header can be drawn.
 */
function myManagerEntry(role) {
  const me = state.account?.email;
  if (!me || !role) return null;
  return (role.managers || []).find((m) => m.email === me) || null;
}

function renderHeroCal(role) {
  // Not through setHidden(): the rest of this function needs the element
  // anyway, and openRole() -- unlike loadRoles() -- has no try around it, so a
  // throw here would stop the role opening at all rather than just leaving a
  // button undrawn. Same stale-HTML case, see setHidden().
  const btn = $('heroCalBtn');
  if (!btn) return;
  const mine = state.isAdmin ? null : myManagerEntry(role);
  // An admin edits everyone's link in the Shortlist tab, and a manager who is
  // somehow not on this role's list has nothing to set.
  btn.hidden = !mine;
  if (!mine) return;

  const has = Boolean(mine.cal_link);
  btn.textContent = has ? 'Your booking link' : 'Add your booking link';
  btn.classList.toggle('is-missing', !has);
  btn.title = has
    ? `${mine.cal_link} — candidates you invite to an interview are sent this.`
    : 'Your cal.com page. Until it is set you cannot send an interview '
      + 'invitation, because there is nothing for the candidate to book.';
}

/* --- the manager's way into an interview invitation --------------------
 *
 * A door, not a composer -- and now a door to the next tab along rather than
 * to another page. The shortlist and the invitation both live at the top of
 * Pipeline, so this button only has to put the reader in front of them. What
 * is behind it is loadTopCandidates(); the reasoning about why there is one
 * composer and not two is in the comment above that section.
 */
function renderInviteBtn(role) {
  const btn = $('inviteBtn');
  if (!btn) return;
  // Same test as the server's: named on THIS role's manager list. Not
  // "is a manager" -- an admin who really does own a seat here passes, and an
  // admin who does not cannot mint an invite in somebody else's name.
  const mine = myManagerEntry(role);
  btn.hidden = !mine;
  // Two filled buttons side by side is two primary actions, which is none.
  // For a manager the invitation is the point of the screen and grading is
  // housekeeping they merely may do, so the emphasis swaps rather than the
  // button disappearing -- a manager on a thin role does still grade.
  $('gradeBtn').classList.toggle('btn-primary', !mine);
  if (!mine) return;

  // Nothing to invite yet is a different sentence from "you cannot invite".
  const waiting = awaitingDecision().length;
  btn.disabled = waiting === 0;
  btn.title = waiting === 0
    ? 'Nobody on this role is waiting for a decision yet.'
    : `Open the top ${Math.min(waiting, state.top.limit)} on the Pipeline tab `
      + 'and send their interview invitations from there.';
}

function openInvite() {
  // The section is the first thing in that pane and setRoleTab() scrolls to
  // the top of it, so switching tab IS the navigation. Its first visit for a
  // role fetches the list.
  setRoleTab('pipeline');
}

async function editHeroCal() {
  const role = activeCard();
  const mine = myManagerEntry(role);
  if (!mine) return;

  const link = window.prompt(
    'Your booking link — your own cal.com page.\n'
    + 'Candidates you move to Interview are sent this.',
    mine.cal_link || 'cal.com/');
  if (link === null) return;

  try {
    const data = await api('/api/managers/cal-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: mine.email, cal_link: link.trim() }),
    });
    // The endpoint writes the link to every role this address is on, so every
    // copy the page is holding is updated with it -- not just the open one.
    // Otherwise the next role they clicked would still read "add your link".
    for (const r of state.roles) {
      const entry = (r.managers || []).find((m) => m.email === mine.email);
      if (entry) entry.cal_link = data.cal_link;
    }
    renderHeroCal(role);
    if (state.loaded.shortlist) renderManagers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

const shortlistLimit = () => {
  const value = Number($('shortlistLimit').value);
  if (!Number.isFinite(value) || value < 1) return state.shortlistSize;
  return Math.min(value, state.shortlistMax);
};

/* Whether this send hands over the score, as a query parameter.
 *
 * Always sent, never omitted: unticking the box on an installation whose
 * SHORTLIST_SHOW_SCORES default is on has to mean "no" rather than "no
 * opinion", and an absent parameter is what the server reads as the latter. */
const shortlistScoresParam = () =>
  `&scores=${$('shortlistScores').checked ? '1' : '0'}`;

function renderShortlist() {
  const rows = state.shortlist.rows;
  const managers = state.shortlist.managers;

  const link = (url, label) => (url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${label}</a>`
    : '<span class="none">—</span>');

  // Shown here only when it is going in the file. This table is the preview
  // of the hand-off, so a score column the manager's copy will not have would
  // make it a preview of something else.
  const scores = state.shortlist.showScores;
  $('shortlistScoreHead').hidden = !scores;

  $('shortlistBodyRows').innerHTML = rows.map((c) => `
    <tr>
      <td class="rank-cell">${c.rank}</td>
      <td>${esc(c.name)}${c.provisional
        ? ' <span class="provisional-mark" title="The AI did not finish this'
          + ' candidate’s rubric. Their rank is not a like-for-like'
          + ' comparison — re-grade them.">partial</span>'
        : ''}</td>
      ${scores ? `<td class="num"><span class="score-cell ${
        scoreClass(c.score)}">${fmtScore(c.score)}</span></td>` : ''}
      <td class="email-cell">${esc(c.email)}</td>
      <td class="link-cell">${link(c.resume_link, 'CV')}</td>
      <td class="link-cell">${link(c.assessment_url, 'Answers')}</td>
      <td class="link-cell">${link(c.video_link, 'Video')}</td>
      <td>${esc(c.submitted_at)}</td>
    </tr>`).join('');

  $('shortlistEmpty').hidden = rows.length > 0;
  $('shortlistEmpty').textContent = state.shortlist.heldBack.length
    ? 'Every scored candidate on this role was only partly graded, so none of '
      + 'them can be ranked. Re-grade them first — see below.'
    : 'No scored candidates yet. Grade this role’s submissions first.';

  /* Held out of the ranking, and said so here rather than only in the log.
   *
   * A partial grid is renormalised to 100, so it does not sort low — it sorts
   * to the TOP, and the manager's page shows rank with no score to read it
   * against. Keeping them out is the only honest option; keeping them out
   * quietly would just move the invisible failure, since the person who can
   * fix it by re-grading is the one reading this screen. */
  const held = state.shortlist.heldBack;
  const heldEl = $('shortlistHeldBack');
  heldEl.hidden = held.length === 0;
  heldEl.innerHTML = held.length
    ? `<b>${held.length} scored candidate${held.length === 1 ? '' : 's'} held
       back.</b> The AI stopped part-way through the rubric, so the total it
       produced was scaled up from the rows it did mark and is not comparable
       with the rest — a single marked row comes out at 100.0. Re-grade
       ${held.length === 1 ? 'them' : 'them'} from the Candidates tab, then
       reload this list. ${held.map((c) => `${esc(c.name)}${
         typeof c.grid_marked === 'number' && typeof c.grid_of === 'number'
           ? ` (${c.grid_marked}/${c.grid_of} criteria)` : ''}`).join(', ')}.`
    : '';

  $('shortlistCount').textContent = rows.length
    ? `${rows.length} candidate${rows.length === 1 ? '' : 's'} · ${
        managers.length ? `to ${managers.map((m) => m.name).join(', ')}`
                        : 'no manager assigned'}`
    : 'Nothing to send yet.';

  // What is being handed over, said plainly before it is handed over. The
  // second sentence is the whole difference the tick makes, so it changes with
  // the tick rather than describing both states at once.
  $('shortlistHint').textContent =
    'Ranked by assessment score, strongest first. The email and the spreadsheet '
    + 'carry the rank, the candidate’s contact details and links to their CV, '
    + 'answers and video. '
    + (scores
      ? 'The spreadsheet also carries the AI score out of 100 — the email body '
        + 'does not, and neither does the manager’s review page.'
      : 'Neither carries the score itself — tick “AI score” to put it in the '
        + 'spreadsheet.');

  const ready = rows.length > 0 && managers.length > 0 && !state.shortlist.dirty;
  const send = $('shortlistSendBtn');
  send.disabled = !ready;
  send.title = state.shortlist.dirty
    ? 'Save the manager list first.'
    : !managers.length ? 'Assign a hiring manager first.'
    : !rows.length ? 'No scored candidates to send.'
    : `Email the top ${rows.length} to ${managers.length} manager(s)`;

  $('shortlistXlsxBtn').disabled = rows.length === 0;
  $('shortlistPreviewBtn').disabled = rows.length === 0;

  const last = state.shortlist.lastSend;
  $('shortlistSent').textContent = last
    ? `Last sent ${shortDate(last.at)} · ${last.count} to ${(last.to || []).join(', ')}`
    : '';
}

/* --- editing the manager list ---------------------------------------- */

// Edits are local until Save, so adding three managers is one round trip.
// Everything that sends is disabled while there are unsaved edits: a send that
// used the server's older list would silently ignore what is on screen.
function markDirty() {
  state.shortlist.dirty = true;
  $('mgrSave').hidden = false;
  renderShortlist();
}

function addManager(e) {
  e.preventDefault();
  const email = $('mgrEmail').value.trim().toLowerCase();
  if (!email.includes('@')) { toast('That is not an email address.', true); return; }
  if (state.shortlist.managers.some((m) => m.email === email)) {
    toast(`${email} is already on this role.`, true);
    return;
  }
  // Someone already on another role keeps the name and title they were given
  // there, so the same person is not spelt two ways across the board.
  const known = state.knownManagers.find((m) => m.email === email);
  state.shortlist.managers.push({
    name: $('mgrName').value.trim() || known?.name
          || email.split('@')[0].replace(/\./g, ' '),
    email,
    title: $('mgrTitle').value.trim() || known?.title || '',
    // A manager who already has a calendar on another role keeps it here:
    // one person books every seat they own out of the same cal.com page.
    cal_link: $('mgrCal').value.trim() || known?.cal_link || '',
  });
  $('mgrForm').reset();
  $('mgrEmail').focus();
  renderManagers();
  markDirty();
}

function removeManager(index) {
  state.shortlist.managers.splice(index, 1);
  renderManagers();
  markDirty();
}

async function saveManagers() {
  const jobId = state.activeRoleId;
  if (jobId === null) return;
  const btn = $('mgrSave');
  btn.disabled = true;
  try {
    const data = await api(`/api/roles/${jobId}/managers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ managers: state.shortlist.managers }),
    });
    state.shortlist.managers = data.managers;
    state.shortlist.dirty = false;
    state.knownManagers = data.known_managers || state.knownManagers;
    renderKnownManagers();
    renderManagers();
    renderShortlist();
    btn.hidden = true;
    toast(data.message);
    // The cards carry the owner chip, so the grid above is now stale.
    loadRoles();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* --- review links ------------------------------------------------------
 *
 * The links that were mailed to managers, and the ability to kill one. Not a
 * list of URLs to copy around: a review link is a credential, so the token is
 * never rendered in full and the only action offered on a live one is revoke.
 * Copying it is there for the manager who says the email never arrived, which
 * is the one legitimate reason to handle it by hand.
 */

const LINK_STATE_LABEL = {
  ok: 'live', revoked: 'revoked', expired: 'expired', unknown: 'unknown',
};

async function loadReviewLinks(jobId) {
  try {
    const data = await api(`/api/shortlist/${jobId}/links`);
    if (state.activeRoleId !== jobId) return;
    state.shortlist.links = data.links || [];
    state.shortlist.unreachable = !!data.unreachable;
    state.shortlist.publicBase = data.public_base_url || '';
    renderReviewLinks();
  } catch (err) {
    $('linkList').innerHTML =
      `<li class="mgr-empty">Could not load review links: ${esc(err.message)}</li>`;
  }
}

function renderReviewLinks() {
  const links = state.shortlist.links || [];
  $('linkList').innerHTML = links.length
    ? links.map((l) => `
        <li class="link-row link-${l.state}">
          <div>
            <div class="link-who">
              ${esc(l.manager.name)}
              <span class="badge badge-link-${l.state}">${LINK_STATE_LABEL[l.state]}</span>
            </div>
            <div class="link-meta">
              ${l.candidates} candidate${l.candidates === 1 ? '' : 's'} ·
              sent ${esc(shortDate(l.created_at))} ·
              ${l.opened_at
                ? `opened ${esc(shortDate(l.opened_at))}, ${l.views} view${l.views === 1 ? '' : 's'}`
                : '<b>never opened</b>'} ·
              ${l.actions} decision${l.actions === 1 ? '' : 's'}
            </div>
          </div>
          <div class="link-actions">
            <button class="btn btn-ghost btn-sm" data-copy="${esc(l.url)}"
                    title="Copy this manager's private link">Copy link</button>
            ${l.state === 'ok'
              ? `<button class="btn btn-ghost btn-sm" data-revoke="${esc(l.token)}"
                         title="Stop this link working">Revoke</button>` : ''}
          </div>
        </li>`).join('')
    : '<li class="mgr-empty">No review links yet — they are created when you send.</li>';

  for (const btn of $('linkList').querySelectorAll('[data-copy]')) {
    btn.addEventListener('click',
      () => copyToClipboard(btn.dataset.copy, 'Review link copied'));
  }
  for (const btn of $('linkList').querySelectorAll('[data-revoke]')) {
    btn.addEventListener('click', () => revokeLink(btn.dataset.revoke));
  }

  // The link in every email is built from PUBLIC_BASE_URL. When that is still
  // loopback the managers got a URL that resolves only on this machine, and
  // nothing about the delivered mail looks wrong -- so it is said here rather
  // than left to be discovered when nobody replies.
  const warn = $('linkWarn');
  warn.hidden = !state.shortlist.unreachable;
  warn.textContent =
    `Review links are being built from ${state.shortlist.publicBase}, which only `
    + `resolves on this machine — managers cannot open them. Set PUBLIC_BASE_URL `
    + `in .env to the address they can reach, and run the review server with `
    + `--review-only.`;
}

async function revokeLink(token) {
  const link = (state.shortlist.links || []).find((l) => l.token === token);
  if (!window.confirm(
    `Revoke ${link ? link.manager.name : 'this'} review link?\n\n`
    + 'It stops working immediately. Anything they already decided stays on '
    + 'the board. Send the shortlist again to give them a new one.')) return;
  try {
    const data = await api('/api/shortlist/links/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    toast(data.message);
    if (state.activeRoleId !== null) loadReviewLinks(state.activeRoleId);
    // THE ONE THIS PAGE MAY BE STANDING ON, AND DELIBERATELY LEFT ALONE.
    //
    // This list is every live link on the role, so a manager who has opened
    // the Pipeline tab is on it: their own self-served workspace, beside the
    // ones a recruiter mailed and looking no different. Revoking it is a
    // reasonable thing to click, and it used to leave the composer posting to
    // a dead token for the rest of the visit.
    //
    // Not cleared here. state.top.token is what selectable() gates the whole
    // panel on, and nulling it turns the Invite button into a button that
    // does nothing -- a worse failure than the one being fixed, and a silent
    // one. Not re-minted here either: that would put a fresh link straight
    // back into the list just cleared, which reads as a revoke that did not
    // work. The composer's renew() asks for a live workspace at the moment
    // one is actually needed, which is the moment somebody writes an
    // invitation; until then a revoked token grants nothing to anybody.
  } catch (err) {
    toast(err.message, true);
  }
}

/* --- sending ---------------------------------------------------------- */

function downloadShortlistXlsx() {
  if (state.activeRoleId === null) return;
  // A plain navigation rather than fetch + blob: the response is already an
  // attachment with its filename in the header, and the browser does the rest.
  window.location.assign(
    `/api/shortlist/${state.activeRoleId}/xlsx?limit=${shortlistLimit()}`
    + `${tierParam('&')}${shortlistScoresParam()}`);
}

async function previewShortlistMail() {
  const jobId = state.activeRoleId;
  if (jobId === null) return;
  const btn = $('shortlistPreviewBtn');
  btn.disabled = true;
  try {
    const note = encodeURIComponent($('shortlistNote').value);
    const data = await api(
      `/api/shortlist/${jobId}?limit=${shortlistLimit()}&preview=1&note=${note}`
      + tierParam('&') + shortlistScoresParam());
    $('mailSubject').textContent = data.email.subject;
    $('mailTo').textContent = data.managers.length
      ? `To ${data.managers.map((m) => `${m.name} <${m.email}>`).join(', ')}`
      : 'No manager assigned yet — this is how it would read.';
    // srcdoc rather than a data: URL -- the message is several kilobytes of
    // markup, and it renders in its own colours instead of inheriting the
    // dashboard's dark tokens, which the manager will never see.
    const frame = $('mailFrame');
    frame.srcdoc = data.email.html;
    $('mailPreview').hidden = false;
    // Grown to the whole message once it renders. A fixed-height iframe would
    // scroll a twenty-row table inside a drawer that also scrolls, and the
    // point of a preview is to read the thing end to end.
    frame.onload = () => {
      const doc = frame.contentDocument;
      if (doc) frame.style.height = `${doc.documentElement.scrollHeight + 24}px`;
    };
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function closeMailPreview() {
  $('mailPreview').hidden = true;
  // Cleared, and the grown height with it: the next preview is a different
  // role with a different row count and must measure itself again.
  $('mailFrame').srcdoc = '';
  $('mailFrame').style.height = '';
}

async function sendShortlist() {
  const jobId = state.activeRoleId;
  if (jobId === null) return;
  const { managers, rows } = state.shortlist;

  // Real mail to real people, and there is no unsend. The confirmation names
  // the count and every recipient, because those are the two things a misclick
  // gets wrong.
  const who = managers.map((m) => `${m.name} <${m.email}>`).join('\n  ');
  // Named in the confirmation and not only in the panel above it. This dialog
  // is the last screen before mail leaves, and "who is NOT on this list" is
  // exactly the thing a recruiter cannot recover after clicking.
  const held = state.shortlist.heldBack;
  const heldLine = held.length
    ? `\n\n${held.length} scored candidate${held.length === 1 ? ' is' : 's are'} `
      + `NOT on this list, because the AI did not finish their rubric:\n  `
      + `${held.map((c) => c.name).join('\n  ')}\n`
      + `Re-grade them first if you want them considered.`
    : '';
  // The last screen before mail leaves, so it says what is in the attachment
  // rather than what is usually in it. "No scores" printed over a send that
  // carries them is worse than saying nothing.
  const ok = window.confirm(
    `Email the top ${rows.length} candidates for `
    + `${state.activeRole?.title || 'this role'} to:\n\n  ${who}\n\n`
    + 'They will see names, contact details and links to each CV and '
    + ($('shortlistScores').checked
      ? 'assessment, and the attached spreadsheet carries the AI score.'
      : 'assessment — no scores.')
    + heldLine);
  if (!ok) return;

  const btn = $('shortlistSendBtn');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    const data = await api('/api/shortlist/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: jobId,
        limit: shortlistLimit(),
        note: $('shortlistNote').value,
        // Sent every time rather than only when ticked -- see
        // shortlistScoresParam() for why an omitted answer is not the same
        // answer as "no".
        include_scores: $('shortlistScores').checked,
        // The two halves of a tiered assignment are sent as two mails with two
        // attachments. Rank is only meaningful among people marked against the
        // same anchors, so one merged list would be a ranking of nothing.
        tier: state.activeTier,
      }),
    });
    toast(data.message);
    state.shortlist.lastSend = {
      at: new Date().toISOString(),
      to: data.result.sent,
      count: data.result.count,
    };
    // The send is what mints the review links, so the list below is now stale.
    loadReviewLinks(jobId);
    loadRoles();
    // Two addresses go out in that mail and they come from two settings. The
    // review link is the one worth shouting about -- it is what the email is
    // for -- so it is checked first, and the board only speaks up when the
    // review link was fine, rather than stacking a second red toast on top of
    // a message nobody can act on twice.
    if (data.result.unreachable) {
      toast('Sent — but the review links point at this machine only. Set '
            + 'PUBLIC_BASE_URL so managers can open them.', true);
    } else if (data.result.board_unreachable) {
      toast('Sent — the review links are fine, but the shortlist board link in '
            + 'the email points at this machine only. Set DASHBOARD_BASE_URL.',
            true);
    }
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.textContent = label;
    renderShortlist();
  }
}

/* --- the two views -----------------------------------------------------
 *
 * A list and a detail, never both. Clicking a role replaces the grid rather
 * than revealing a panel below it: the previous page let you scroll from one
 * role's candidates back up into another role's totals, which is exactly the
 * confusion a hiring decision cannot afford.
 */

const ROLE_TABS = ['candidates', 'shortlist', 'pipeline'];

/* Which of them this account is actually shown. A hiring manager gets two:
 * the people who applied, and where the ones they decided on ended up.
 *
 * The section between those two is the recruiter's -- assign the managers,
 * pick the top N, send the mail. Everything on it a manager could use was
 * either read-only or theirs alone, and the one thing that was theirs alone
 * (their booking link) now sits in the role header instead.
 *
 * THIS IS NOT THE ACCESS RULE. /api/shortlist/<id> makes its own check; this
 * only stops a manager being offered a tab that is nearly all greyed out. */
const visibleRoleTabs = () =>
  state.isAdmin ? ROLE_TABS : ROLE_TABS.filter((t) => t !== 'shortlist');

/* A tab this account cannot see falls back to the first one it can. Reached
 * from a pasted `#role=12&tab=shortlist` -- a recruiter sending a manager the
 * URL out of their own address bar -- and from the mail's board link if the
 * hash is ever wrong. Landing on Candidates beats landing on a blank pane. */
const allowedTab = (tab) =>
  visibleRoleTabs().includes(tab) ? tab : 'candidates';

function showView(name, scroll = true) {
  $('viewRoles').hidden = name !== 'roles';
  $('viewRole').hidden = name !== 'role';
  // A new view starts at its own top. Re-reading the view you are already on
  // does not -- a background refresh that yanks the page up loses your place.
  if (scroll) window.scrollTo({ top: 0, behavior: 'auto' });
}

function backToRoles(push = true) {
  state.activeRoleId = null;
  state.activeTier = null;
  state.activeRole = null;
  state.candidates = [];
  if (push && location.hash) {
    history.pushState(null, '', location.pathname + location.search);
  }
  renderRoles();
  showView('roles');
}

/* The open role's own numbers, in the same cards as the overview's. Read
 * left to right they are the same funnel, asked about one seat. */
function renderRoleStats(role) {
  const c = role?.counts || {};
  const p = role?.pipeline || {};
  const cards = [
    ['Submitted', (c.scored || 0) + (c.pending || 0) + (c.rejected || 0), true,
      'Assessments actually handed in'],
    ['Scored', c.scored || 0, false, 'Marked against the grid'],
    ['Pending', c.pending || 0, false, 'Submitted, waiting on AI evaluation'],
    ['Missing artefact', c.rejected || 0, false,
      'Rejected for arriving without a video or a resume'],
    ['Not submitted', c.in_progress || 0, false, 'Started but never handed in'],
    ['Interview', p.interview || 0, false, 'Booked in for an interview'],
    ['Hired', p.hired || 0, true, 'Offers accepted'],
    ['Rejected', p.rejected || 0, false, 'Turned down after being seen'],
  ];
  $('roleStats').innerHTML = cards.map(([label, value, accent, title]) => `
    <div class="stat${accent && value ? ' is-accent' : ''}${value ? '' : ' is-zero'}"
         title="${esc(title)}">
      <div class="stat-value">${value.toLocaleString()}</div>
      <div class="stat-label">${esc(label)}</div>
    </div>`).join('');

  $('tabCountCandidates').textContent = (c.total || 0).toLocaleString();
  $('tabCountPipeline').textContent =
    ((p.interview || 0) + (p.hired || 0) + (p.rejected || 0)) || '';
  const mgrs = (role?.managers || []).length;
  $('tabCountShortlist').textContent = mgrs || '';
}

/* Switching section. Each section fetches on its first visit for this role
 * and not again -- the candidate table is what the click was for, and it
 * should not queue behind a board and a hand-off nobody asked to see. */
function setRoleTab(tab, updateHash = true) {
  tab = allowedTab(tab);
  state.tab = tab;
  for (const btn of $('roleTabs').querySelectorAll('.viewtab')) {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-selected', String(on));
  }
  for (const pane of $('viewRole').querySelectorAll('.tabpane')) {
    pane.classList.toggle('is-active', pane.dataset.pane === tab);
  }
  // A tab clicked from the bottom of a long table should land at the top of
  // what it opened, not partway down it.
  if (updateHash) {
    writeHash();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  const jobId = state.activeRoleId;
  if (jobId === null) return;
  if (tab === 'shortlist' && !state.loaded.shortlist) {
    state.loaded.shortlist = true;
    loadShortlist(jobId);
  }
  if (tab === 'pipeline' && !state.loaded.pipeline) {
    state.loaded.pipeline = true;
    loadPipeline();
    loadRejected();
  }
  // Cheap on a revisit: it redraws what it already holds unless the role
  // changed or something asked it to re-read. Hides itself for an account
  // that is not a hiring manager on this role.
  if (tab === 'pipeline') loadTopCandidates();
}

/* #role=12&tab=shortlist -- a section of a role is a thing worth pasting to
 * somebody, not just the role.
 *
 * Opening a role pushes, so the browser's own Back button walks out of it the
 * way the button in the corner does. Changing tab inside a role replaces:
 * four tabs' worth of history between a reader and the grid they came from is
 * a Back button that stops meaning anything. */
function writeHash(push = false) {
  if (state.activeRoleId === null) return;
  const want = `#role=${state.activeRoleId}` +
    (state.activeTier ? `&tier=${encodeURIComponent(state.activeTier)}` : '') +
    (state.tab === 'candidates' ? '' : `&tab=${state.tab}`);
  if (location.hash === want) return;
  if (push) history.pushState(null, '', want);
  else history.replaceState(null, '', want);
}

// Back/forward, including the ones a keyboard or a mouse thumb button sends.
window.addEventListener('popstate', () => {
  const params = new URLSearchParams(location.hash.slice(1));
  const linked = Number(params.get('role'));
  if (!linked) {
    if (state.activeRoleId !== null) backToRoles(false);
    return;
  }
  const tab = allowedTab(params.get('tab'));
  const tier = params.get('tier') || null;
  if (linked === state.activeRoleId && tier === state.activeTier) {
    setRoleTab(tab, false);
  } else {
    openRole(linked, false, tab, tier);
  }
});

/* Open a role, or re-read the one already open.
 *
 * `tab` is only obeyed when it is asked for. Half a dozen callers re-open the
 * current role to pick up a score or a stage move, and a refresh that also
 * dropped the reader back onto Candidates would lose their place every time
 * they marked somebody hired from the board. */
async function openRole(jobId, updateHash = true, tab = null, tier = undefined) {
  // `undefined` means "whatever is already open", which is what the half-dozen
  // callers that re-read the current role want. `null` means the role has no
  // tiers, and a string picks one. Switching tier counts as opening something
  // else even though the job id has not moved: different candidates,
  // different anchors, different shortlist.
  const wantTier = tier === undefined
    ? (state.activeRoleId === jobId ? state.activeTier : null)
    : (tier || null);
  const reopening = state.activeRoleId === jobId && state.activeTier === wantTier;
  state.activeRoleId = jobId;
  state.activeTier = wantTier;
  if (!reopening) {
    state.loaded = {};
    // Another role's shortlist, and any ticks left on it. Dropped rather than
    // refetched: the pane it lives in is not on screen, and its first visit
    // will ask for this role's own.
    Object.assign(state.top, { token: null, jobId: null, rows: [], empty: '' });
    state.top.picked.clear();
  }
  const known = roleCard(jobId, wantTier) || roleCard(jobId);

  // Draw the header from the roles payload straight away: the title and the
  // eight tallies are already in hand, and waiting on the candidate request
  // to show them buys an empty screen for nothing.
  $('roleTitle').textContent = known ? known.title : 'Loading…';
  $('roleSub').textContent = known
    ? [known.slug,
       known.published ? 'published' : 'unpublished',
       known.rubric_source === 'pack' ? 'pack grid'
         : known.rubric_source === 'derived' ? 'derived grid' : 'no grid',
       // Which of the two standards this half is marked against. Named on the
       // header because everything below it -- the scores, the criterion
       // columns, the shortlist -- is that tier's and not the assignment's.
       ...(known.tier ? [`${known.tier} tier`] : []),
      ].join(' · ')
    : '';
  renderTierBar(known);
  renderRoleStats(known);
  renderHeroCal(known);
  renderInviteBtn(known);
  renderTopVisibility();

  // Both role-scoped lists start on this role. The selects stay, so widening
  // the board back out to every seat is one click and not a trip back.
  scopeRoleSelect('pipelineRole', jobId, known);
  scopeRoleSelect('rejectedRole', jobId, known);

  showView('role', !reopening);
  setRoleTab(tab || (reopening ? state.tab : 'candidates'), false);
  if (updateHash) writeHash(!reopening);

  $('candBody').innerHTML = '';
  $('candidateTitle').textContent = 'Candidates';

  // Fetched but never rendered: the criterion columns on the candidate table
  // are built from it, and the drawer's band ranges are read off it. See the
  // rubric section's header.
  loadRubric(jobId);

  try {
    const data = await api(`/api/evaluations/role/${jobId}${tierParam()}`);
    if (state.activeRoleId !== jobId || state.activeTier !== wantTier) return;
    state.activeRole = data.role;
    state.candidates = data.candidates;
    // The posting's name for a tiered half, the assignment's otherwise. The
    // server sends the assignment's title on the role object either way, so
    // the card is the only place the posting name lives.
    $('roleTitle').textContent = (known && known.tier && known.title)
      || data.role.title;
    renderCandidates();
    updateGradeStatus();
    // The shortlist borrows its scores from this response -- see renderTop().
    // It draws without them if the reader reached the Pipeline tab first, so
    // it is redrawn once they arrive.
    if (state.top.rows.length) renderTop();
  } catch (err) {
    $('candidateTitle').textContent = 'Could not load candidates';
    toast(err.message, true);
  }
}

/* The two-standard switch, and the button that fills it in.
 *
 * Drawn only where one assignment is sat by two postings graded differently.
 * It does two jobs and the second is the important one: switching halves is
 * obvious, but a reader also has to be told when the split is not real yet.
 * Until the postings have been matched, every candidate sits on the default
 * tier by fallback and the other card is empty -- which looks like "nobody
 * applied to the junior seat" and is actually "nobody has asked Workable
 * which seat they applied to". So the count of unmatched people is stated in
 * words next to the button that fixes it, rather than left to be inferred
 * from an empty table. */
function renderTierBar(card) {
  const bar = $('tierBar');
  // Read off the card and nothing else. `state.rubric` is fetched after this
  // draws and can still be the previously opened role's, which would put a
  // switch on a role that has nothing to switch between; the server sets
  // `tier` on every card of a tiered role, so the card is the reliable answer.
  if (!card?.tier) {
    bar.hidden = true;
    bar.innerHTML = '';
    return;
  }

  const cards = state.roles.filter((r) => r.id === state.activeRoleId && r.tier);
  const unresolved = cards.reduce((n, r) => n + (r.unresolved || 0), 0);
  bar.hidden = false;
  bar.innerHTML = `
    <div class="tier-switch" role="group" aria-label="Which posting">
      ${cards.map((r) => `
        <button type="button" class="tier-pill${
          (r.tier || null) === (state.activeTier || null) ? ' is-active' : ''}"
                data-tier="${esc(r.tier)}">
          ${esc(r.title)}
          <span class="tier-pill-count">${(r.counts?.total || 0).toLocaleString()}</span>
        </button>`).join('')}
    </div>
    ${unresolved ? `
      <div class="tier-note">
        <b>${unresolved.toLocaleString()}</b> candidate${unresolved === 1 ? '' : 's'}
        ${unresolved === 1 ? 'has' : 'have'} not been matched to a posting yet, so
        ${unresolved === 1 ? 'it sits' : 'they sit'} here and
        ${unresolved === 1 ? 'is' : 'are'} graded against this standard by default.
        <button type="button" class="btn btn-ghost btn-sm" id="tierResolveBtn">
          Match to postings
        </button>
      </div>` : ''}`;

  for (const pill of bar.querySelectorAll('.tier-pill')) {
    pill.addEventListener('click', () => {
      if ((pill.dataset.tier || null) === (state.activeTier || null)) return;
      openRole(state.activeRoleId, true, state.tab, pill.dataset.tier);
    });
  }
  const resolve = $('tierResolveBtn');
  if (resolve) resolve.addEventListener('click', resolveTiers);
}

/* Ask Workable which posting each candidate applied to.
 *
 * Changes no scores, and says so: anyone already graded keeps the verdict the
 * old tier produced until they are re-graded. Moving a card and re-marking a
 * candidate are two different actions, and collapsing them would re-spend a
 * model call on everyone every time somebody clicked this. */
async function resolveTiers() {
  const jobId = state.activeRoleId;
  if (jobId === null) return;
  const btn = $('tierResolveBtn');
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Matching…'; }
  try {
    const data = await api('/api/evaluations/tiers/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId }),
    });
    toast(data.message);
    await loadRoles();
    await openRole(jobId, false, state.tab);
  } catch (err) {
    toast(err.message, true);
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* Point a cross-role select at the open role.
 *
 * Those selects only list roles that have something in them, so the role just
 * opened may not be an option yet -- a role with no rejections still needs to
 * say "none for this role" rather than quietly showing every other seat's. */
function scopeRoleSelect(id, jobId, role) {
  const select = $(id);
  if (!select.querySelector(`option[value="${jobId}"]`)) {
    const opt = document.createElement('option');
    opt.value = String(jobId);
    opt.textContent = role ? role.title : `Role ${jobId}`;
    select.append(opt);
  }
  select.value = String(jobId);
}

/* The grid's criteria as table columns.
 *
 * Within a family every candidate is marked against the same criteria, which
 * is what makes a column readable: "who ranked risk best out of these 320
 * people" is only a question you can ask when everyone faced the same anchors.
 * The labels come from the rubric response so they can never drift from the
 * grid the server actually marked against.
 *
 * One family is marked at two tiers -- the AI Strategist pair, senior and
 * associate, sitting one assessment. The rows and their weights are identical
 * across the two by rule, enforced in rubric_pack's validation, so the columns
 * still line up for everyone on the assignment; what differs is the anchors
 * behind a mark, and the header tooltip here shows the default tier's. A 4 on
 * triage means the same thing either way. A 4 on the background row does not,
 * and the drawer's grid_tier is where that is read. */
function categoryColumns() {
  if (!state.showMatrix || !Array.isArray(state.rubric?.blocks)) return [];
  return state.rubric.blocks.flatMap((block) =>
    block.criteria.map((c) => ({ ...c, block: block.key })));
}

// "Deal arithmetic and pricing (1.1)" -> "DAP": the full label, weight and 5
// anchor live in the header's tooltip, since seven spelled-out headings would
// push the table off the screen.
const abbreviate = (label) => String(label || '').replace(/\(.*?\)/g, '')
  .split(/[\s/&-]+/).filter(Boolean).map((w) => w[0].toUpperCase()).join('').slice(0, 3);

const categoryScore = (candidate, key) => {
  const row = (candidate.evaluation?.grid || []).find((m) => m.key === key);
  return typeof row?.score === 'number' ? row.score : null;
};

// A criterion mark is 1-5, not 0-100, so it needs its own colour ramp: a 3 is
// the middle of the scale, and colouring it by scoreClass would paint every
// competent submission red.
function ratingClass(rating) {
  if (rating == null) return 'score-none';
  if (rating >= 4) return 'score-strong';
  if (rating >= 3) return 'score-mid';
  return 'score-low';
}

function renderCandHead() {
  const cats = categoryColumns();
  $('candHead').innerHTML = `
    <th data-sort="candidate_name">Candidate</th>
    <th data-sort="score" class="num">Score</th>
    ${cats.map((c) => `
      <th data-sort="cat:${c.key}" class="num cat-col"
          title="${esc(c.label)} — weight ${c.weight} of 100. A 5 is: ${
            esc(c.anchors?.['5'] || '')}">${esc(abbreviate(c.label))}</th>`).join('')}
    <th data-sort="recommendation">Verdict</th>
    <th>Brief</th>
    <th>Artefacts</th>
    <th data-sort="submitted_at">Submitted</th>
    <th data-sort="status">Status</th>`;

  for (const th of $('candHead').querySelectorAll('th[data-sort]')) {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      // Re-clicking the active column flips direction; a new column starts
      // descending, which is what you want for scores and dates.
      state.sort = state.sort.key === key
        ? { key, dir: state.sort.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'desc' };
      renderCandidates();
    });
  }
}

/* The role's best N, by the SAME rule the shortlist email uses.
 *
 * This is a deliberate copy of mongo_store.top_candidates(), and the two have
 * to stay in step: a manager reading "Top 20" here and a manager reading "top
 * 20" in their email must be looking at the same twenty people, or the next
 * conversation is about which list is the real one. The rule, in full:
 *
 *   - scored only. `evaluation.score` has to be a number. An ungraded row is
 *     not a weak candidate, it is an unanswered question, and padding a thin
 *     role's top 20 with them makes ungraded people read as ranked.
 *   - not artefact-rejected. `decision.status === 'rejected'` means a missing
 *     CV or assessment, not a hiring decision.
 *   - not already moved along the board. Somebody booked, hired or turned
 *     down is not awaiting a decision, so they are not on a shortlist of
 *     people who are -- and they are the manager's own past clicks coming
 *     back at them.
 *   - highest score first, and the earlier submission wins a tie, which is
 *     the sort Mongo is asked for.
 *
 * Returned as a Set of ids rather than an ordered list: the caller still has
 * its own sort to apply, and "which twenty" is the only question this answers.
 * Ranking by whatever column the reader happened to click would mean Top 20
 * sorted by name gave the first twenty of the alphabet.
 */
/* Everyone marked, not yet turned down, not yet anywhere on the board.
 *
 * READ FROM FLAGS, NOT FROM THE SCORE. A hiring manager's payload carries no
 * `evaluation` and no `decision` -- see MANAGER_SUBMISSION_FIELDS -- so the old
 * `typeof c.evaluation?.score === 'number'` test was false for every row on
 * their screen, and the list behind their invite button came back empty. The
 * server sends `graded` and `rejected` for exactly this, and a recruiter's
 * payload still carries the originals, so both are read and either will do.
 *
 * The ORDER still needs the number, and only a recruiter has it. A manager
 * gets submission order instead, which is honest: this list is "who is ready
 * to be looked at", not a ranking, and their ranking is the shortlist. */
function awaitingDecision() {
  const graded = (c) => (c.graded ?? typeof c.evaluation?.score === 'number');
  const rejected = (c) => (c.rejected ?? (c.decision?.status === 'rejected'));
  return state.candidates
    .filter((c) => graded(c) && !rejected(c) && !stageOf(c))
    .sort((a, b) => ((b.evaluation?.score ?? 0) - (a.evaluation?.score ?? 0))
                 || String(a.submitted_at || '').localeCompare(
                      String(b.submitted_at || '')));
}

function topCandidateIds(limit) {
  if (!limit) return null;                      // "Everyone" -- no cap at all
  return new Set(awaitingDecision().slice(0, limit).map((c) => c.id));
}

function visibleCandidates() {
  const term = $('candSearch').value.trim().toLowerCase();
  const status = $('statusFilter').value;
  const top = topCandidateIds(Number($('topN').value) || 0);

  const rows = state.candidates.filter((c) => {
    const s = c.decision?.status;
    if (top && !top.has(c.id)) return false;
    if (status && s !== status) return false;
    if (term && !`${c.candidate_name || ''} ${c.candidate_email || ''}`
      .toLowerCase().includes(term)) return false;
    return true;
  });

  const { key, dir } = state.sort;
  const sign = dir === 'asc' ? 1 : -1;
  const value = (c) => {
    if (key === 'score') return c.evaluation?.score ?? -1;
    if (key.startsWith('cat:')) return categoryScore(c, key.slice(4)) ?? -1;
    // The words are a ranking now, so the column sorts by rank rather than by
    // the alphabet. Negated so that, as with score, bigger is better and
    // descending puts the strongest first.
    if (key === 'recommendation') {
      const i = REC_RANK.indexOf(recLabel(c.evaluation));
      return i === -1 ? -REC_RANK.length : -i;
    }
    if (key === 'status') return c.decision?.status ?? '';
    return c[key] ?? '';
  };
  return rows.sort((a, a2) => {
    const [x, y] = [value(a), value(a2)];
    if (x === y) return 0;
    return (typeof x === 'number' && typeof y === 'number' ? x - y
      : String(x).localeCompare(String(y))) * sign;
  });
}

function renderCandidates() {
  const rows = visibleCandidates();
  const cats = categoryColumns();
  $('candEmpty').hidden = rows.length > 0;
  // The role's name is the heading of the page now, so repeating it here would
  // be the same words twice on one screen. What the panel can say instead is
  // how much of the role the filters are currently showing.
  const total = state.candidates.length;
  $('candidateTitle').textContent = rows.length === total
    ? `Candidates (${total.toLocaleString()})`
    : `Candidates (${rows.length.toLocaleString()} of ${total.toLocaleString()})`;
  renderCandHead();

  $('candBody').innerHTML = rows.map((c) => {
    const ev = c.evaluation;
    const status = c.decision?.status || 'unknown';
    const reason = REASON_LABEL[c.decision?.reason] || c.decision?.reason || '';
    const artefact = (present, label) =>
      `<span class="artefact ${present ? 'has' : 'missing'}">${label}</span>`;

    return `
      <tr class="row-click${status === 'rejected' || status === 'in_progress' ? ' row-inert' : ''}"
          data-id="${c.id}">
        <td>
          <div class="cand-name">${esc(c.candidate_name || '—')}${
            stageOf(c)
              ? ` <span class="badge ${STAGE_CLASS[stageOf(c)]}">${
                  esc(STAGE_LABEL[stageOf(c)])}</span>`
              : ''}${
            portalQueue(c)
              ? ` <span class="badge badge-queue">${esc(portalQueue(c))}</span>`
              : ''}</div>
          <div class="cand-email">${esc(c.candidate_email || '')}</div>
        </td>
        ${scoreCell(ev, c)}
        ${cats.map((cat) => {
          const s = categoryScore(c, cat.key);
          return `<td class="num cat-col">${s === null
            ? '<span class="dim">—</span>'
            : `<span class="cat-score ${ratingClass(s)}">${s}</span>`}</td>`;
        }).join('')}
        <td>${ev ? `<span class="badge ${recClass(recLabel(ev))}">${
          esc(recLabel(ev))}</span>` : '<span class="dim">—</span>'}</td>
        <td><div class="brief-cell">${esc(ev?.brief || reason)}</div></td>
        <td><span class="artefacts">${
          artefact(!!c.video_link, 'VID')}${artefact(!!c.resume_link, 'CV')}</span></td>
        <td class="nowrap dim">${shortDate(c.submitted_at)}</td>
        <td><span class="badge ${STATUS_CLASS[status] || ''}">${
          esc(STATUS_LABEL[status] || status)}</span></td>
      </tr>`;
  }).join('');

  for (const tr of $('candBody').querySelectorAll('tr')) {
    tr.addEventListener('click', () => openDrawer(Number(tr.dataset.id)));
  }
}

function updateGradeStatus() {
  const pending = state.candidates.filter((c) => c.decision?.status === 'pending').length;
  const scored = state.candidates.filter((c) => c.decision?.status === 'scored').length;
  const btn = $('gradeBtn');

  if (!state.evaluatorConfigured) {
    $('gradeStatus').textContent =
      'AI evaluation is not configured — set LLM_API_KEY in .env to enable grading.';
    btn.disabled = true;
    return;
  }
  $('gradeStatus').textContent = `${scored} scored · ${pending} pending evaluation`;
  btn.disabled = pending === 0;
  renderInviteBtn(activeCard());
}

/* --- grading ---------------------------------------------------------- */

async function gradePending() {
  const btn = $('gradeBtn');
  const limit = Number($('gradeLimit').value) || 5;
  btn.disabled = true;
  const previous = btn.textContent;
  btn.textContent = 'Grading…';
  $('gradeStatus').textContent = `Grading up to ${limit} submission(s)…`;

  try {
    const result = await api('/api/evaluations/grade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: state.activeRoleId, limit,
                             tier: state.activeTier }),
    });
    toast(result.message);
    if (result.failed?.length) {
      toast(`${result.failed.length} failed: ${result.failed[0].error}`, true);
    }
    await openRole(state.activeRoleId);   // refresh scores in place
    await loadRoles();                    // and the role tallies
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.textContent = previous;
    updateGradeStatus();
  }
}

/* --- drawer ----------------------------------------------------------- */

async function openDrawer(submissionId) {
  const drawer = $('drawer');
  drawer.hidden = false;
  $('drawerBody').innerHTML = '<p class="empty">Loading submission…</p>';

  try {
    const c = await api(`/api/evaluations/submission/${submissionId}`);
    $('drawerRole').textContent = c.job_title || '';
    $('drawerName').textContent = c.candidate_name || '—';
    $('drawerEmail').textContent = c.candidate_email || '';
    $('drawerBody').innerHTML = drawerContent(c);

    const reconsider = $('drawerBody').querySelector('[data-reconsider]');
    if (reconsider) {
      reconsider.addEventListener('click', () => setDecision(c.id, 'pending'));
    }
    const reject = $('drawerBody').querySelector('[data-reject]');
    if (reject) reject.addEventListener('click', () => setDecision(c.id, 'rejected'));
    const evaluate = $('drawerBody').querySelector('[data-evaluate]');
    if (evaluate) {
      evaluate.addEventListener('click', () => evaluateOne(c.id, evaluate));
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-stage]')) {
      btn.addEventListener('click', async () => {
        const stage = btn.dataset.stage || null;
        const words = $('stageNote').value.trim();
        const detail = stageMailFields(c);
        // The same box is a note on the way in and a reason on the way out;
        // stored under the field the stage is read by, so the board's Reason
        // column is never empty on a rejection someone explained.
        if (words) detail[stage === 'rejected' ? 'reason' : 'note'] = words;

        btn.disabled = true;
        const ok = await moveStage(c.id, stage, detail);
        if (ok) openDrawer(c.id);
        else btn.disabled = false;
      });
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-mailpreview]')) {
      btn.addEventListener('click', () => previewStageEmail(c, btn.dataset.mailpreview));
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-mailsend]')) {
      btn.addEventListener('click', () => sendStageEmail(c, btn.dataset.mailsend, btn));
    }
  } catch (err) {
    $('drawerBody').innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

/* What the drawer's form says about the email: who signs it, and the line the
 * candidate reads. No calendar -- the only mail that carries one is the
 * invitation, and that is not sent from here.
 *
 * `email_note` is deliberately separate from the internal note above it. One
 * box is for the next reviewer and one is for the candidate, and a form that
 * quietly forwarded the first to the second would put a private remark in
 * front of the person it is about. */
function stageMailFields(c) {
  const picked = $('drawerBody').querySelector('#stageManager')?.value;
  const owner = picked
    ? (c.managers || []).find((m) => m.email === picked)
    : managerFor(c);
  return {
    // No `notify` -- the server's switch decides whether a move sends, and a
    // hardcoded false here would quietly outvote it the day it is turned on.
    manager_email: owner?.email || undefined,
    email_note: $('stageEmailNote')?.value.trim() || undefined,
  };
}

/* The send. One click, one candidate, one message -- the only thing on this
 * page that reaches somebody outside the company.
 *
 * It asks first, by name and by address, because there is no unsend. The
 * server refuses a second copy of the same message on its own, so the confirm
 * is about the first one being right rather than about clicking twice; a
 * candidate who has already been written to comes back as "already emailed"
 * and the resend has to be asked for outright. */
async function sendStageEmail(c, stage, btn) {
  const fields = stageMailFields(c);
  const who = c.candidate_name || 'this candidate';
  if (!window.confirm(
    `Send ${who} the rejection at ${c.candidate_email || 'their address'} now?`)) {
    return;
  }

  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    const result = await api('/api/pipeline/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        submission_id: c.id,
        stage,
        manager_email: fields.manager_email,
        email_note: fields.email_note,
      }),
    });
    toast(result.message, !result.mail?.sent);
    // Reopened rather than patched: the card carries the mail history, and a
    // send that is not visible in it is a send somebody will make twice.
    if (result.mail?.sent) openDrawer(c.id);
    else btn.textContent = label;
  } catch (err) {
    toast(err.message, true);
    btn.textContent = label;
  } finally {
    btn.disabled = false;
  }
}

/* The candidate's email, rendered by the server function that sends it, in the
 * same drawer the shortlist preview uses. A manager should be able to read
 * what a click will put in someone's inbox before making it. */
async function previewStageEmail(c, stage) {
  const fields = stageMailFields(c);
  const query = new URLSearchParams({ submission_id: String(c.id), stage });
  for (const key of ['manager_email', 'email_note']) {
    if (fields[key]) query.set(key, fields[key]);
  }

  try {
    const data = await api(`/api/pipeline/preview?${query}`);
    $('mailSubject').textContent = data.email.subject;
    $('mailTo').textContent = `To ${data.to_name} <${data.to}>`
      + (data.already_sent ? ` · already emailed ${shortDate(data.already_sent.at)}` : '');
    $('mailPreview').hidden = false;
    // srcdoc and the same grow-to-fit as the shortlist preview: the message
    // renders in its own colours rather than inheriting the dashboard's dark
    // tokens, which the candidate will never see.
    const frame = $('mailFrame');
    frame.srcdoc = data.email.html;
    frame.onload = () => {
      const doc = frame.contentDocument;
      if (doc) frame.style.height = `${doc.documentElement.scrollHeight + 24}px`;
    };
  } catch (err) {
    toast(err.message, true);
  }
}

/* The scored grid, one row per criterion, grouped into its blocks.
 *
 * Every row shows the mark, the anchor it was marked against, and the points
 * it contributed -- score x weight / 5 -- so the total can be added up by hand
 * and any single row argued with on its own.
 *
 * Four blocks on most seats. Three grids carry a fifth, Background and
 * experience: the AI Strategist pair splits 40/40/6/7/7 and the Social Media
 * and Marketing Intern grid splits 55/10/10/13/12, rather than 70/10/10/10.
 * Nothing here is hard-coded to any of those shapes -- the blocks, their
 * labels and their point totals all arrive on the verdict -- so a grid that
 * states its own split renders it without this function knowing which one it
 * is. */
function gridTable(ev) {
  if (!Array.isArray(ev?.grid) || !ev.grid.length) return legacyMatrixTable(ev);

  const blocks = Array.isArray(ev.blocks) && ev.blocks.length
    ? ev.blocks
    : [{ key: 'all', label: 'Criteria', points: 100,
         criteria: ev.grid.map((r) => r.key) }];

  const body = blocks.map((block) => {
    const rows = ev.grid.filter((r) => block.criteria.includes(r.key));
    if (!rows.length) return '';
    return `
      <tr class="grid-block-row">
        <th colspan="4">${esc(block.label)}
          <span class="matrix-weight">${
            block.earned == null ? '—' : fmtScore(block.earned)} / ${block.points}</span>
        </th>
      </tr>
      ${rows.map((row) => {
        const marked = typeof row.score === 'number';
        return `
          <tr>
            <th scope="row">
              ${esc(row.label)}
              <span class="matrix-weight">wt ${row.weight}</span>
            </th>
            <td class="num">
              <span class="matrix-score ${marked ? ratingClass(row.score) : 'score-none'}"
                    ${row.anchor ? `title="${esc(row.anchor)}"` : ''}>${
                marked ? row.score : '—'}</span>
            </td>
            <td class="num dim points">${row.points == null ? '—'
              : `${fmtScore(row.points)}<span class="of">/${row.max_points}</span>`}</td>
            <td class="matrix-evidence">${
              row.evidence ? esc(row.evidence)
                : '<span class="dim">No evidence given</span>'}</td>
          </tr>`;
      }).join('')}`;
  }).join('');

  /* The renormalisation, spelled out with its consequence rather than as a
   * footnote. The old wording was accurate and read as housekeeping: "the
   * marked rows renormalised to 100" does not tell a recruiter that ONE row
   * marked 5 renormalises to exactly 100.0 — the same headline a perfect full
   * grid produces, from a candidate nobody graded. So this says what fraction
   * was judged, names the rows that were not, and says what the system does
   * about it, because a warning nothing acts on is a warning people learn to
   * scroll past. */
  const missed = (ev.grid_unmarked || []).length
    ? ev.grid_unmarked
    : ev.grid.filter((r) => r.score == null).map((r) => r.key);
  const labelFor = (key) =>
    (ev.grid.find((r) => r.key === key) || {}).label || key;
  const incomplete = isProvisional(ev)
    ? `<p class="matrix-note warn"><b>Partly graded — this total is not
       comparable.</b> The AI marked ${
         typeof ev.grid_marked === 'number' ? ev.grid_marked : ev.grid.length - missed.length
       } of ${
         typeof ev.grid_of === 'number' ? ev.grid_of : ev.grid.length
       } criteria${
         typeof ev.grid_coverage === 'number'
           ? `, ${Math.round(ev.grid_coverage * 100)}% of the rubric's weight`
           : ''} and the total above is those rows scaled up to 100 —
       so a grid with one row left at 5 reads as 100.0, exactly like a full one.
       ${missed.length ? `Never marked: ${
         missed.map((k) => esc(labelFor(k))).join(', ')}.` : ''}
       This usually means the grader's JSON ran past its output budget, not
       that anything was wrong with the submission. They are held off
       shortlists until someone re-grades them — use Re-evaluate below.</p>`
    : '';

  /* The one mark the system overrules, so it says so rather than showing a 3
   * a reviewer would read as something the grader found in a CV. Only ever
   * upward, only when there was genuinely no CV to read. */
  const floored = ev.background_floored;
  const flooredNote = floored
    ? `<p class="matrix-note warn"><b>Experience scored at its anchor, not from
       a CV.</b> No CV text could be read for this candidate, so the
       ${esc(floored.key)} row is held at ${floored.now} — the rubric's own
       mark for absent information — rather than the ${floored.was} the grader
       gave it. Roughly two in five CV links never extract (a private file, a
       profile page, a scan), and that is not the candidate's doing. Extract
       the CV and re-grade to have this row actually judged.</p>`
    : '';

  return `
    <div class="drawer-section">
      <h3>Scoring grid ${ev.grid_unit ? `<span class="dim">· ${esc(ev.grid_unit)}${
        ev.grid_tier ? `, ${esc(ev.grid_tier)} tier` : ''}</span>` : ''}</h3>
      <table class="matrix grid">
        <thead>
          <tr><th scope="col">Criterion</th>
              <th scope="col" class="num">1–5</th>
              <th scope="col" class="num">Points</th>
              <th scope="col">What earned it</th></tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
      <p class="matrix-note">${blendNote(ev)}</p>
      ${flooredNote}
      ${incomplete}
    </div>`;
}

/* How the rows below add up to the score above.
 *
 * These stopped being the same number on 2026-08-14-c, when the CV started
 * carrying part of the final score. The rows still sum to the rubric total; the
 * score is a share of that plus the CV's mark, so a reviewer adding the column
 * up by hand lands well away from the headline and concludes the table is
 * broken. Spelling the arithmetic out is the whole job of this line.
 *
 * Since 2026-08-15 the share is the seat's own -- 25% on a full-stack build,
 * 60% on Customer Success -- so the sentence says "for this seat". Two cards
 * with the same rubric total and the same CV mark can now legitimately show
 * different final scores, and a reviewer who reads the split as a company-wide
 * rule will file that as a bug.
 *
 * Verdicts graded before any of it carry no `cv_weight`, and for them the old
 * sentence is still exactly true, so they keep it. */
function blendNote(ev) {
  const band = ev.auto_failed ? '' : ` (${esc(bandRange(ev.score))})`;
  const tail = `${esc(recLabel(ev))}${band}.`;
  const weight = Number(ev.cv_weight) || 0;

  /* Some grids score the track record as a row IN the table above rather than
   * as a second document blended in afterwards, so their cards carry no CV
   * weight. Left to the branch below, that reads as "experience was not scored
   * on this seat" -- the exact opposite of the truth. Say which arrangement is
   * in force before saying the total, and take the row's worth from the block
   * rather than naming a number: it is 40 points on the AI Strategist pair,
   * where experience is the single heaviest row, and 10 on the intern seat,
   * where it is deliberately the lightest. */
  const bg = (ev.blocks || []).find((b) => b.key === 'background');
  if (bg && !weight) {
    return `Each row is score × weight ÷ 5. Total ${fmtScore(ev.score)} of 100 —
      ${tail} Experience is scored <b>inside</b> this grid, in the
      ${esc(bg.label)} row worth ${bg.points} points, not blended in afterwards
      — so there is no separate CV score on this seat and nothing is forfeited
      when a CV cannot be read.`;
  }

  if (!weight) {
    return `Each row is score × weight ÷ 5. Total ${fmtScore(ev.score)} of 100 — ${tail}`;
  }

  const rubric = typeof ev.rubric_score === 'number' ? ev.rubric_score : ev.score;
  const rubricPct = Math.round((1 - weight) * 100);
  const cvPct = Math.round(weight * 100);
  const cv = ev.cv_assessment;
  const split = `This seat splits ${rubricPct}% assessment to ${cvPct}% experience${
    ev.cv_weight_source === 'default'
      ? ', the fallback split — nobody has weighted this seat yet' : ''}.`;

  if (ev.cv_applied && cv && typeof cv.score === 'number') {
    return `Each row is score × weight ÷ 5, summing to a rubric total of
      <b>${fmtScore(rubric)}</b>. ${split} ${fmtScore(rubric)} × ${rubricPct}% +
      ${fmtScore(cv.score)} × ${cvPct}% = <b>${fmtScore(ev.score)}</b> — ${tail}`;
  }

  if (ev.cv_unmarked) {
    return `Each row is score × weight ÷ 5, summing to a rubric total of
      <b>${fmtScore(rubric)}</b>. ${split} The grader returned no marks for this
      candidate's CV, which is our failure and not theirs, so nothing was
      forfeited and the score is the assessment alone —
      <b>${fmtScore(ev.score)}</b>. Re-grade to have the CV judged. ${tail}`;
  }

  return `Each row is score × weight ÷ 5, summing to a rubric total of
    <b>${fmtScore(rubric)}</b>. ${split} This candidate has no readable CV, so the
    ${cvPct}% it carries was forfeited: ${fmtScore(rubric)} × ${rubricPct}% =
    <b>${fmtScore(ev.score)}</b>, against a ceiling of ${rubricPct} — ${tail}`;
}

/* The CV's own marks -- the other side of the score since 2026-08-14-c, and
 * since 2026-08-15 a share that differs by seat.
 *
 * Rendered as its own table beside the grid rather than folded into it,
 * because the two are marked from different documents against different
 * anchors, and a reviewer arguing about a score needs to see which half
 * produced it. A 63 built from a weak answer and a strong CV is a different
 * candidate from a 63 built the other way round.
 *
 * The missing case gets a row of its own rather than an empty table. 38% of
 * candidates land there, none of them through anything they did, and the
 * forfeited points are the single most likely thing on this page to be
 * disputed -- so it says what happened and what it cost. */
function cvTable(ev) {
  const weight = Number(ev?.cv_weight) || 0;
  if (!weight) return '';                       // graded before the CV scored
  const cv = ev.cv_assessment;
  if (!cv || !Array.isArray(cv.criteria) || !cv.criteria.length) return '';

  const pct = Math.round(weight * 100);

  /* Two ways to end up unscored, and they are opposite failures. One is a CV
     we could not read; the other is a CV we read and the grader skipped. The
     second costs the candidate nothing and must not be dressed up as the
     first, or a reviewer reads "no readable CV" on a candidate whose CV is
     sitting one click away in the drawer. */
  if (!cv.scored && ev.cv_unmarked) {
    return `
      <div class="drawer-section">
        <h3>CV score <span class="dim">· not marked</span></h3>
        <p class="matrix-note warn">
          <b>The grader did not mark this CV.</b> The CV was read successfully
          and sent with the submission, and the model returned no marks for it —
          our failure, not the candidate's. Rather than forfeit the ${pct}% it
          carries, this candidate is scored on the assessment alone
          (<b>${fmtScore(ev.rubric_score)}</b>). Re-grade to have the CV judged;
          the score will move once it is.
        </p>
      </div>`;
  }

  if (!cv.scored) {
    return `
      <div class="drawer-section">
        <h3>CV score <span class="dim">· ${pct}% of the final score for this seat</span></h3>
        <p class="matrix-note">
          No readable CV. The linked file was private, a profile page, or a
          scan with no text layer — this is a gap in our extraction, not
          something the candidate did. Under the
          <code>${esc(ev.cv_missing_policy || 'forfeit')}</code> policy the
          ${pct}% it carries was forfeited, capping this candidate at
          ${Math.round((1 - weight) * 100)} however good the answer.
        </p>
      </div>`;
  }

  const rows = cv.criteria.map((row) => {
    const marked = typeof row.score === 'number';
    return `
      <tr>
        <th scope="row">${esc(row.label || row.key)}</th>
        <td class="num">
          <span class="matrix-score ${marked ? ratingClass(row.score) : 'score-none'}">${
            marked ? row.score : '—'}</span>
        </td>
        <td class="matrix-evidence">${
          row.evidence ? esc(row.evidence)
            : '<span class="dim">No evidence given</span>'}</td>
      </tr>`;
  }).join('');

  return `
    <div class="drawer-section">
      <h3>CV score <span class="dim">· ${pct}% of the final score for this seat</span></h3>
      <table class="matrix grid">
        <thead>
          <tr><th scope="col">Criterion</th>
              <th scope="col" class="num">1–5</th>
              <th scope="col">What earned it</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="matrix-note">
        Marked from the CV alone, against this seat. The three are equally
        weighted and averaged: <b>${fmtScore(cv.score)} of 100</b>, contributing
        ${fmtScore(cv.score * weight)} points of the final
        ${fmtScore(ev.score)}. These marks never touch the grid above — the
        answer is scored on its own.
      </p>
    </div>`;
}

/* Evaluations graded before the pack rewrite carry the old five-category
 * `matrix` with 0-100 marks. They keep their score and brief and render in
 * their own shape rather than being silently redrawn as if they had been
 * marked against anchors they never saw. */
function legacyMatrixTable(ev) {
  if (!Array.isArray(ev?.matrix) || !ev.matrix.length) return '';
  const rows = ev.matrix.map((row) => {
    const marked = typeof row.score === 'number';
    return `
      <tr>
        <th scope="row">${esc(row.label)}
          <span class="matrix-weight">${Math.round(row.weight * 100)}%</span></th>
        <td class="num"><span class="matrix-score ${
          marked ? scoreClass(row.score) : 'score-none'}">${
          marked ? row.score : '—'}</span></td>
        <td class="matrix-evidence">${
          row.evidence ? esc(row.evidence) : '<span class="dim">No evidence given</span>'}</td>
      </tr>`;
  }).join('');
  return `
    <div class="drawer-section">
      <h3>Evaluation matrix <span class="dim">· pre-rubric-pack</span></h3>
      <table class="matrix"><tbody>${rows}</tbody></table>
      <p class="matrix-note">Marked against the old five-category matrix, before
        the rubric pack. Re-grade this role to score it against the family grid.</p>
    </div>`;
}

/* Triage, auto-fails and the fraud log: the parts of the pack that sit beside
 * the score rather than inside it. */
function findingsBlock(ev) {
  if (!ev) return '';
  const t = ev.triage;
  const list = (items, key, cls, title) => (items?.length ? `
    <div class="rubric-block ${cls}">
      <h3>${title}</h3>
      <ul>${items.map((f) => `<li><b>${esc(f[key])}</b>${
        f.evidence ? ` — ${esc(f.evidence)}` : ''}</li>`).join('')}</ul>
    </div>` : '');

  /* What the CV did or did not corroborate. Shown only when there was a CV to
     read: "no_cv" is the answer for two candidates in five, and a row saying
     so on 40% of the list would train reviewers to skim past the 1% that says
     something.

     This is the consistency signal and it still carries no points, which is
     worth saying out loud now that the CV carries a scored share somewhere else
     on this page -- the two are separate, and a reviewer who conflates them
     will read "consistent" as the thing that moved the score. */
  const cv = ev.cv_check;
  const scored = Number(ev.cv_weight) > 0;
  const cvRow = (cv && cv.verdict && cv.verdict !== 'no_cv') ? `
    <div class="rubric-block ${cv.verdict === 'contradicted' ? 'rubric-fraud' : ''}">
      <h3>CV consistency check — ${scored
        ? 'no points; separate from the CV score above'
        : 'background only, worth no points'}</h3>
      <ul><li><b>${cv.verdict === 'contradicted'
        ? 'CV contradicts the submission'
        : 'CV consistent with the submission'}</b>${
        cv.note ? ` — ${esc(cv.note)}` : ''}</li></ul>
    </div>` : '';

  /* Auto-fails the grader hedged. Shown, because a reviewer may want to check
     one by hand, but visibly separated from the ones that acted: an auto-fail
     ends a candidacy and a guess must never be read as having done so. */
  const disputedRow = ev.disputed_auto_fails?.length ? `
    <div class="rubric-block">
      <h3>Unproven auto-fail claims — not applied</h3>
      <ul>${ev.disputed_auto_fails.map((f) => `<li><b>${esc(f.rule)}</b>${
        f.evidence ? ` — ${esc(f.evidence)}` : ''}</li>`).join('')}</ul>
      <p class="matrix-note">The grader hedged these, so they were not acted on
        and did not affect the band. Worth checking by hand if one looks real.</p>
    </div>` : '';

  if (!t && !ev.auto_fails?.length && !ev.fraud_tells?.length && !cvRow
      && !disputedRow) return '';

  return `
    <div class="drawer-section">
      ${t ? `
        <h3>Triage <span class="dim">· ${t.passed} of ${t.of} · ${
          esc(t.route_label)}</span></h3>
        <ul class="triage-checks">${(t.checks || []).map((c) => `
          <li class="${c.pass === true ? 'yes' : c.pass === false ? 'no' : 'unknown'}">
            <span class="tick">${c.pass === true ? '✓' : c.pass === false ? '✗' : '?'}</span>
            <span>${esc(c.label)}${c.note ? ` <i class="dim">${esc(c.note)}</i>` : ''}</span>
          </li>`).join('')}
        </ul>` : ''}
      ${list(ev.auto_fails, 'rule', 'rubric-fails-hit',
             'Auto-fails tripped — not scored, whatever the grid totalled')}
      ${list(ev.fraud_tells, 'tell', 'rubric-fraud',
             'Fraud tells — route to the fraud log, not to a score')}
      ${disputedRow}
      ${cvRow}
    </div>`;
}

/* The GIA read: a note for the interviewer, worth nothing on the scoreboard. */
function giaRead(ev) {
  const g = ev?.gia;
  if (!g || (!g.read && !Object.keys(g.scales || {}).length)) return '';
  return `
    <div class="drawer-section">
      <h3>GIA proxy read <span class="dim">· changes no points</span></h3>
      ${g.primary?.length ? `<div class="gia-scales">${
        g.primary.map((s) => `<span class="scale primary">${esc(s)}</span>`).join('')}${
        (g.secondary || []).map((s) => `<span class="scale">${esc(s)}</span>`).join('')}
      </div>` : ''}
      ${g.read ? `<p class="verdict-brief">${esc(g.read)}</p>` : ''}
      ${Object.keys(g.scales || {}).length ? `<dl class="meta-grid">${
        Object.entries(g.scales).map(([k, v]) =>
          `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    </div>`;
}

/* Grade this one candidate, now, regardless of which queue they are in.
 *
 * The role-level "Grade pending" button only reaches the pending queue, so the
 * candidate a reviewer is actually looking at is often the one it will never
 * touch: auto-rejected for a missing artefact, or already scored and worth a
 * re-run after a rubric edit. This is the on-demand door for exactly that.
 *
 * Hidden only when there is genuinely nothing to mark -- no answer text -- or
 * when no evaluator is configured to mark it with. */
function evaluateButton(c) {
  if (!state.evaluatorConfigured) return '';
  if (!(c.submission_markdown || '').trim()) return '';
  const label = c.evaluation ? 'Re-evaluate' : 'Evaluate now';
  return `<button class="btn btn-primary" data-evaluate>${label}</button>`;
}

/* Moving one candidate along the pipeline, from the card you are already
 * reading. This is where a stage is set in practice: the decision is made while
 * looking at the score and the grid that produced it, not from a board.
 *
 * One form serves all three moves rather than three dialogs. The fields are
 * only meaningful to some of them -- a hire does not need an interview time --
 * but sending them together means marking an interviewed candidate hired keeps
 * the date they were seen on, which is the fact you want back later when asking
 * how long the process takes. */
/* Which manager owns this interview, mirroring candidate_mail.resolve_manager()
 * so the field is pre-filled with the same person the send would pick.
 *
 * The server decides for real -- this is only what the box shows before anyone
 * clicks. Where it cannot tell (three managers, an interviewer name matching
 * none of them) it picks nobody and the manager says who they are, which is
 * the honest answer: putting a stranger's calendar in front of a candidate is
 * a mistake they discover in the meeting. */
function managerFor(c, interviewer) {
  const managers = (c.managers || []).filter((m) => m.email);
  if (!managers.length) return null;
  const hint = String(interviewer || c.pipeline?.interviewer || '').trim().toLowerCase();
  if (hint) {
    const exact = managers.find((m) => [m.email, m.name]
      .map((v) => String(v || '').toLowerCase()).includes(hint));
    if (exact) return exact;
    const first = managers.filter((m) => String(m.name || '').toLowerCase()
      .split(' ')[0] === hint.split(' ')[0]);
    if (first.length === 1) return first[0];
  }
  return managers.length === 1 ? managers[0] : null;
}

/* What the candidate has already been told, and when. A rejection sent twice
 * is worse than one sent late, and this is the line that stops it. */
function mailHistory(p) {
  const sends = (p.emails || []).slice().reverse();
  if (!sends.length) return '';
  return `
      <ul class="stage-mails">${sends.map((m) => `
        <li class="${m.ok ? '' : 'is-failed'}">
          <span class="badge ${m.ok ? (STAGE_CLASS[m.stage] || 'badge-queue') : 'badge-stage-rejected'}">${
            m.ok ? esc(STAGE_LABEL[m.stage] || m.stage) : 'Send failed'} email</span>
          <span class="dim">${esc(shortDate(m.at))} · ${esc(m.to || '')}</span>
          ${m.error ? `<span class="warn">${esc(m.error)}</span>` : ''}
        </li>`).join('')}
      </ul>`;
}

function pipelineSection(c) {
  const p = c.pipeline || {};
  const stage = stageOf(c);
  const history = (p.history || []).slice().reverse();
  const managers = (c.managers || []).filter((m) => m.email);
  const owner = managerFor(c);

  const label = stage
    ? `<span class="badge ${STAGE_CLASS[stage]}">${esc(STAGE_LABEL[stage])}</span>`
    : '<span class="dim">Not in the pipeline</span>';

  const note = stage === 'rejected' ? (p.reason || p.note || '') : (p.note || '');

  // The manager picker is only shown where there is a choice to make. One
  // manager on the role is not a decision, and a select with a single option
  // is a click that teaches the reader nothing. It is here for the signature
  // on a rejection now, not for a calendar.
  const managerField = managers.length > 1 ? `
        <label>Signed by
          <select id="stageManager">
            ${managers.map((m) => `<option value="${esc(m.email)}"${
              owner && owner.email === m.email ? ' selected' : ''}>${
              esc(m.name)}</option>`).join('')}
          </select>
        </label>` : '';

  // What the manager did, read-only. The interview belongs to them, so this
  // page reports it rather than offering to change it -- and it separates
  // "they were invited" from "the invitation was actually sent", because a
  // candidate can sit at this stage with an empty inbox after a failed send.
  const invite = (p.emails || []).filter((m) => m.stage === 'interview' && m.ok).pop();
  const inviteBlock = stage === 'interview' ? `
      <div class="stage-readout">
        <div><span class="dim">Invited by</span> ${esc(p.by || p.interviewer || 'a hiring manager')}</div>
        ${p.interview_at
          ? `<div><span class="dim">Suggested time</span> ${esc(fmtWhen(p.interview_at))}</div>` : ''}
        <div><span class="dim">Invitation</span> ${invite
          ? `sent ${esc(shortDate(invite.at))} to ${esc(invite.to || '')}`
          : '<b>not sent</b> — the candidate has no booking link yet'}</div>
      </div>` : '';

  return `
    <div class="drawer-section">
      <h3>Hiring pipeline ${label}</h3>
      <p class="matrix-note">
        Where this candidate is after the assessment. Nothing here re-marks the
        submission — the score, the grid and the status above are left exactly
        as they are, so a hire or a rejection can always be read back against
        what the assessment predicted.
      </p>
      ${inviteBlock}
      <div class="stage-form">
        ${managerField}
        <label class="wide">${stage === 'rejected' ? 'Reason' : 'Note'}
          <span class="field-hint">Internal — never leaves the dashboard</span>
          <input type="text" id="stageNote" value="${esc(note)}"
                 placeholder="Anything the next reader needs">
        </label>
        <label class="wide">Message to the candidate
          <span class="field-hint">Optional — appears in the email they receive</span>
          <input type="text" id="stageEmailNote"
                 placeholder="Really enjoyed your take on the funnel question.">
        </label>
      </div>

      <!-- Moving and telling are two clicks, not one.
           The buttons on the first row change the board and send nothing. The
           second row is the only thing on this page that puts a message in a
           candidate's inbox, and it sits next to Preview so the order of work
           is the obvious one: move, read the message, send it.

           No interview in either row. The server refuses it from here, and a
           button that 403s is worse than no button -- see the note below,
           which says where the invitation is actually written. -->
      <div class="drawer-actions">
        <button class="btn btn-primary" data-stage="hired">Mark hired</button>
        <button class="btn" data-stage="rejected">Mark rejected</button>
        ${stage ? '<button class="btn btn-ghost" data-stage="">Remove from pipeline</button>' : ''}
      </div>

      ${state.mail.interview_locked ? `
        <p class="field-hint stage-locked">
          ${esc(state.mail.interview_locked_reason
                || 'Interviews are invited by the hiring manager, from the review link in their shortlist email.')}
        </p>` : ''}

      <div class="stage-send">
        <p class="field-hint">
          ${state.mail.enabled === false
            ? 'Candidate emails are switched off (PIPELINE_EMAILS_ENABLED=0), so this sends nothing.'
            : state.mail.auto
              ? 'Marking someone rejected already emails them. This is here to read the message, or to send it again after a change.'
              : 'Nothing above emails anyone — the move is recorded and stops there. Preview the message, then send it.'}
          Nothing is ever sent for a hire or a removal.
        </p>
        <div class="drawer-actions">
          <button class="btn btn-ghost" data-mailpreview="rejected">Preview rejection</button>
          <button class="btn" data-mailsend="rejected">Send rejection</button>
        </div>
      </div>
      ${mailHistory(p)}
      ${history.length ? `
        <ul class="stage-history">${history.map((h) => `
          <li>
            <span class="badge ${STAGE_CLASS[h.stage] || 'badge-queue'}">${
              esc(STAGE_LABEL[h.stage] || 'Returned to shortlist')}</span>
            <span class="dim">${esc(shortDate(h.at))}${
              h.interview_at ? ` · interview ${esc(fmtWhen(h.interview_at))}` : ''}${
              h.interviewer ? ` · ${esc(h.interviewer)}` : ''}</span>
            ${h.reason || h.note ? `<span>${esc(h.reason || h.note)}</span>` : ''}
          </li>`).join('')}
        </ul>` : ''}
    </div>`;
}

function drawerContent(c) {
  const ev = c.evaluation;
  const status = c.decision?.status || 'unknown';
  const reason = REASON_LABEL[c.decision?.reason] || c.decision?.reason || '—';
  const link = (url, label) => url
    ? `<a class="link" href="${esc(url)}" target="_blank" rel="noopener">${label}</a>`
    : '<span class="dim">Not submitted</span>';

  const verdict = ev ? `
    <div class="verdict-card${ev.auto_failed ? ' is-autofail' : ''}">
      <div class="verdict-score ${scoreClass(ev.score)}">${fmtScore(ev.score)}${
        provisionalMark(ev)}</div>
      <div>
        <span class="badge ${recClass(recLabel(ev))}">${esc(recLabel(ev))}</span>
        ${ev.triage ? `<span class="badge badge-route">triage ${
          ev.triage.passed}/${ev.triage.of}</span>` : ''}
        ${ev.grid_source === 'derived'
          ? '<span class="badge badge-derived">derived grid</span>' : ''}
        ${withoutMark(ev)}
        <p class="verdict-brief">${esc(ev.brief)}</p>
      </div>
    </div>` : `
    <div class="verdict-card">
      <div class="verdict-score score-none">—</div>
      <div><span class="badge ${STATUS_CLASS[status] || ''}">${
        esc(STATUS_LABEL[status] || status)}</span>
        <p class="verdict-brief">${esc(reason)}</p></div>
    </div>`;

  return `
    ${verdict}
    ${pipelineSection(c)}
    ${gridTable(ev)}
    ${cvTable(ev)}
    ${findingsBlock(ev)}
    ${giaRead(ev)}
    <div class="drawer-section">
      <h3>Details</h3>
      <dl class="meta-grid">
        <dt>Status</dt><dd>${esc(STATUS_LABEL[status] || status)} — ${esc(reason)}</dd>
        ${portalQueue(c) ? `<dt>Portal queue</dt><dd>${esc(portalQueue(c))}${
          c.screener_rating ? ` — rated ${esc(c.screener_rating)}` : ''}</dd>` : ''}
        <dt>Video</dt><dd>${link(c.video_link, 'Open video')}</dd>
        <dt>Resume</dt><dd>${link(c.resume_link, 'Open resume')}</dd>
        <dt>Submitted</dt><dd>${esc(c.submitted_at || '—')}</dd>
        <dt>Assignment</dt><dd>${esc(c.assignment_name || '—')}</dd>
        ${ev ? `<dt>Marked against</dt><dd>${
          ev.grid_unit ? `${esc(ev.grid_unit)} grid` : 'legacy matrix'}${
          // Only the AI Strategist family has more than one tier, so this is
          // absent everywhere else. Where it is present it is the single most
          // important thing in this list: the background row is worth 40 of
          // the 100 and its anchors are the whole difference between the two.
          ev.grid_tier ? ` · <b>${esc(ev.grid_tier)} tier</b>` : ''}${
          ev.grid_version ? ` · <code>${esc(ev.grid_version)}</code>` : ''}${
          ev.pack_version ? ` · pack ${esc(ev.pack_version)}` : ''}${
          ev.grid_source ? ` · ${esc(ev.grid_source)}` : ''}</dd>
        <dt>Model</dt><dd>${esc(ev.model)}${
          ev.answer_truncated ? ' · answer truncated' : ''}</dd>` : ''}
        <dt>On portal</dt><dd>${link(c.admin_url, 'Open in portal')}</dd>
      </dl>
      <div class="drawer-actions">
        ${evaluateButton(c)}
        ${status === 'rejected'
          ? '<button class="btn" data-reconsider>Move to pending</button>'
          : '<button class="btn" data-reject>Move to rejected</button>'}
      </div>
    </div>
    <div class="drawer-section">
      <h3>Submission</h3>
      <div class="answer">${esc(c.submission_markdown || 'No answer text.')}</div>
    </div>`;
}

/* One model call, one candidate. Slow enough (10-30s) that the button has to
 * say so, and the drawer is reopened afterwards rather than patched, so the
 * grid, triage and fraud findings all arrive together from the server. */
async function evaluateOne(submissionId, btn) {
  const previous = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Evaluating…';
  try {
    const result = await api('/api/evaluations/grade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId }),
    });
    toast(result.message);
    await openDrawer(submissionId);       // the new score, in place
    if (state.activeRoleId) await openRole(state.activeRoleId, false);
    await loadRoles();                    // role tallies moved
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
    btn.textContent = previous;
  }
}

async function setDecision(submissionId, status) {
  try {
    const result = await api('/api/evaluations/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId, status }),
    });
    toast(result.message);
    closeDrawer();
    await openRole(state.activeRoleId);
    await loadRoles();
  } catch (err) {
    toast(err.message, true);
  }
}

function closeDrawer() { $('drawer').hidden = true; }

/* --- portal sync ------------------------------------------------------ */

async function syncPortal() {
  const btn = $('syncBtn');
  btn.disabled = true;
  const previous = btn.textContent;
  btn.textContent = 'Syncing…';
  try {
    const result = await api('/api/evaluations/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ include_roles: false }),
    });
    toast(result.message);
    await loadRoles();
    if (state.activeRoleId) await openRole(state.activeRoleId);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = previous;
  }
}

/* --- wiring ----------------------------------------------------------- */

$('refreshBtn').addEventListener('click', loadRoles);
$('syncBtn').addEventListener('click', syncPortal);
$('rejectedRole').addEventListener('change', loadRejected);
$('copyEmailsBtn').addEventListener('click',
  () => copyEmails(', ', 'Copied comma-separated'));
$('copyBccBtn').addEventListener('click',
  () => copyEmails('; ', 'Copied semicolon-separated for BCC'));
$('exportCsvBtn').addEventListener('click', exportCsv);

function toggleRejectedList(show) {
  const wrap = $('rejectedWrap');
  wrap.hidden = show === undefined ? !wrap.hidden : !show;
  $('rejectedToggle').textContent = wrap.hidden ? 'Show list' : 'Hide list';
}

$('rejectedToggle').addEventListener('click', () => toggleRejectedList());

// A tick-all per column. Each governs its own list and nothing else -- which
// is the point of there being two of them.
$('waitingAll').addEventListener('change', (e) => tickAll(
  'rejectedBody', waitingPicked, waitingRows(), e.target.checked));
$('mailedAll').addEventListener('change', (e) => tickAll(
  'mailedBody', mailedPicked, mailedRows(), e.target.checked));
$('rejectedRefresh').addEventListener('click', loadRejected);
$('markMailedBtn').addEventListener('click', markAsEmailed);
$('unmarkBtn').addEventListener('click', moveBackToWaiting);
$('mailedCsvBtn').addEventListener('click', exportMailedCsv);
for (const tab of $('pipelineTabs').querySelectorAll('.tab')) {
  tab.addEventListener('click', () => selectStage(tab.dataset.stage));
}
// --- the top N, and the invitation ---
$('topSearch').addEventListener('input', renderTop);
$('topRefresh').addEventListener('click', () => loadTopCandidates(true));
// Changing the size re-asks the server rather than slicing what is on screen:
// going from 10 to 20 needs ten rows the client never had, and the workspace
// behind the Send button has to be re-pointed at them anyway.
$('topLimit').addEventListener('change', () => {
  state.top.limit = Number($('topLimit').value) || 20;
  loadTopCandidates(true);
});
$('topAll').addEventListener('change', () => {
  // Acts on what the search is showing, not on the whole list: "select all"
  // under a filter means the ones you can see.
  const term = $('topSearch').value.trim().toLowerCase();
  const rows = state.top.rows.filter((c) => invitable(c)
    && (!term || `${c.name || ''} ${c.email || ''}`.toLowerCase().includes(term)));
  const every = rows.every((c) => state.top.picked.has(c.submission_id));
  for (const c of rows) {
    if (every) state.top.picked.delete(c.submission_id);
    else state.top.picked.add(c.submission_id);
  }
  renderTop();
});
$('topInviteBtn').addEventListener('click',
  () => InviteComposer.open([...state.top.picked]));

$('pipelineRole').addEventListener('change', loadPipeline);
$('pipelineRefresh').addEventListener('click', loadPipeline);
$('pipelineCopyBtn').addEventListener('click', copyPipelineEmails);
$('pipelineCsvBtn').addEventListener('click', exportPipelineCsv);

// --- hiring managers and the hand-off ---
$('mgrForm').addEventListener('submit', addManager);
$('mgrSave').addEventListener('click', saveManagers);
$('shortlistXlsxBtn').addEventListener('click', downloadShortlistXlsx);
$('shortlistPreviewBtn').addEventListener('click', previewShortlistMail);
$('shortlistSendBtn').addEventListener('click', sendShortlist);
// Changing the size re-asks the server rather than slicing what is on screen:
// going from 10 to 20 needs ten rows the client never had.
$('shortlistLimit').addEventListener('change', () => {
  $('shortlistLimit').dataset.touched = '1';
  if (state.activeRoleId !== null) loadShortlist(state.activeRoleId);
});
// Re-asks the server for the same reason the size box does: the score is not
// on the rows the page is holding unless it asked for it, so it cannot be
// drawn by re-rendering what is already here.
$('shortlistScores').addEventListener('change', () => {
  $('shortlistScores').dataset.touched = '1';
  if (state.activeRoleId !== null) loadShortlist(state.activeRoleId);
});
for (const el of document.querySelectorAll('[data-mail-close]')) {
  el.addEventListener('click', closeMailPreview);
}

$('gradeBtn').addEventListener('click', gradePending);
$('inviteBtn')?.addEventListener('click', openInvite);
$('matrixToggle').addEventListener('change', (e) => {
  state.showMatrix = e.target.checked;
  renderCandidates();
});
// --- accounts ---
// Optional-chained like the other late additions on this page: a browser
// holding cached HTML against fresh JS should lose the button, not the page.
$('accountsBtn')?.addEventListener('click', openAccounts);
for (const el of document.querySelectorAll('[data-accounts-close]')) {
  el.addEventListener('click', closeAccounts);
}
$('newUserForm').addEventListener('submit', createUser);
$('newUserToggle')?.addEventListener('click', () => toggleNewUserForm());
$('userSearch')?.addEventListener('input', renderUsers);
$('accountsRefresh').addEventListener('click', loadUsers);
$('roleSearch').addEventListener('input', renderRoles);
$('roleFilter').addEventListener('change', renderRoles);
$('candSearch').addEventListener('input', renderCandidates);
$('statusFilter').addEventListener('change', renderCandidates);
$('topN').addEventListener('change', () => {
  // Remembered, so it survives clicking into a candidate and back out, and
  // clicking through to the next role. A manager works one shortlist at a
  // time and should not have to re-pick it per role.
  state.topN = Number($('topN').value) || 0;
  renderCandidates();
});

// --- moving between the two views ---
$('backBtn').addEventListener('click', backToRoles);
$('heroCalBtn')?.addEventListener('click', editHeroCal);
for (const tab of $('roleTabs').querySelectorAll('.viewtab')) {
  tab.addEventListener('click', () => setRoleTab(tab.dataset.tab));
}
// Back out of a role with Escape, the same key that shuts the drawer over it.
// The drawer wins while it is up: one Escape should close one thing.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('mailPreview').hidden || !$('drawer').hidden
      || $('accountsDrawer')?.hidden === false
      || InviteComposer.isOpen()) return;
  if (state.activeRoleId !== null) backToRoles();
});

// The candidate header is rebuilt whenever the category columns are toggled,
// so its sort handlers are attached in renderCandHead() rather than here.

for (const el of document.querySelectorAll('[data-close]')) {
  el.addEventListener('click', closeDrawer);
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('mailPreview').hidden) closeMailPreview();
  else if (InviteComposer.isOpen()) InviteComposer.close();
  else if (!$('drawer').hidden) closeDrawer();
  // Last: it is the only one of the four that can have another open over it,
  // and an open role picker has already swallowed the key. See rolePickerKey().
  // Optional-chained because it is the newest element on the page and so the
  // one a browser on cached HTML is likeliest to be missing -- and a throw here
  // would take Escape away from the other three as well.
  else if ($('accountsDrawer')?.hidden === false) closeAccounts();
});

/* The composer, told how this page talks and what to do afterwards.
 *
 * Its endpoints are the token'd ones even here: a signed-in manager asks
 * api_my_review_link for their own workspace and then uses it exactly as a
 * mailed manager would, so there is one invite surface on the server as well
 * as one in the browser. state.top.token is that workspace. */
InviteComposer.init({
  post: (path, payload) => postJson(path, payload),
  previewPath: () => reviewApi('/invite/preview'),
  sendPath: () => reviewApi('/invite'),
  toast,
  placeholders: () => state.top.placeholders || [],
  selectable: (id) => !!state.top.token && state.top.rows
    .some((c) => c.submission_id === id && invitable(c)),
  /* How this page gets a live workspace when the one in hand has died.
   *
   * The token is minted when the panel loads and then held for the visit, so
   * it outlives things that end it: the recruiter revoking it from the links
   * panel, a restart against a different database, its expiry passing on a
   * tab left open over a weekend. The composer used to answer all of those
   * with a dialog that filled in with nothing.
   *
   * A re-mint, not a second link -- /api/managers/review-link re-points this
   * manager's one live credential at today's list, and force re-reads the rows
   * under it so the ids the composer is about to send are ones the new token
   * actually covers. Only the composer asks for this: a manager sitting on the
   * panel doing nothing should not be minting credentials in the background.
   *
   * loadTopCandidates() reports its own failures and returns, so the absence
   * of a token afterwards is the only signal that it did not work. Thrown
   * rather than returned quietly: the composer retries whatever comes back,
   * and retrying with no token asks /api/review//invite/preview, which is a
   * bare 404 in place of the sentence the manager was about to be shown. */
  renew: async () => {
    await loadTopCandidates(true);
    if (!state.top.token) throw new Error('No workspace to invite from.');
  },
  onSent: async (data) => {
    // Cleared only for the ones that actually went. Anyone left behind stays
    // ticked, so a second click retries exactly them rather than the whole
    // batch again.
    for (const row of data.results || []) {
      if (row.sent) state.top.picked.delete(row.submission_id);
    }
    // Everything that could be showing these people is re-read from the server
    // rather than patched: they have left this list and joined the board below
    // it, and two panels on one screen must not disagree about where somebody
    // is.
    await loadTopCandidates(true);
    await loadPipeline();
    if (state.activeRoleId !== null) await openRole(state.activeRoleId, false);
    await loadRoles();
  },
});

loadRoles();
