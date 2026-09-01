/*
 * The hiring pipeline board: which stage each candidate is at, moving them
 * between stages, and the export.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

const STAGE_LABEL = {
  interview: 'Interview scheduled',
  hired: 'Hired',
  rejected: 'Rejected after review',
};

const STAGE_CLASS = {
  interview: 'badge-stage-interview',
  hired: 'badge-stage-hired',
  rejected: 'badge-stage-rejected',
};

const stageOf = (c) => {
  const stage = c?.pipeline?.stage;
  return STAGE_LABEL[stage] ? stage : null;
};

/* An interview time is a wall-clock string from a datetime-local input
 * ("2026-08-20T14:30"), stored verbatim: it means the time the interviewer
 * typed, in their own day, and reinterpreting it against a timezone we guessed
 * would move real meetings. So it is formatted by hand rather than through
 * Date, which would apply one. */
function fmtWhen(value) {
  if (!value) return '';
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(value);
  const [, y, mo, d, hh, mm] = m;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = `${Number(d)} ${MONTHS[Number(mo) - 1]}`;
  const year = Number(y) === new Date().getFullYear() ? '' : ` ${y}`;
  return hh ? `${day}${year}, ${hh}:${mm}` : `${day}${year}`;
}

/* "in 2 days", "today", "3 days ago" -- an interview list is read for what is
 * imminent, and a date on its own makes you count. */
function whenRelative(value) {
  if (!value) return '';
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return '';
  const today = new Date();
  const days = Math.round(
    (new Date(when.getFullYear(), when.getMonth(), when.getDate()) -
     new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days === -1) return 'yesterday';
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

function renderPipelineRoleOptions() {
  const select = $('pipelineRole');
  const current = select.value;
  const withStages = state.roles.filter((r) => r.pipeline
    && (r.pipeline.interview + r.pipeline.hired + r.pipeline.rejected) > 0);
  select.innerHTML = '<option value="">All roles</option>' + withStages
    .map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join('');
  select.value = current;
}

function renderPipelineCounts(counts) {
  state.pipeline.counts = counts;
  for (const el of document.querySelectorAll('[data-count]')) {
    el.textContent = (counts[el.dataset.count] || 0).toLocaleString();
  }
}

async function loadPipeline() {
  const { stage } = state.pipeline;
  const jobId = $('pipelineRole').value;
  $('pipelineStatus').textContent = 'Loading…';
  try {
    const data = await api(`/api/pipeline?stage=${stage}` + (jobId ? `&job_id=${jobId}` : ''));
    state.pipeline.rows = data.candidates;
    renderPipelineCounts(data.counts.stages);
    renderPipeline();
  } catch (err) {
    $('pipelineStatus').textContent = 'Could not load the board.';
    toast(err.message, true);
  }
}

const PIPELINE_HEAD = {
  interview: ['Candidate', 'Role', 'Score', 'Interview', 'Interviewer', 'Note', ''],
  hired: ['Candidate', 'Role', 'Score', 'Hired', 'Interviewed', 'Note', ''],
  rejected: ['Candidate', 'Role', 'Score', 'Rejected', 'Interviewed', 'Reason', ''],
};

const PIPELINE_HINT = {
  interview: 'Everyone a hiring manager has invited, soonest first. A row with '
    + 'no date is one the candidate has not booked into yet. Read-only as to '
    + 'who is here — mark the outcome from this row or from their card.',
  hired: 'Offers accepted. The score and the grid that produced it stay on the '
    + 'record, so a hire can be read back against what the assessment predicted.',
  rejected: 'Turned down after being seen — kept apart from the '
    + 'missing-artefact list below, which is a different email to a different '
    + 'candidate.',
};

const PIPELINE_EMPTY = {
  interview: 'Nobody has been invited yet. Send a role\u2019s shortlist to its '
    + 'hiring manager from the Shortlist tab, and whoever they invite from '
    + 'their review link appears here.',
  hired: 'No hires recorded yet.',
  rejected: 'Nobody has been rejected after review.',
};

/* The move buttons on a row. What is worth offering depends on where the
 * candidate is: an interview needs an outcome, a closed stage needs a way back
 * if the outcome was recorded on the wrong person.
 *
 * "Back to interview" is gone with the rest of the interview stage. Undoing a
 * mis-recorded outcome is Remove, which puts them back on the shortlist where
 * the manager can invite them again -- and the manager finds out, which a
 * silent re-book on this page would not tell them. */
function stageActions(c) {
  const stage = stageOf(c);
  const btn = (to, label, cls = 'btn-ghost') =>
    `<button class="btn ${cls} btn-sm" data-move="${to}" data-id="${c.id}">${label}</button>`;
  if (stage === 'interview') {
    return btn('hired', 'Hired', 'btn-primary') + btn('rejected', 'Reject');
  }
  return btn('', 'Remove');
}

/* A board click that would reach a candidate's inbox asks first.
 *
 * While sending is manual it cannot: a move records where somebody is and
 * nothing leaves the building, so the row buttons are safe by construction and
 * a confirm on each of them would only teach people to click through dialogs.
 * The question comes back the moment the server says moves send again -- the
 * one irreversible thing a row button could do is tell someone they did not
 * get the job, and that is worth a sentence first. */
function boardMoveDetail(row, stage) {
  const who = row?.candidate_name || 'this candidate';
  if (stage === 'rejected' && state.mail.auto) {
    const ok = window.confirm(
      `Reject ${who} and email them the rejection now?\n\n`
      + 'Cancel to open their card instead, where you can add a message, '
      + 'preview the email, or record the rejection without sending one.');
    return ok ? {} : null;
  }
  // Moving someone back out of a closed stage is a correction of ours. The
  // candidate is not told, because there is nothing to tell them yet.
  if (stage === null) return { notify: false };
  return {};
}

function renderPipeline() {
  const { stage, rows } = state.pipeline;
  const head = PIPELINE_HEAD[stage];

  $('pipelineHead').innerHTML = head
    .map((h) => `<th${h === 'Score' ? ' class="num"' : ''}>${esc(h)}</th>`).join('');
  $('pipelineHint').textContent = PIPELINE_HINT[stage];
  $('pipelineEmpty').textContent = PIPELINE_EMPTY[stage];
  $('pipelineEmpty').hidden = rows.length > 0;

  const roleName = $('pipelineRole').selectedOptions[0]?.textContent || 'all roles';
  $('pipelineStatus').textContent = rows.length
    ? `${rows.length.toLocaleString()} candidate(s) · ${roleName}`
    : `Nothing at this stage · ${roleName}`;
  for (const id of ['pipelineCopyBtn', 'pipelineCsvBtn']) {
    $(id).disabled = rows.length === 0;
  }

  $('pipelineBody').innerHTML = rows.map((c) => {
    const p = c.pipeline || {};
    const ev = c.evaluation;
    // Column three onwards differs by stage: an interview is read for when it
    // is, a closed stage for when it was decided and what was said.
    const when = stage === 'interview'
      ? (p.interview_at
        ? `${esc(fmtWhen(p.interview_at))} <span class="dim">${
            esc(whenRelative(p.interview_at))}</span>`
        : '<span class="warn">No date set</span>')
      : `${esc(shortDate(p.at))} <span class="dim">${esc(whenRelative(p.at))}</span>`;
    const second = stage === 'interview'
      ? esc(p.interviewer || '—')
      : (p.interview_at ? esc(fmtWhen(p.interview_at)) : '<span class="dim">not interviewed</span>');
    const words = stage === 'rejected' ? (p.reason || p.note) : p.note;
    // Who decided. A hiring manager acting on their own review link is a
    // different fact from a recruiter moving someone on this page, and the
    // board could otherwise say a candidate was hired without ever saying by
    // whom -- the first question asked about any hire that goes wrong.
    const who = p.source === 'manager'
      ? `<span class="by-chip" title="${esc(p.by || '')}">via manager</span>` : '';

    return `
      <tr class="row-click" data-id="${c.id}">
        <td>
          <div class="cand-name">${esc(c.candidate_name || '—')}</div>
          <div class="cand-email">${esc(c.candidate_email || '')}</div>
        </td>
        <td class="dim">${esc(c.job_title || '—')}</td>
        ${scoreCell(ev, c)}
        <td class="nowrap">${when}</td>
        <td class="dim">${second}</td>
        <td><div class="brief-cell">${who}${esc(words || '')}</div></td>
        <td class="nowrap stage-actions">${stageActions(c)}</td>
      </tr>`;
  }).join('');

  for (const tr of $('pipelineBody').querySelectorAll('tr')) {
    tr.addEventListener('click', () => openDrawer(Number(tr.dataset.id)));
  }
  for (const btn of $('pipelineBody').querySelectorAll('[data-move]')) {
    btn.addEventListener('click', (e) => {
      // The row opens the drawer; a button on it must not do both.
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      const stage = btn.dataset.move || null;
      const detail = boardMoveDetail(rows.find((r) => r.id === id), stage);
      if (detail === null) { openDrawer(id); return; }
      moveStage(id, stage, detail);
    });
  }
}

/* Move one candidate. Everything that could be showing them -- the board, the
 * role tallies, the open role's table -- is refreshed from the server rather
 * than patched, so two views can never disagree about where someone is. */
async function moveStage(submissionId, stage, detail = {}) {
  try {
    const result = await api('/api/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId, stage, ...detail }),
    });
    // An email that did not go out is reported as a problem, not folded into
    // the cheerful line about the move: "rejected, not emailed -- no address
    // on record" is a candidate still waiting to hear.
    toast(result.message, Boolean(result.mail && result.mail.reason && !result.mail.sent
                                  && result.mail.reason !== 'Not requested.'));
    renderPipelineCounts(result.counts.stages);
    await loadPipeline();
    if (state.activeRoleId) await openRole(state.activeRoleId, false);
    await loadRoles();
    // Taking somebody back out of a stage puts them among the people awaiting
    // a decision again, which is exactly what the panel above the board lists.
    if (state.top.jobId !== null) await loadTopCandidates(true);
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  }
}

function selectStage(stage) {
  if (!STAGE_LABEL[stage]) return;
  state.pipeline.stage = stage;
  for (const tab of $('pipelineTabs').querySelectorAll('.tab')) {
    const on = tab.dataset.stage === stage;
    tab.classList.toggle('is-active', on);
    tab.setAttribute('aria-selected', String(on));
  }
  loadPipeline();
}

function copyPipelineEmails() {
  const rows = state.pipeline.rows;
  if (!rows.length) return;
  const emails = [...new Set(rows.map((c) => c.candidate_email).filter(Boolean))];
  copyToClipboard(emails.join('; '), `Copied ${emails.length} address(es)`);
}

function exportPipelineCsv() {
  const { stage, rows } = state.pipeline;
  if (!rows.length) return;
  const csv = [['Name', 'Email', 'Role', 'Score', 'Stage', 'Interview',
                'Interviewer', 'Decided', 'Note']]
    .concat(rows.map((c) => [
      c.candidate_name || '',
      c.candidate_email || '',
      c.job_title || '',
      c.evaluation?.score ?? '',
      STAGE_LABEL[stage],
      c.pipeline?.interview_at || '',
      c.pipeline?.interviewer || '',
      c.pipeline?.at || '',
      c.pipeline?.reason || c.pipeline?.note || '',
    ]));
  const role = $('pipelineRole').selectedOptions[0]?.textContent || 'all-roles';
  saveCsv(csv, `${stage}-${slugify(role)}.csv`);
  toast(`Downloaded ${rows.length} candidate(s).`);
}

/* --- the top N, and the invitation the manager writes -------------------
 *
 * The shortlist a hiring manager actually works, sitting one panel above the
 * board it feeds: the people still awaiting a decision, then the people who
 * have had one. It used to be a filter on the Candidates tab and a button that
 * navigated away; both were a step away from where the work continues.
 *
 * THE ONE DOOR IS UNCHANGED. A candidate enters the interview stage from a
 * hiring manager's review workspace and from nowhere else -- /api/pipeline
 * still refuses that move outright, and nothing here asks it to. What this
 * section does is open the caller's OWN workspace for the role they have on
 * screen (/api/managers/review-link, entitled by being named on the role) and
 * drive it from the dashboard, through the same two routes the emailed review
 * page uses. So there is still one composer, one preview builder and one place
 * the interview stage is written -- this is a second way in to it, not a
 * second copy of it.
 *
 * It also settles who may be picked. The rows on screen are the workspace's
 * own list, so a tick can never be a candidate the server would refuse.
 */
