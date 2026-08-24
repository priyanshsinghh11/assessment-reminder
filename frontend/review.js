/*
 * The hiring manager's review page.
 *
 * Reached with a token and nothing else. Everything on screen comes from
 * /api/review/<token>, which decides what this link may see -- the page never
 * asks for a role, a candidate or a manager by id, because it has no way to
 * know one it was not given.
 *
 * NO SCORES REACH THIS FILE. The server projects `evaluation` out at the
 * database, so there is nothing here to accidentally render.
 *
 * Two different shapes of decision live here, and they are deliberately not
 * the same control. Hire and rejection are one click and a note, in the
 * confirm dialog. An interview is a message -- a subject, a body, a calendar
 * and a candidate reading it -- so it goes through the composer, which shows
 * the manager the rendered mail before it exists in anybody's inbox. This page
 * is the ONLY way a candidate enters the interview stage; the recruiting
 * dashboard's own routes refuse it.
 */

// The token is the last path segment of /review/<token>. Read once and never
// put in a query string, a link or a log line.
const TOKEN = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');

const state = {
  role: null,
  manager: null,
  candidates: [],
  mailedStages: [],
  // The master switch: whether anything at all reaches a candidate.
  emailsEnabled: true,
  // Whether HIRED and REJECTED mail on the click. The invitation does not ask
  // this -- it leaves from a composer the manager just read, which is the
  // human check the switch exists to force.
  autoEmail: false,
  canBook: false,
  placeholders: [],

  // --- the list ---
  hideDecided: false,
  search: '',
  topN: 0,
  picked: new Set(),

  // What the confirm dialog is about to do, set when it opens.
  pending: null,

};

const $ = (id) => document.getElementById(id);

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('is-error', isError);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, isError ? 9000 : 5000);
}

async function api(path, options) {
  const resp = await fetch(path, options);
  let body = {};
  try { body = await resp.json(); } catch { /* not JSON */ }
  if (!resp.ok) {
    const err = new Error(body.error || `HTTP ${resp.status}`);
    err.body = body;
    err.status = resp.status;
    throw err;
  }
  return body;
}

const post = (path, payload) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

const url = (suffix) => `/api/review/${encodeURIComponent(TOKEN)}${suffix}`;

const STAGE_LABEL = {
  interview: 'Interview',
  hired: 'Hired',
  rejected: 'Not proceeding',
};

// What each button does, in the manager's words rather than the schema's.
const STAGE_VERB = { hired: 'Mark hired', rejected: 'Not proceeding' };

// Who can still be picked for an interview. Somebody already at interview can
// -- that is a reschedule, or a resend to a candidate who says nothing
// arrived. Somebody hired or turned down cannot: inviting a candidate who has
// been rejected is the one mis-click on this page that cannot be walked back.
const selectable = (c) => c.stage !== 'hired' && c.stage !== 'rejected';

function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric' });
}

const firstName = (name) => String(name || '').trim().split(/\s+/)[0] || 'there';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/* --- load -------------------------------------------------------------- */

async function load() {
  try {
    const data = await api(url(''));
    state.role = data.role;
    state.manager = data.manager;
    state.candidates = data.candidates;
    state.mailedStages = data.mailed_stages || [];
    state.emailsEnabled = data.emails_enabled !== false;
    state.autoEmail = !!data.auto_email;
    state.canBook = !!data.can_book;
    state.placeholders = data.placeholders || [];

    document.title = `${data.role.title || 'Candidate review'} — Ajaia`;
    $('roleTitle').textContent = data.role.title || 'Candidate review';
    $('meta').textContent =
      plural(data.candidates.length, 'candidate')
      + (data.expires_at ? ` · this link works until ${shortDate(data.expires_at)}` : '');

    $('intro').hidden = false;
    $('listPanel').hidden = false;
    $('introLead').innerHTML =
      `Hi <strong>${esc(firstName(data.manager.name))}</strong> — these are the `
      + `candidates we would like you to look at for `
      + `<strong>${esc(data.role.title || 'this role')}</strong>, strongest first.`;

    // Said before the first click, not after it fails. A manager with no
    // booking link cannot invite anyone, and finding that out from a red
    // error halfway down the list is the worst time to find out.
    // Rank on this page is the whole recommendation -- there are no scores to
    // read it against -- so a rank built from a rubric our grader never
    // finished has to say so before the manager starts down the list, not only
    // on the row they may filter away.
    const partial = data.candidates.filter((c) => c.grading_incomplete);
    const partialNote = $('introPartial');
    if (partialNote) {
      partialNote.hidden = partial.length === 0;
      partialNote.textContent = partial.length
        ? `Our AI grader did not finish the scorecard for `
          + `${plural(partial.length, 'candidate')} on this list `
          + `(${partial.map((c) => c.name).join(', ')}), so their `
          + `${partial.length === 1 ? 'position is' : 'positions are'} not a `
          + `like-for-like comparison with the rest. Read their answers before `
          + `you decide, and tell us so we can re-grade them.`
        : '';
    }

    const warn = $('introWarn');
    if (!state.emailsEnabled) {
      warn.textContent = 'Candidate emails are switched off right now, so your '
        + 'decisions will be recorded but nobody will be emailed.';
      warn.hidden = false;
    } else if (!state.canBook) {
      warn.textContent = 'We do not have your booking link yet, so interview '
        + 'invitations cannot go out. Reply to the email that brought you here '
        + 'with your cal.com link and we will add it.';
      warn.hidden = false;
    }

    render();
  } catch (err) {
    showDead(err);
  }
}

const DEAD_TITLE = {
  expired: 'This link has expired',
  revoked: 'This link has been withdrawn',
  unknown: 'We could not open this link',
};

function showDead(err) {
  const kind = err.body?.state || 'unknown';
  $('roleTitle').textContent = 'Candidate review';
  $('meta').textContent = '';
  $('deadTitle').textContent = DEAD_TITLE[kind] || DEAD_TITLE.unknown;
  $('deadBody').textContent = err.message;
  $('dead').hidden = false;
  $('intro').hidden = true;
  $('listPanel').hidden = true;
}

/* --- filtering ---------------------------------------------------------
 *
 * Three narrowings that compose, because they answer different questions:
 * "show me the strongest few" (rank), "where is that person called Priya"
 * (search), and "what have I not dealt with yet" (decided).
 *
 * Rank is the filter that matters. A manager with twenty names and four
 * interview slots is going to work top-down whatever the page does, and Top 5
 * makes that one click instead of twenty judgements. It cuts on `rank`, which
 * is the position in the list they were sent -- not on anything recomputed
 * here, so the fifth row on screen is the fifth row of their email. */

function visible() {
  const query = state.search.trim().toLowerCase();
  return state.candidates.filter((c) => {
    if (state.hideDecided && c.stage) return false;
    if (state.topN && c.rank > state.topN) return false;
    if (query && !`${c.name} ${c.email}`.toLowerCase().includes(query)) return false;
    return true;
  });
}

// Selections survive filtering rather than being cleared by it: picking three
// people from Top 5 and then searching for a fourth is one task, and a page
// that quietly dropped the first three would be lying about what Send does.
// The count line names the total so that stays visible while a filter hides
// some of them.
const pickable = () => visible().filter(selectable);

/* --- the list ---------------------------------------------------------- */

function render() {
  const rows = visible();
  $('listEmpty').hidden = rows.length > 0;
  $('listEmpty').textContent = state.candidates.length
    ? 'Nobody matches that filter.'
    : 'There is nothing on this list.';

  const link = (href, label) => (href
    ? `<a class="art" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`
    : `<span class="art is-missing" title="Not provided">${label}</span>`);

  $('candList').innerHTML = rows.map((c) => {
    const decided = !!c.stage;
    const canPick = selectable(c);
    const buttons = ['hired', 'rejected'].map((stage) => `
      <button class="btn stage-btn stage-${stage}${c.stage === stage ? ' is-current' : ''}"
              type="button" data-act="${stage}" data-id="${c.submission_id}"
              ${c.stage === stage ? 'disabled' : ''}>
        ${esc(STAGE_VERB[stage])}
      </button>`).join('');

    // "Marked for interview" and "invitation sent" are different facts and are
    // shown as different lines. A candidate can sit at interview with an empty
    // inbox -- the send failed, or mail is off -- and a manager who cannot see
    // that waits a week for a booking that was never asked for.
    const state_row = decided ? `
        <div class="cand-state">
          <span class="badge badge-${c.stage}">${esc(STAGE_LABEL[c.stage] || c.stage)}</span>
          <span class="dim">${esc(shortDate(c.stage_at))}</span>
          ${c.stage === 'interview'
            ? (c.invited_at
              ? `<span class="dim">invitation sent ${esc(shortDate(c.invited_at))}</span>`
              : '<span class="warn-inline">not emailed yet</span>')
            : ''}
          ${c.note ? `<span class="cand-note">“${esc(c.note)}”</span>` : ''}
        </div>` : '';

    return `
      <li class="cand${decided ? ' is-decided' : ''}${state.picked.has(c.submission_id) ? ' is-picked' : ''}"
          data-id="${c.submission_id}">
        <label class="cand-pick">
          <input type="checkbox" data-pick="${c.submission_id}"
                 ${state.picked.has(c.submission_id) ? 'checked' : ''}
                 ${canPick ? '' : 'disabled'}
                 aria-label="Pick ${esc(c.name)} for interview"
                 title="${canPick ? `Pick ${esc(c.name)} for an interview`
                                  : 'Already decided — nothing left to invite them to'}">
        </label>
        <div class="cand-rank">${c.rank}</div>
        <div class="cand-main">
          <div class="cand-name">${esc(c.name)}${
            c.grading_incomplete
              ? ' <span class="badge badge-partial" title="Our AI grader did'
                + ' not finish this candidate’s rubric, so their position'
                + ' on this list is not a like-for-like comparison. Read the'
                + ' answers before you decide, and tell us so we can re-grade'
                + ' them.">partly graded</span>'
              : ''}</div>
          <div class="cand-sub">
            <span class="cand-email">${esc(c.email)}</span>
            ${c.submitted_at ? `<span class="dim">submitted ${esc(c.submitted_at)}</span>` : ''}
          </div>
          <div class="cand-arts">
            ${link(c.resume_link, 'CV')}
            ${link(c.assessment_url, 'Answers')}
            ${link(c.video_link, 'Video')}
          </div>
          ${state_row}
        </div>
        <div class="cand-actions">${buttons}</div>
      </li>`;
  }).join('');

  for (const btn of $('candList').querySelectorAll('[data-act]')) {
    btn.addEventListener('click',
      () => openConfirm(Number(btn.dataset.id), btn.dataset.act));
  }
  for (const box of $('candList').querySelectorAll('[data-pick]')) {
    box.addEventListener('change', () => {
      const id = Number(box.dataset.pick);
      if (box.checked) state.picked.add(id); else state.picked.delete(id);
      render();
    });
  }

  renderPickBar();
}

function renderPickBar() {
  const rows = pickable();
  const shown = rows.filter((c) => state.picked.has(c.submission_id)).length;
  const total = state.picked.size;

  const all = $('pickAll');
  all.checked = rows.length > 0 && shown === rows.length;
  all.indeterminate = shown > 0 && shown < rows.length;
  all.disabled = rows.length === 0;
  $('pickAllLabel').textContent = all.checked ? 'Clear selection' : 'Select all';

  // The total is what Send acts on, so it is what the line says. The "of which
  // N on screen" half only appears when a filter is actually hiding some of
  // the selection, because that is the only time the two numbers differ and
  // the only time the difference could surprise anyone.
  $('pickCount').textContent = total === 0
    ? 'Nobody picked yet'
    : `${plural(total, 'candidate')} picked`
      + (shown === total ? '' : ` · ${shown} of them on screen`);

  const btn = $('inviteBtn');
  btn.disabled = total === 0 || !state.canBook || !state.emailsEnabled;
  btn.textContent = total > 1 ? `Invite ${total} to interview` : 'Invite to interview';
  btn.title = !state.emailsEnabled ? 'Candidate emails are switched off.'
    : !state.canBook ? 'We do not have your booking link yet.'
    : total === 0 ? 'Tick the people you want to meet.'
    : 'Write their invitation and send it';
}

/* --- confirm: hire and rejection --------------------------------------- */

// What the manager is told the click will do. The email consequence is stated
// in the dialog rather than the toast: after the send is too late to change
// your mind.
const CONFIRM = {
  hired: {
    title: 'Mark hired',
    body: (n) => `${n} will be moved to Hired on the board. No email is sent — `
                 + `the offer is yours to make.`,
    noteLabel: 'Note for the record',
    noteHint: 'Internal only. The candidate never sees this.',
  },
  rejected: {
    title: 'Not proceeding',
    body: (n) => `${n} will be told we are not taking their application `
                 + `further. This cannot be undone.`,
    noteLabel: 'Feedback for the candidate',
    noteHint: 'Included in the email, if you write one. Left out entirely if '
              + 'you do not — better nothing than a generic line.',
  },
};

function openConfirm(submissionId, stage) {
  const candidate = state.candidates.find((c) => c.submission_id === submissionId);
  if (!candidate) return;

  const copy = CONFIRM[stage];
  state.pending = { submissionId, stage };

  $('confirmTitle').textContent = copy.title;
  // A rejection only reaches the candidate when both switches allow it. The
  // dialog says which of the two worlds this click is in rather than promising
  // an email the server is not going to send.
  const mails = stage === 'rejected' && state.emailsEnabled;
  $('confirmBody').textContent = copy.body(candidate.name)
    + (mails && !state.autoEmail
      ? ' We will send their email once the recruiting team has read it.'
      : '');
  $('confirmBody').classList.remove('warn');
  $('noteLabel').innerHTML = `${esc(copy.noteLabel)} <span class="opt">optional</span>`;
  $('noteHint').textContent = copy.noteHint;
  $('confirmNote').value = '';
  $('confirmGo').textContent = copy.title;
  $('confirmGo').className = `btn btn-primary stage-${stage}`;
  $('confirm').hidden = false;
  $('confirmNote').focus();
}

function closeConfirm() {
  $('confirm').hidden = true;
  state.pending = null;
}

async function submitDecision() {
  if (!state.pending) return;
  const { submissionId, stage } = state.pending;
  const btn = $('confirmGo');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Saving…';

  try {
    const data = await post(url('/decision'), {
      submission_id: submissionId,
      stage,
      note: $('confirmNote').value,
    });

    // Updated in place rather than re-fetched: the list is the manager's place
    // in a task, and rebuilding it from the server would lose their scroll
    // position halfway through twenty people.
    patch(submissionId, {
      stage,
      stage_at: new Date().toISOString(),
      note: $('confirmNote').value.trim() || null,
    });
    // Somebody hired or turned down cannot be invited, so they leave any
    // selection they were part of rather than sitting in it invisibly.
    state.picked.delete(submissionId);
    closeConfirm();
    render();
    toast(data.message, data.mail && data.mail.sent === false
                        && state.mailedStages.includes(stage));
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function patch(submissionId, fields) {
  const candidate = state.candidates.find((c) => c.submission_id === submissionId);
  if (candidate) Object.assign(candidate, fields);
}

/* --- wiring ------------------------------------------------------------ */

$('confirmGo').addEventListener('click', submitDecision);
for (const el of document.querySelectorAll('[data-cancel]')) {
  el.addEventListener('click', closeConfirm);
}

// One Escape closes one thing, innermost first.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('confirm').hidden) closeConfirm();
  else if (!$('composer').hidden) InviteComposer.close();
});

$('hideDecided').addEventListener('change', (e) => {
  state.hideDecided = e.target.checked;
  render();
});
$('search').addEventListener('input', (e) => {
  state.search = e.target.value;
  render();
});
$('topN').addEventListener('change', (e) => {
  state.topN = Number(e.target.value) || 0;
  render();
});
$('pickAll').addEventListener('change', () => {
  const rows = pickable();
  const every = rows.every((c) => state.picked.has(c.submission_id));
  for (const c of rows) {
    if (every) state.picked.delete(c.submission_id);
    else state.picked.add(c.submission_id);
  }
  render();
});
$('inviteBtn').addEventListener('click', () => InviteComposer.open([...state.picked]));

/* The composer, told how this page talks and what to do afterwards. Its
 * endpoints are the token'd ones: on this page the URL is the credential. */
InviteComposer.init({
  post: (path, payload) => post(path, payload),
  previewPath: () => url('/invite/preview'),
  sendPath: () => url('/invite'),
  toast,
  placeholders: () => state.placeholders,
  selectable: (id) => state.candidates
    .some((c) => c.submission_id === id && selectable(c)),
  onSent: (data) => {
    const at = new Date().toISOString();
    for (const row of data.results || []) {
      patch(row.submission_id, {
        stage: 'interview',
        stage_at: at,
        invited_at: row.invited_at || null,
      });
      // Cleared only for the ones that actually went. Anyone left behind stays
      // ticked, so a second click retries exactly them rather than the whole
      // batch again.
      if (row.sent) state.picked.delete(row.submission_id);
    }
    render();
  },
});

load();
