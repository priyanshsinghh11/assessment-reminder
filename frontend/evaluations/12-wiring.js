/*
 * Portal sync, the rejected-list toggle, and every event listener on the
 * page. Ends with the loadRoles() that starts the dashboard.
 *
 * LOADED LAST, AND IT HAS TO BE. Every listener below binds a handler defined
 * in one of the files above, and the loadRoles() call on the final line is the
 * only thing here that runs rather than waits.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

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
