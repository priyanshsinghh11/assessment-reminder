/*
 * The rejected list, in two columns: who has not been told, and who has.
 *
 * Also the clipboard and CSV helpers, which start here because this list was
 * the first thing to need them. The pipeline board below reuses them.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

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
