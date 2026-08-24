/*
 * The interview composer: the manager writes the invitation and watches it
 * render, then sends it.
 *
 * SHARED BY TWO PAGES ON PURPOSE. A manager reaches this either from the
 * private link in their shortlist email or from their own dashboard account.
 * The server already treats those two as one flow -- _invite_preview and
 * _invite_send take a resolved context and do not care which door produced it
 * -- and this is the other half of that. A second copy of this file would be a
 * second set of wording, a second set of guards, and a preview that agreed
 * with the send on only one of the two pages.
 *
 * Everything in the preview comes back from the server, through the same
 * builder that will send it: a preview drawn in the browser from a second copy
 * of the template is a preview of a different email.
 *
 * The boxes are only filled from a response while the manager has not touched
 * them. Otherwise a reply arriving mid-sentence would overwrite the sentence.
 *
 * The host page supplies everything page-specific through init():
 *
 *   post(path, payload)  its own transport -- the dashboard has a session and
 *                        a CSRF token to send, the review page has neither
 *   previewPath()        where to render, sendPath() where to send
 *   toast(msg, isError)  its own notifications
 *   placeholders()       the tokens the manager may drop into the message
 *   selectable(id)       whether this candidate may still be invited
 *   onSent(data)         update its own rows; shapes differ between the pages
 *   renew()              optional -- get a live workspace, see post() below
 */
window.InviteComposer = (function () {
  'use strict';

  let cfg = null;

  // Module-private, and deliberately not the host page's. Both pages define
  // helpers by these names at their own top level; a second `const esc` in
  // shared scope would be a redeclaration error the moment both files loaded.
  // The element this module mounted, and the root every lookup below is
  // scoped to.
  let root = null;

  /* Looked up INSIDE our own markup, never by document.getElementById.
   *
   * These ids are not unique on a page that has a stale copy of the old
   * composer still in its DOM -- a browser holding a cached evaluations.html
   * from before the dedup has its own #composerSubject, #composerMessage and
   * #previewFrame sitting in a hidden drawer. getElementById returns the FIRST
   * match in document order, so every write would land in those invisible
   * fields and the dialog the reader is looking at would stay blank, with no
   * error anywhere: the writes all succeed, just not where anyone can see.
   *
   * Scoping to our own root makes that impossible rather than unlikely. */
  const $ = (id) => (root ? root.querySelector('#' + id)
                          : document.getElementById(id));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const shortDate = (value) => {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value).slice(0, 10)
      : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  };

  /* --- a workspace that died under the dialog ----------------------------
   *
   * Both pages reach this module through a review token, and on the dashboard
   * that token is minted once when the panel loads and then held for the whole
   * visit. Anything that ends the link mid-session -- the recruiter revoking it
   * from the links panel two tabs away, its expiry passing, the database being
   * replaced under a dev server -- leaves every request from here answering
   * 410 against a credential the manager never saw and does not know they are
   * holding. What they get is this dialog, opened on a candidate they picked,
   * filling in with nothing: no subject, no message, no preview, and a Send
   * button that stays disabled. Nothing on screen says why, because from the
   * browser's side nothing went wrong -- it asked, and it was refused.
   *
   * The host page is the one that knows how to get a live workspace. This only
   * has to notice that the refusal was about the credential rather than about
   * the invitation, ask once, and try again.
   */
  const DEAD_LINK = new Set(['unknown', 'revoked', 'expired']);
  const isDeadLink = (err) => DEAD_LINK.has(err?.body?.state)
    || err?.status === 404 || err?.status === 410;

  /*
   * One attempt, one renewal, one retry -- and only for a dead workspace.
   *
   * `path` is the config's own function rather than a string, because renewing
   * is what changes the token and the token is IN the path: re-reading it
   * afterwards is the whole point, and a path resolved before the renewal
   * would retry against exactly the credential that just failed.
   *
   * Renewing mints on the server -- an audit row, a re-pointed link -- so it is
   * not something to do on every refused request, and a second failure belongs
   * in front of the manager rather than in another retry. A host page with no
   * way to renew (the review page's token came out of an email; there is no
   * fresher one to ask for) simply passes the refusal through.
   */
  async function post(path, body) {
    try {
      return await cfg.post(path(), body);
    } catch (err) {
      if (!isDeadLink(err) || typeof cfg.renew !== 'function') throw err;
      // A renewal that itself fails tells the manager nothing they can use --
      // "nobody is waiting on this role" in answer to a preview they are
      // looking at. The refusal they get is the original one.
      try {
        await cfg.renew();
      } catch {
        throw err;
      }
      return cfg.post(path(), body);
    }
  }

  const C = {
    ids: [],
    dirty: { subject: false, message: false },
    defaults: { subject: '', message: '' },
    calLink: '',
    recipients: [],
    ready: false,
    timer: null,
    seq: 0,
  };

  // The markup travels with the behaviour. It used to sit in review.html, and
  // copying it into evaluations.html would have left two dialogs to keep in
  // step by hand -- the exact drift this module exists to prevent.
  const MARKUP = '<div class="modal" id="composer" hidden>\n  <div class="modal-scrim" data-close-composer></div>\n  <div class="modal-box modal-wide" role="dialog" aria-modal="true"\n       aria-labelledby="composerTitle">\n    <h2 id="composerTitle">Invite to interview</h2>\n    <p id="composerTo"></p>\n\n    <p class="warn" id="composerWarn" hidden></p>\n\n    <div class="compose-grid">\n      <div class="compose-edit">\n        <label class="field" id="composerWhenField" hidden>\n          <span>Suggested time <span class="opt">optional</span></span>\n          <input type="datetime-local" id="composerWhen">\n        </label>\n\n        <label class="field">\n          <span>Subject</span>\n          <input type="text" id="composerSubject">\n        </label>\n\n        <label class="field">\n          <span>Message</span>\n          <textarea id="composerMessage" rows="16" spellcheck="true"></textarea>\n        </label>\n\n        <p class="dim small chips-line">\n          <span>Fills in per candidate:</span>\n          <span class="chips" id="composerChips"></span>\n        </p>\n\n        <p class="dim small locked-note" id="composerLocked"></p>\n\n        <p>\n          <button class="btn btn-ghost btn-sm" type="button" id="composerReset">\n            Reset to our default wording\n          </button>\n        </p>\n      </div>\n\n      <div class="compose-preview">\n        <div class="preview-head">\n          <span class="small" id="previewFor"></span>\n          <span class="dim small" id="previewState"></span>\n        </div>\n        <!-- Fully sandboxed: no scripts, no same-origin. The markup is ours\n             and the manager\'s words are escaped before they reach it, and it\n             still runs with nothing, because a preview pane is not a place to\n             be relying on that. -->\n        <iframe id="previewFrame" title="What the candidate receives" sandbox=""></iframe>\n      </div>\n    </div>\n\n    <label class="toggle resend" id="resendField" hidden>\n      <input type="checkbox" id="composerResend">\n      <span id="resendLabel"></span>\n    </label>\n\n    <div class="modal-actions">\n      <button class="btn btn-ghost" type="button" data-close-composer>Cancel</button>\n      <button class="btn btn-primary" type="button" id="composerSend">Send invitation</button>\n    </div>\n  </div>\n</div>';

  function mount() {
    if (root) return;
    const host = document.createElement('div');
    host.innerHTML = MARKUP;
    root = host.firstElementChild;
    document.body.appendChild(root);
    for (const el of root.querySelectorAll('[data-close-composer]')) {
      el.addEventListener('click', closeComposer);
    }
    $('composerSubject').addEventListener('input', () => {
      C.dirty.subject = true;
      schedulePreview();
    });
    $('composerMessage').addEventListener('input', () => {
      C.dirty.message = true;
      schedulePreview();
    });
    $('composerWhen').addEventListener('change', () => {
      // An untouched message is regenerated so the new time appears in the
      // pencilled-in line; an edited one is left alone and picks the time up
      // through its own {when}.
      refreshPreview(!C.dirty.message);
    });
    $('composerReset').addEventListener('click', () => {
      C.dirty = { subject: false, message: false };
      refreshPreview(true);
      $('composerMessage').focus();
    });
    $('composerSend').addEventListener('click', sendInvitations);
  }

  function init(options) {
    cfg = options;
    mount();
    return api;
  }

  function openComposer(ids) {
    const picks = ids.filter((id) => cfg.selectable(id));
    if (!picks.length) return;

    C.ids = picks;
    C.dirty = { subject: false, message: false };
    C.ready = false;
    C.recipients = [];

    $('composerTitle').textContent = picks.length > 1
      ? `Invite ${picks.length} candidates to interview` : 'Invite to interview';
    $('composerSubject').value = '';
    $('composerMessage').value = '';
    $('composerWhen').value = '';
    $('composerResend').checked = false;
    $('resendField').hidden = true;
    $('composerWarn').hidden = true;
    $('previewFrame').srcdoc = '';
    $('previewState').textContent = 'Rendering…';
    $('previewFor').textContent = '';
    $('composerSend').disabled = true;
    $('composerSend').textContent = picks.length > 1
      ? `Send ${picks.length} invitations` : 'Send invitation';

    // A pencilled-in time is one candidate's, so it is offered for one candidate
    // and hidden for a batch -- the server refuses it for a batch anyway, and a
    // field that is going to be rejected should not be on screen.
    $('composerWhenField').hidden = picks.length !== 1;

    $('composerChips').innerHTML = cfg.placeholders()
      .map((p) => `<button class="chip" type="button" data-insert="{${esc(p)}}">{${esc(p)}}</button>`)
      .join('');
    for (const chip of $('composerChips').querySelectorAll('[data-insert]')) {
      chip.addEventListener('click', () => insertToken(chip.dataset.insert));
    }

    root.hidden = false;
    refreshPreview(true);
    $('composerMessage').focus();
  }

  function closeComposer() {
    clearTimeout(C.timer);
    root.hidden = true;
    C.ids = [];
  }

  /* Put a placeholder where the cursor is, rather than at the end. A manager
   * adding "{first_name}" means it to land in the sentence they are looking at. */
  function insertToken(token) {
    const box = $('composerMessage');
    const at = box.selectionStart ?? box.value.length;
    const to = box.selectionEnd ?? at;
    box.value = box.value.slice(0, at) + token + box.value.slice(to);
    box.selectionStart = box.selectionEnd = at + token.length;
    C.dirty.message = true;
    box.focus();
    schedulePreview();
  }

  function schedulePreview() {
    clearTimeout(C.timer);
    $('previewState').textContent = 'Rendering…';
    C.timer = setTimeout(() => refreshPreview(false), 500);
  }

  /*
   * `reload` means "this is an open or a reset, take the server's wording".
   * Otherwise the manager's own text is sent up and echoed back, and only the
   * rendered preview changes.
   *
   * Replies are sequenced. Typing fires a request every half second and they can
   * land out of order; without the counter a slow render of an older draft would
   * paint over a newer one and the preview would disagree with the box beside it.
   */
  async function refreshPreview(reload) {
    const compose = C;
    if (!compose.ids.length) return;
    const seq = ++compose.seq;

    const body = {
      submission_ids: compose.ids,
      interview_at: $('composerWhen').value || '',
      subject: reload || !compose.dirty.subject ? '' : $('composerSubject').value,
      message: reload || !compose.dirty.message ? '' : $('composerMessage').value,
    };

    try {
      const data = await post(cfg.previewPath, body);
      if (seq !== compose.seq || !isOpen()) return;

      compose.defaults = data.defaults || { subject: '', message: '' };
      compose.calLink = data.cal_link || '';
      compose.recipients = data.recipients || [];
      compose.ready = true;

      if (!compose.dirty.subject) $('composerSubject').value = data.subject || '';
      if (!compose.dirty.message) $('composerMessage').value = data.message || '';

      $('composerTo').textContent = describeRecipients(data.recipients || []);
      $('composerLocked').textContent =
        `Below your message we always add the button that books you — `
        + `${compose.calLink} — and your name. Those cannot be edited away, so an `
        + `invitation can never go out with no way to book.`;

      $('previewFor').textContent = data.preview
        ? `As ${data.preview.name || data.preview.to} will read it`
        : '';
      $('previewState').textContent = compose.ids.length > 1
        ? `1 of ${compose.ids.length} — the rest say the same with their own names`
        : '';
      $('previewFrame').srcdoc = data.preview?.html || '';

      renderResend();
      $('composerWarn').hidden = true;
      $('composerSend').disabled = false;
    } catch (err) {
      if (seq !== compose.seq || !isOpen()) return;
      compose.ready = false;
      $('previewState').textContent = '';
      // Never left blank. An empty dialog with an empty warning above it is
      // the same thing as no warning at all, and this is the one place the
      // manager finds out that what they are looking at is not their email.
      $('composerWarn').textContent = err.message
        || 'We could not render this invitation. Reload the page and try again.';
      $('composerWarn').hidden = false;
      $('composerSend').disabled = true;
    }
  }

  function describeRecipients(recipients) {
    if (!recipients.length) return '';
    if (recipients.length === 1) {
      const one = recipients[0];
      return `To ${one.name} <${one.email || 'no address on record'}>`;
    }
    const names = recipients.map((r) => r.name).join(', ');
    return `To ${plural(recipients.length, 'candidate')}: ${names}`;
  }

  /* An invitation sent twice is a candidate holding two booking links wondering
   * which one is real. The server refuses the second copy on its own; this is
   * the line that lets the manager see it coming and say yes on purpose. */
  function renderResend() {
    const already = C.recipients.filter((r) => r.invited_at);
    $('resendField').hidden = already.length === 0;
    if (!already.length) return;
    $('resendLabel').textContent = already.length === C.recipients.length
      ? (already.length === 1
        ? `${already[0].name} was already invited on ${shortDate(already[0].invited_at)} — send it again`
        : `All ${already.length} were already invited — send it to them again`)
      : `${plural(already.length, 'of them')} were already invited (${
          already.map((r) => r.name).join(', ')}) — send it to them again too`;
  }

  async function sendInvitations() {
    const compose = C;
    if (!compose.ids.length || !compose.ready) return;

    const count = compose.ids.length;
    const who = count === 1
      ? (compose.recipients[0]?.name || 'this candidate')
      : `${count} candidates`;
    if (!window.confirm(
      `Send the invitation to ${who} now?\n\n`
      + 'It goes out over your calendar, signed with your name. '
      + 'There is no way to unsend it.')) return;

    const btn = $('composerSend');
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Sending…';

    try {
      // Renews and retries on a dead workspace exactly as the preview does.
      // Safe to repeat: a refusal about the credential is one the server made
      // before it read the invitation, so nothing left the building.
      const data = await post(cfg.sendPath, {
        submission_ids: compose.ids,
        subject: $('composerSubject').value,
        message: $('composerMessage').value,
        interview_at: $('composerWhen').value || '',
        resend: $('composerResend').checked,
      });

      // What just happened to the host page's own rows is the host page's to
      // decide. The dashboard and the review page keep different lists in
      // different shapes; both need to mark these people interviewed and
      // untick the ones that actually went, and neither wants the other's
      // idea of how.
      cfg.onSent(data);

      closeComposer();
      cfg.toast(data.message, data.sent !== data.total);
    } catch (err) {
      btn.textContent = label;
      btn.disabled = false;
      // The one refusal the manager can act on themselves stays on screen rather
      // than flashing past in a toast.
      if (err.body?.needs === 'cal_link') {
        $('composerWarn').textContent = err.message;
        $('composerWarn').hidden = false;
      } else {
        cfg.toast(err.message, true);
      }
      return;
    }
    btn.textContent = label;
    btn.disabled = false;
  }

  // isOpen so a host page's Escape handler can ask whether this dialog is
  // up without knowing the id of markup it no longer owns.
  const isOpen = () => !!root && !root.hidden;

  const api = { init, open: openComposer, close: closeComposer, isOpen };
  return api;
})();
