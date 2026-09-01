/*
 * The two views -- the role list and one role's candidates -- the tabs
 * between them, the URL hash that survives a reload, and the candidate table.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
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
 * `evaluation` while MANAGER_DASHBOARD_SCORES is off -- see
 * MANAGER_SUBMISSION_FIELDS -- so the old `typeof c.evaluation?.score ===
 * 'number'` test was false for every row on their screen, and the list behind
 * their invite button came back empty. The server sends `graded` and
 * `rejected` for exactly this, and a recruiter's payload still carries the
 * originals, so both are read and either will do. (`decision` IS on a
 * manager's payload now, narrowed to status and reason, because they grade.)
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
