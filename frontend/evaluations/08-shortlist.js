/*
 * The top N and everything the hiring manager does with it: the shortlist
 * itself, the manager list, a manager's booking link, and the interview
 * invitation they write.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
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
    // The pack's own wording for each planted issue. Cards can render before
    // this lands and fall back to the de-slugged key, so this only ever
    // improves the chips -- it is never what makes them appear.
    rememberSeededLabels(rubric.seeded);
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
