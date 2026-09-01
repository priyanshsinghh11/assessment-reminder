/*
 * Review links, and the shortlist hand-off leaving the building: the preview,
 * the spreadsheet and the send.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
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
