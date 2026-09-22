/*
 * The rejection composer: the recruiter writes the rejection and watches it
 * render, then sends it to everybody ticked.
 *
 * WHY THIS IS NOT InviteComposer WITH A FLAG. The two dialogs look alike and
 * are not the same job. An invitation goes to one person a manager has just
 * read, over that manager's calendar, and the thing it cannot be sent without
 * is their booking link; a rejection goes to everybody on the left-hand list
 * at once, in the recruiter's own words, and the thing it cannot be sent
 * without is the unsubscribe footer. They address people by different keys as
 * well -- submission ids there, email addresses here, because the rejection
 * list is de-duplicated by address and somebody who sat two assessments is one
 * recipient. A shared module would carry both sets of rules and a flag
 * deciding which half to obey, and the half that is wrong is a rejection sent
 * over somebody's calendar link.
 *
 * What it DOES copy, deliberately: the preview is rendered by the server
 * through the same builder that sends, the boxes are only filled from a
 * response while the recruiter has not touched them, and replies are sequenced
 * so a slow render of an older draft cannot paint over a newer one.
 *
 * The send is a background batch, unlike the invitation's. Four hundred
 * messages outlive the request that asked for them, so the server answers 202
 * with a job id and this polls it -- see api_send_rejections. The dialog stays
 * up showing progress, because closing it on the 202 would put "sending..."
 * behind a page that looks finished.
 *
 * The host page supplies everything page-specific through init():
 *
 *   post(path, payload)  its own transport, carrying session and CSRF
 *   get(path)            the same, for polling the batch
 *   previewPath()        where to render
 *   sendPath()           where to send
 *   statusPath(job)      where to watch it
 *   toast(msg, isError)  its own notifications
 *   onSent(status)       re-read whatever was showing these people
 */
window.RejectionComposer = (function () {
  'use strict';

  let cfg = null;
  let root = null;

  /* Scoped to our own markup, never document.getElementById -- the dashboard
   * has the invitation composer mounted beside this one, and a browser holding
   * a cached copy of either has more than one element per id. getElementById
   * returns the first in document order, so every write would land somewhere
   * invisible and nothing would report an error. */
  const $ = (id) => (root ? root.querySelector('#' + id)
                          : document.getElementById(id));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  const C = {
    recipients: [],
    jobId: null,
    dirty: { subject: false, message: false },
    placeholders: [],
    ready: false,
    sending: false,
    timer: null,
    poll: null,
    seq: 0,
  };

  // The markup travels with the behaviour, for the reason the invitation
  // composer gives: a copy in evaluations.html is a second dialog to keep in
  // step by hand.
  const MARKUP = [
    '<div class="modal" id="rejectComposer" hidden>',
    '  <div class="modal-scrim" data-close-reject></div>',
    '  <div class="modal-box modal-wide" role="dialog" aria-modal="true"',
    '       aria-labelledby="rcTitle">',
    '    <h2 id="rcTitle">Send rejection email</h2>',
    '    <p id="rcTo"></p>',
    '    <p class="warn" id="rcWarn" hidden></p>',
    '    <div class="compose-grid">',
    '      <div class="compose-edit">',
    '        <label class="field">',
    '          <span>Subject</span>',
    '          <input type="text" id="rcSubject">',
    '        </label>',
    '        <label class="field">',
    '          <span>Message</span>',
    '          <textarea id="rcMessage" rows="16" spellcheck="true"></textarea>',
    '        </label>',
    '        <p class="dim small chips-line">',
    '          <span>Fills in per candidate:</span>',
    '          <span class="chips" id="rcChips"></span>',
    '        </p>',
    '        <p class="dim small locked-note" id="rcLocked"></p>',
    '        <p>',
    '          <button class="btn btn-ghost btn-sm" type="button" id="rcReset">',
    '            Reset to our default wording',
    '          </button>',
    '        </p>',
    '      </div>',
    '      <div class="compose-preview">',
    '        <div class="preview-head">',
    '          <span class="small" id="rcPreviewFor"></span>',
    '          <span class="dim small" id="rcPreviewState"></span>',
    '        </div>',
    // Sandboxed the way the invitation's is: our markup, the recruiter's words
    // escaped by the server before they reach it, and still no scripts and no
    // same-origin, because a preview pane is not a place to rely on that.
    '        <iframe id="rcPreviewFrame" class="preview-frame"',
    '                title="What the candidate receives" sandbox=""></iframe>',
    '      </div>',
    '    </div>',
    '    <p class="dim small" id="rcProgress" hidden></p>',
    '    <div class="modal-actions">',
    '      <button class="btn btn-ghost" type="button" data-close-reject>Cancel</button>',
    '      <button class="btn btn-primary" type="button" id="rcSend">Send</button>',
    '    </div>',
    '  </div>',
    '</div>',
  ].join('\n');

  function mount() {
    if (root) return;
    const host = document.createElement('div');
    host.innerHTML = MARKUP;
    root = host.firstElementChild;
    document.body.appendChild(root);
    for (const el of root.querySelectorAll('[data-close-reject]')) {
      el.addEventListener('click', closeComposer);
    }
    $('rcSubject').addEventListener('input', () => {
      C.dirty.subject = true;
      schedulePreview();
    });
    $('rcMessage').addEventListener('input', () => {
      C.dirty.message = true;
      schedulePreview();
    });
    $('rcReset').addEventListener('click', () => {
      C.dirty = { subject: false, message: false };
      refreshPreview(true);
      $('rcMessage').focus();
    });
    $('rcSend').addEventListener('click', send);
  }

  function init(options) {
    cfg = options;
    mount();
    return api;
  }

  /* `recipients` is [{email, name}] -- the ticked rows, already filtered by the
   * host to people who have not been told. The server checks the ledger and the
   * opt-out list again before a single message goes out; this is the list the
   * recruiter is looking at, not the list that decides. */
  function openComposer(options) {
    const opts = options || {};
    const picks = (opts.recipients || []).filter((r) => r && r.email);
    if (!picks.length) return;

    C.recipients = picks;
    C.jobId = opts.jobId ?? null;
    C.dirty = { subject: false, message: false };
    C.ready = false;
    C.sending = false;

    $('rcTitle').textContent = picks.length > 1
      ? 'Send ' + picks.length + ' rejection emails' : 'Send rejection email';
    $('rcTo').textContent = describeRecipients(picks);
    $('rcSubject').value = '';
    $('rcMessage').value = '';
    $('rcWarn').hidden = true;
    $('rcProgress').hidden = true;
    $('rcPreviewFrame').srcdoc = '';
    $('rcPreviewState').textContent = 'Rendering...';
    $('rcPreviewFor').textContent = '';
    $('rcSend').disabled = true;
    $('rcSend').textContent = picks.length > 1
      ? 'Send ' + picks.length + ' rejections' : 'Send rejection';

    root.hidden = false;
    refreshPreview(true);
    $('rcMessage').focus();
  }

  function closeComposer() {
    // A batch on the server does not stop because the dialog did -- this only
    // stops watching it. Left running, the poll would go on firing against a
    // dialog nobody is looking at and toast over whatever the recruiter moved
    // on to.
    clearTimeout(C.timer);
    clearTimeout(C.poll);
    root.hidden = true;
    C.recipients = [];
    C.sending = false;
  }

  function describeRecipients(recipients) {
    if (recipients.length === 1) {
      const one = recipients[0];
      return 'To ' + (one.name || one.email) + ' <' + one.email + '>';
    }
    const shown = recipients.slice(0, 6).map((r) => r.name || r.email).join(', ');
    const rest = recipients.length - 6;
    return 'To ' + plural(recipients.length, 'candidate') + ': ' + shown
      + (rest > 0 ? ' and ' + rest + ' more' : '');
  }

  /* Put a placeholder where the cursor is, rather than at the end. */
  function insertToken(token) {
    const box = $('rcMessage');
    const at = box.selectionStart ?? box.value.length;
    const to = box.selectionEnd ?? at;
    box.value = box.value.slice(0, at) + token + box.value.slice(to);
    box.selectionStart = box.selectionEnd = at + token.length;
    C.dirty.message = true;
    box.focus();
    schedulePreview();
  }

  function renderChips() {
    $('rcChips').innerHTML = (C.placeholders || [])
      .map((p) => '<button class="chip" type="button" data-insert="{' + esc(p)
        + '}">{' + esc(p) + '}</button>')
      .join('');
    for (const chip of $('rcChips').querySelectorAll('[data-insert]')) {
      chip.addEventListener('click', () => insertToken(chip.dataset.insert));
    }
  }

  function schedulePreview() {
    clearTimeout(C.timer);
    $('rcPreviewState').textContent = 'Rendering...';
    C.timer = setTimeout(() => refreshPreview(false), 500);
  }

  /*
   * `reload` means "this is an open or a reset, take the server's wording".
   * Otherwise the recruiter's own text is sent up and echoed back, and only
   * the rendered preview changes.
   *
   * Rendered against the FIRST REAL RECIPIENT rather than a sample name, so the
   * unsubscribe link in the footer is the one that person will actually get.
   *
   * The boxes are filled from `defaults`, which is the wording with its
   * placeholders still in it. `email` beside it is one candidate's copy with
   * {first_name} already resolved -- prefilling from that would put the first
   * recipient's name into the template every other recipient is sent.
   */
  async function refreshPreview(reload) {
    const compose = C;
    if (!compose.recipients.length) return;
    const seq = ++compose.seq;
    const first = compose.recipients[0];

    const body = {
      job_id: compose.jobId,
      name: first.name || '',
      email: first.email,
      subject: reload || !compose.dirty.subject ? '' : $('rcSubject').value,
      message: reload || !compose.dirty.message ? '' : $('rcMessage').value,
    };

    try {
      const data = await cfg.post(cfg.previewPath(), body);
      if (seq !== compose.seq || !isOpen()) return;

      compose.placeholders = data.placeholders || [];
      compose.ready = true;
      renderChips();

      if (!compose.dirty.subject) {
        $('rcSubject').value = data.defaults?.subject || data.email?.subject || '';
      }
      if (!compose.dirty.message) $('rcMessage').value = data.defaults?.message || '';

      $('rcLocked').textContent =
        'Under your message we always add our sign-off and the unsubscribe '
        + 'link. Those cannot be edited away, so a rejection can never go out '
        + 'with no way off the list.';

      $('rcPreviewFor').textContent = 'As ' + (first.name || first.email)
        + ' will read it';
      $('rcPreviewState').textContent = compose.recipients.length > 1
        ? '1 of ' + compose.recipients.length
          + ' - the rest say the same with their own names'
        : '';
      $('rcPreviewFrame').srcdoc = data.email?.html || '';

      $('rcWarn').hidden = true;
      if (!compose.sending) $('rcSend').disabled = false;
    } catch (err) {
      if (seq !== compose.seq || !isOpen()) return;
      compose.ready = false;
      $('rcPreviewState').textContent = '';
      // Never left blank. An empty dialog with an empty warning above it is the
      // same as no warning at all, and this is the one place the recruiter
      // finds out that what they are looking at is not their email.
      $('rcWarn').textContent = err.message
        || 'We could not render this rejection. Reload the page and try again.';
      $('rcWarn').hidden = false;
      $('rcSend').disabled = true;
    }
  }

  /* The button that reaches everybody on the list. It asks first, by count,
   * because there is no unsend and no second look. */
  async function send() {
    const compose = C;
    if (!compose.recipients.length || !compose.ready || compose.sending) return;

    const count = compose.recipients.length;
    const who = count === 1
      ? (compose.recipients[0].name || compose.recipients[0].email)
      : count + ' candidates';
    if (!window.confirm(
      'Send the rejection to ' + who + ' now?\n\n'
      + 'It goes out from Ajaia, in the wording in the preview. '
      + 'There is no way to unsend it.')) return;

    const btn = $('rcSend');
    const label = btn.textContent;
    compose.sending = true;
    btn.disabled = true;
    btn.textContent = 'Sending...';
    $('rcWarn').hidden = true;

    let started;
    try {
      started = await cfg.post(cfg.sendPath(), {
        recipients: compose.recipients.map((r) => ({
          email: r.email, name: r.name || '',
        })),
        subject: $('rcSubject').value,
        message: $('rcMessage').value,
        job_id: compose.jobId,
      });
    } catch (err) {
      compose.sending = false;
      btn.textContent = label;
      btn.disabled = false;
      // The refusals worth reading -- nobody new on the list, a batch already
      // running, candidate mail switched off on this server, over the per-send
      // cap -- stay on screen rather than flashing past in a toast.
      $('rcWarn').textContent = err.message;
      $('rcWarn').hidden = false;
      return;
    }

    $('rcProgress').hidden = false;
    $('rcProgress').textContent = started.message
      || 'Sending to ' + started.queued + ' candidate(s)...';
    watch(started.job, label);
  }

  /*
   * Watch the batch until it stops. Every second: the send loop writes a row
   * per candidate and four hundred of them take minutes, so this is a progress
   * line rather than a wait.
   *
   * A dropped poll is not a failed send. The batch is on the server, not in
   * this tab, so a blip keeps asking; a batch that has genuinely gone answers
   * 404 and ends the watch with something the recruiter can act on.
   */
  function watch(job, label) {
    let misses = 0;

    const tick = async () => {
      if (!isOpen()) return;
      let status;
      try {
        status = await cfg.get(cfg.statusPath(job));
        misses = 0;
      } catch (err) {
        if (err.status === 404 || ++misses > 10) return finish(null, err, label);
        C.poll = setTimeout(tick, 1000);
        return;
      }

      if (status.state === 'running') {
        $('rcProgress').textContent = status.message
          || 'Sent ' + status.done + ' of ' + status.total + '...';
        C.poll = setTimeout(tick, 1000);
        return;
      }
      finish(status, null, label);
    };

    C.poll = setTimeout(tick, 1000);
  }

  async function finish(status, err, label) {
    C.sending = false;
    const btn = $('rcSend');
    btn.textContent = label;
    btn.disabled = false;

    // Whatever happened, the list is re-read. Anything that did go out is
    // already in the ledger, and a stale list is how somebody gets a second
    // rejection.
    await cfg.onSent(status || {});

    if (!status) {
      $('rcProgress').hidden = true;
      $('rcWarn').textContent = (err?.message || 'We lost track of that send.')
        + ' It may still be running -- check the list before sending again.';
      $('rcWarn').hidden = false;
      return;
    }

    if (status.state !== 'done' || status.error) {
      $('rcProgress').hidden = true;
      $('rcWarn').textContent = status.error || status.message
        || 'That send did not finish.';
      $('rcWarn').hidden = false;
      return;
    }
    closeComposer();
    cfg.toast(status.message || 'Rejections sent.');
  }

  // isOpen so the host page's Escape handler can ask whether this dialog is up
  // without knowing the id of markup it no longer owns.
  const isOpen = () => !!root && !root.hidden;

  const api = { init, open: openComposer, close: closeComposer, isOpen };
  return api;
})();
