/*
 * Loading the roles, and deciding what this account is allowed to see.
 *
 * applyAccountView() is the client half of the access rule -- the server half
 * is auth.visible_job_ids(), which is the one that actually enforces it. This
 * hides what a person may not act on; it is not the boundary.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

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
