/*
 * The page's state object, and the primitives every other file here uses:
 * element lookup, HTML escaping, the toast, and the fetch wrapper that every
 * API call goes through.
 *
 * Loaded first because everything below refers to `state` and `$`.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

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
