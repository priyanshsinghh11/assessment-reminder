/*
 * The candidate drawer: the grid, the CV table, the findings, the GIA read,
 * the mail history and the stage controls. The longest file here because it
 * is the whole of what a reviewer reads about one person.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

async function openDrawer(submissionId) {
  const drawer = $('drawer');
  drawer.hidden = false;
  $('drawerBody').innerHTML = '<p class="empty">Loading submission…</p>';

  try {
    const c = await api(`/api/evaluations/submission/${submissionId}`);
    $('drawerRole').textContent = c.job_title || '';
    $('drawerName').textContent = c.candidate_name || '—';
    $('drawerEmail').textContent = c.candidate_email || '';
    $('drawerBody').innerHTML = drawerContent(c);

    const reconsider = $('drawerBody').querySelector('[data-reconsider]');
    if (reconsider) {
      reconsider.addEventListener('click', () => setDecision(c.id, 'pending'));
    }
    const reject = $('drawerBody').querySelector('[data-reject]');
    if (reject) reject.addEventListener('click', () => setDecision(c.id, 'rejected'));
    const evaluate = $('drawerBody').querySelector('[data-evaluate]');
    if (evaluate) {
      evaluate.addEventListener('click', () => evaluateOne(c.id, evaluate));
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-stage]')) {
      btn.addEventListener('click', async () => {
        const stage = btn.dataset.stage || null;
        const words = $('stageNote').value.trim();
        const detail = stageMailFields(c);
        // The same box is a note on the way in and a reason on the way out;
        // stored under the field the stage is read by, so the board's Reason
        // column is never empty on a rejection someone explained.
        if (words) detail[stage === 'rejected' ? 'reason' : 'note'] = words;

        btn.disabled = true;
        const ok = await moveStage(c.id, stage, detail);
        if (ok) openDrawer(c.id);
        else btn.disabled = false;
      });
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-mailpreview]')) {
      btn.addEventListener('click', () => previewStageEmail(c, btn.dataset.mailpreview));
    }

    for (const btn of $('drawerBody').querySelectorAll('[data-mailsend]')) {
      btn.addEventListener('click', () => sendStageEmail(c, btn.dataset.mailsend, btn));
    }
  } catch (err) {
    $('drawerBody').innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

/* What the drawer's form says about the email: who signs it, and the line the
 * candidate reads. No calendar -- the only mail that carries one is the
 * invitation, and that is not sent from here.
 *
 * `email_note` is deliberately separate from the internal note above it. One
 * box is for the next reviewer and one is for the candidate, and a form that
 * quietly forwarded the first to the second would put a private remark in
 * front of the person it is about. */
function stageMailFields(c) {
  const picked = $('drawerBody').querySelector('#stageManager')?.value;
  const owner = picked
    ? (c.managers || []).find((m) => m.email === picked)
    : managerFor(c);
  return {
    // No `notify` -- the server's switch decides whether a move sends, and a
    // hardcoded false here would quietly outvote it the day it is turned on.
    manager_email: owner?.email || undefined,
    email_note: $('stageEmailNote')?.value.trim() || undefined,
  };
}

/* The send. One click, one candidate, one message -- the only thing on this
 * page that reaches somebody outside the company.
 *
 * It asks first, by name and by address, because there is no unsend. The
 * server refuses a second copy of the same message on its own, so the confirm
 * is about the first one being right rather than about clicking twice; a
 * candidate who has already been written to comes back as "already emailed"
 * and the resend has to be asked for outright. */
async function sendStageEmail(c, stage, btn) {
  const fields = stageMailFields(c);
  const who = c.candidate_name || 'this candidate';
  if (!window.confirm(
    `Send ${who} the rejection at ${c.candidate_email || 'their address'} now?`)) {
    return;
  }

  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    const result = await api('/api/pipeline/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        submission_id: c.id,
        stage,
        manager_email: fields.manager_email,
        email_note: fields.email_note,
      }),
    });
    toast(result.message, !result.mail?.sent);
    // Reopened rather than patched: the card carries the mail history, and a
    // send that is not visible in it is a send somebody will make twice.
    if (result.mail?.sent) openDrawer(c.id);
    else btn.textContent = label;
  } catch (err) {
    toast(err.message, true);
    btn.textContent = label;
  } finally {
    btn.disabled = false;
  }
}

/* The candidate's email, rendered by the server function that sends it, in the
 * same drawer the shortlist preview uses. A manager should be able to read
 * what a click will put in someone's inbox before making it. */
async function previewStageEmail(c, stage) {
  const fields = stageMailFields(c);
  const query = new URLSearchParams({ submission_id: String(c.id), stage });
  for (const key of ['manager_email', 'email_note']) {
    if (fields[key]) query.set(key, fields[key]);
  }

  try {
    const data = await api(`/api/pipeline/preview?${query}`);
    $('mailSubject').textContent = data.email.subject;
    $('mailTo').textContent = `To ${data.to_name} <${data.to}>`
      + (data.already_sent ? ` · already emailed ${shortDate(data.already_sent.at)}` : '');
    $('mailPreview').hidden = false;
    // srcdoc and the same grow-to-fit as the shortlist preview: the message
    // renders in its own colours rather than inheriting the dashboard's dark
    // tokens, which the candidate will never see.
    const frame = $('mailFrame');
    frame.srcdoc = data.email.html;
    frame.onload = () => {
      const doc = frame.contentDocument;
      if (doc) frame.style.height = `${doc.documentElement.scrollHeight + 24}px`;
    };
  } catch (err) {
    toast(err.message, true);
  }
}

/* The planted issues reported under one criterion, caught and missed.
 *
 * Six grids carry these. They are the primary signal on those rubrics -- more
 * than polish, more than writing quality -- so they sit inside the evidence
 * cell of the row that reports them rather than in a panel further down the
 * card, where a reviewer would read the mark first and the trap second.
 *
 * Caught and missed are always exhaustive together: the parser resolves every
 * issue into exactly one list, so "2 of 6" here means the same thing on every
 * card and can be compared across them. Missed is listed first, deliberately.
 * It is the shorter list on a strong submission and the one a reviewer is
 * actually looking for. */
function seededRow(row) {
  const caught = row.seeded_caught || [];
  const missed = row.seeded_missed || [];
  if (!caught.length && !missed.length) return '';
  const chips = (keys, cls, mark) => keys.map((k) =>
    `<span class="seeded-chip ${cls}">${mark} ${esc(seededLabel(k))}</span>`).join('');
  return `
    <div class="seeded-issues" title="Issues planted in this assessment's materials on purpose">
      ${chips(missed, 'seeded-missed', '✗')}${chips(caught, 'seeded-caught', '✓')}
    </div>`;
}

/* An issue key rendered for a human.
 *
 * The pack's own label is the right text and it arrives on the rubric payload,
 * not on the verdict, so it is only available once the role page has been
 * opened. Falling back to a de-slugged key rather than waiting for it keeps
 * the chips readable on a cold card -- "say do gap" is not the pack's wording
 * but nobody is misled by it. */
const SEEDED_LABELS = new Map();
function seededLabel(key) {
  return SEEDED_LABELS.get(key) || String(key).replace(/_/g, ' ');
}
function rememberSeededLabels(seeded) {
  (seeded || []).forEach((issue) => {
    if (issue && issue.key && issue.label) SEEDED_LABELS.set(issue.key, issue.label);
  });
}

/* The scored grid, one row per criterion, grouped into its blocks.
 *
 * Every row shows the mark, the anchor it was marked against, and the points
 * it contributed -- score x weight / 5 -- so the total can be added up by hand
 * and any single row argued with on its own.
 *
 * Four blocks on most seats. Five grids carry a fifth, Background and
 * experience: the AI Strategist pair splits 40/40/6/7/7, the Social Media and
 * Marketing Intern grid splits 55/10/10/13/12, and the six seats added on
 * 2026-08-31 each open it at 10, rather than 70/10/10/10. Nothing here is
 * hard-coded to any of those shapes -- the blocks, their labels and their
 * point totals all arrive on the verdict -- so a grid that states its own
 * split renders it without this function knowing which one it is. */
function gridTable(ev) {
  if (!Array.isArray(ev?.grid) || !ev.grid.length) return legacyMatrixTable(ev);

  const blocks = Array.isArray(ev.blocks) && ev.blocks.length
    ? ev.blocks
    : [{ key: 'all', label: 'Criteria', points: 100,
         criteria: ev.grid.map((r) => r.key) }];

  const body = blocks.map((block) => {
    const rows = ev.grid.filter((r) => block.criteria.includes(r.key));
    if (!rows.length) return '';
    return `
      <tr class="grid-block-row">
        <th colspan="4">${esc(block.label)}
          <span class="matrix-weight">${
            block.earned == null ? '—' : fmtScore(block.earned)} / ${block.points}</span>
        </th>
      </tr>
      ${rows.map((row) => {
        const marked = typeof row.score === 'number';
        return `
          <tr>
            <th scope="row">
              ${esc(row.label)}
              <span class="matrix-weight">wt ${row.weight}</span>
            </th>
            <td class="num">
              <span class="matrix-score ${marked ? ratingClass(row.score) : 'score-none'}"
                    ${row.anchor ? `title="${esc(row.anchor)}"` : ''}>${
                marked ? row.score : '—'}</span>
            </td>
            <td class="num dim points">${row.points == null ? '—'
              : `${fmtScore(row.points)}<span class="of">/${row.max_points}</span>`}</td>
            <td class="matrix-evidence">${
              row.evidence ? esc(row.evidence)
                : '<span class="dim">No evidence given</span>'}${seededRow(row)}</td>
          </tr>`;
      }).join('')}`;
  }).join('');

  /* The renormalisation, spelled out with its consequence rather than as a
   * footnote. The old wording was accurate and read as housekeeping: "the
   * marked rows renormalised to 100" does not tell a recruiter that ONE row
   * marked 5 renormalises to exactly 100.0 — the same headline a perfect full
   * grid produces, from a candidate nobody graded. So this says what fraction
   * was judged, names the rows that were not, and says what the system does
   * about it, because a warning nothing acts on is a warning people learn to
   * scroll past. */
  const missed = (ev.grid_unmarked || []).length
    ? ev.grid_unmarked
    : ev.grid.filter((r) => r.score == null).map((r) => r.key);
  const labelFor = (key) =>
    (ev.grid.find((r) => r.key === key) || {}).label || key;
  const incomplete = isProvisional(ev)
    ? `<p class="matrix-note warn"><b>Partly graded — this total is not
       comparable.</b> The AI marked ${
         typeof ev.grid_marked === 'number' ? ev.grid_marked : ev.grid.length - missed.length
       } of ${
         typeof ev.grid_of === 'number' ? ev.grid_of : ev.grid.length
       } criteria${
         typeof ev.grid_coverage === 'number'
           ? `, ${Math.round(ev.grid_coverage * 100)}% of the rubric's weight`
           : ''} and the total above is those rows scaled up to 100 —
       so a grid with one row left at 5 reads as 100.0, exactly like a full one.
       ${missed.length ? `Never marked: ${
         missed.map((k) => esc(labelFor(k))).join(', ')}.` : ''}
       This usually means the grader's JSON ran past its output budget, not
       that anything was wrong with the submission. They are held off
       shortlists until someone re-grades them — use Re-evaluate below.</p>`
    : '';

  /* The one mark the system overrules, so it says so rather than showing a 3
   * a reviewer would read as something the grader found in a CV. Only ever
   * upward, only when there was genuinely no CV to read. */
  const floored = ev.background_floored;
  const flooredNote = floored
    ? `<p class="matrix-note warn"><b>Experience scored at its anchor, not from
       a CV.</b> No CV text could be read for this candidate, so the
       ${esc(floored.key)} row is held at ${floored.now} — the rubric's own
       mark for absent information — rather than the ${floored.was} the grader
       gave it. Roughly two in five CV links never extract (a private file, a
       profile page, a scan), and that is not the candidate's doing. Extract
       the CV and re-grade to have this row actually judged.</p>`
    : '';

  return `
    <div class="drawer-section">
      <h3>Scoring grid ${ev.grid_unit ? `<span class="dim">· ${esc(ev.grid_unit)}${
        ev.grid_tier ? `, ${esc(ev.grid_tier)} tier` : ''}</span>` : ''}</h3>
      <table class="matrix grid">
        <thead>
          <tr><th scope="col">Criterion</th>
              <th scope="col" class="num">1–5</th>
              <th scope="col" class="num">Points</th>
              <th scope="col">What earned it</th></tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
      <p class="matrix-note">${blendNote(ev)}</p>
      ${flooredNote}
      ${incomplete}
    </div>`;
}

/* How the rows below add up to the score above.
 *
 * These stopped being the same number on 2026-08-14-c, when the CV started
 * carrying part of the final score. The rows still sum to the rubric total; the
 * score is a share of that plus the CV's mark, so a reviewer adding the column
 * up by hand lands well away from the headline and concludes the table is
 * broken. Spelling the arithmetic out is the whole job of this line.
 *
 * Since 2026-08-15 the share is the seat's own -- 25% on a full-stack build,
 * 60% on Customer Success -- so the sentence says "for this seat". Two cards
 * with the same rubric total and the same CV mark can now legitimately show
 * different final scores, and a reviewer who reads the split as a company-wide
 * rule will file that as a bug.
 *
 * Verdicts graded before any of it carry no `cv_weight`, and for them the old
 * sentence is still exactly true, so they keep it. */
function blendNote(ev) {
  const band = ev.auto_failed ? '' : ` (${esc(bandRange(ev.score))})`;
  const tail = `${esc(recLabel(ev))}${band}.`;
  const weight = Number(ev.cv_weight) || 0;

  /* Some grids score the track record as a row IN the table above rather than
   * as a second document blended in afterwards, so their cards carry no CV
   * weight. Left to the branch below, that reads as "experience was not scored
   * on this seat" -- the exact opposite of the truth. Say which arrangement is
   * in force before saying the total, and take the row's worth from the block
   * rather than naming a number: it is 40 points on the AI Strategist pair,
   * where experience is the single heaviest row, and 10 on the intern seat,
   * where it is deliberately the lightest. */
  const bg = (ev.blocks || []).find((b) => b.key === 'background');
  if (bg && !weight) {
    return `Each row is score × weight ÷ 5. Total ${fmtScore(ev.score)} of 100 —
      ${tail} Experience is scored <b>inside</b> this grid, in the
      ${esc(bg.label)} row worth ${bg.points} points, not blended in afterwards
      — so there is no separate CV score on this seat and nothing is forfeited
      when a CV cannot be read.`;
  }

  if (!weight) {
    return `Each row is score × weight ÷ 5. Total ${fmtScore(ev.score)} of 100 — ${tail}`;
  }

  const rubric = typeof ev.rubric_score === 'number' ? ev.rubric_score : ev.score;
  const rubricPct = Math.round((1 - weight) * 100);
  const cvPct = Math.round(weight * 100);
  const cv = ev.cv_assessment;
  const split = `This seat splits ${rubricPct}% assessment to ${cvPct}% experience${
    ev.cv_weight_source === 'default'
      ? ', the fallback split — nobody has weighted this seat yet' : ''}.`;

  if (ev.cv_applied && cv && typeof cv.score === 'number') {
    return `Each row is score × weight ÷ 5, summing to a rubric total of
      <b>${fmtScore(rubric)}</b>. ${split} ${fmtScore(rubric)} × ${rubricPct}% +
      ${fmtScore(cv.score)} × ${cvPct}% = <b>${fmtScore(ev.score)}</b> — ${tail}`;
  }

  if (ev.cv_unmarked) {
    return `Each row is score × weight ÷ 5, summing to a rubric total of
      <b>${fmtScore(rubric)}</b>. ${split} The grader returned no marks for this
      candidate's CV, which is our failure and not theirs, so nothing was
      forfeited and the score is the assessment alone —
      <b>${fmtScore(ev.score)}</b>. Re-grade to have the CV judged. ${tail}`;
  }

  return `Each row is score × weight ÷ 5, summing to a rubric total of
    <b>${fmtScore(rubric)}</b>. ${split} This candidate has no readable CV, so the
    ${cvPct}% it carries was forfeited: ${fmtScore(rubric)} × ${rubricPct}% =
    <b>${fmtScore(ev.score)}</b>, against a ceiling of ${rubricPct} — ${tail}`;
}

/* The CV's own marks -- the other side of the score since 2026-08-14-c, and
 * since 2026-08-15 a share that differs by seat.
 *
 * Rendered as its own table beside the grid rather than folded into it,
 * because the two are marked from different documents against different
 * anchors, and a reviewer arguing about a score needs to see which half
 * produced it. A 63 built from a weak answer and a strong CV is a different
 * candidate from a 63 built the other way round.
 *
 * The missing case gets a row of its own rather than an empty table. 38% of
 * candidates land there, none of them through anything they did, and the
 * forfeited points are the single most likely thing on this page to be
 * disputed -- so it says what happened and what it cost. */
function cvTable(ev) {
  const weight = Number(ev?.cv_weight) || 0;
  if (!weight) return '';                       // graded before the CV scored
  const cv = ev.cv_assessment;
  if (!cv || !Array.isArray(cv.criteria) || !cv.criteria.length) return '';

  const pct = Math.round(weight * 100);

  /* Two ways to end up unscored, and they are opposite failures. One is a CV
     we could not read; the other is a CV we read and the grader skipped. The
     second costs the candidate nothing and must not be dressed up as the
     first, or a reviewer reads "no readable CV" on a candidate whose CV is
     sitting one click away in the drawer. */
  if (!cv.scored && ev.cv_unmarked) {
    return `
      <div class="drawer-section">
        <h3>CV score <span class="dim">· not marked</span></h3>
        <p class="matrix-note warn">
          <b>The grader did not mark this CV.</b> The CV was read successfully
          and sent with the submission, and the model returned no marks for it —
          our failure, not the candidate's. Rather than forfeit the ${pct}% it
          carries, this candidate is scored on the assessment alone
          (<b>${fmtScore(ev.rubric_score)}</b>). Re-grade to have the CV judged;
          the score will move once it is.
        </p>
      </div>`;
  }

  if (!cv.scored) {
    return `
      <div class="drawer-section">
        <h3>CV score <span class="dim">· ${pct}% of the final score for this seat</span></h3>
        <p class="matrix-note">
          No readable CV. The linked file was private, a profile page, or a
          scan with no text layer — this is a gap in our extraction, not
          something the candidate did. Under the
          <code>${esc(ev.cv_missing_policy || 'forfeit')}</code> policy the
          ${pct}% it carries was forfeited, capping this candidate at
          ${Math.round((1 - weight) * 100)} however good the answer.
        </p>
      </div>`;
  }

  const rows = cv.criteria.map((row) => {
    const marked = typeof row.score === 'number';
    return `
      <tr>
        <th scope="row">${esc(row.label || row.key)}</th>
        <td class="num">
          <span class="matrix-score ${marked ? ratingClass(row.score) : 'score-none'}">${
            marked ? row.score : '—'}</span>
        </td>
        <td class="matrix-evidence">${
          row.evidence ? esc(row.evidence)
            : '<span class="dim">No evidence given</span>'}</td>
      </tr>`;
  }).join('');

  return `
    <div class="drawer-section">
      <h3>CV score <span class="dim">· ${pct}% of the final score for this seat</span></h3>
      <table class="matrix grid">
        <thead>
          <tr><th scope="col">Criterion</th>
              <th scope="col" class="num">1–5</th>
              <th scope="col">What earned it</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="matrix-note">
        Marked from the CV alone, against this seat. The three are equally
        weighted and averaged: <b>${fmtScore(cv.score)} of 100</b>, contributing
        ${fmtScore(cv.score * weight)} points of the final
        ${fmtScore(ev.score)}. These marks never touch the grid above — the
        answer is scored on its own.
      </p>
    </div>`;
}

/* Evaluations graded before the pack rewrite carry the old five-category
 * `matrix` with 0-100 marks. They keep their score and brief and render in
 * their own shape rather than being silently redrawn as if they had been
 * marked against anchors they never saw. */
function legacyMatrixTable(ev) {
  if (!Array.isArray(ev?.matrix) || !ev.matrix.length) return '';
  const rows = ev.matrix.map((row) => {
    const marked = typeof row.score === 'number';
    return `
      <tr>
        <th scope="row">${esc(row.label)}
          <span class="matrix-weight">${Math.round(row.weight * 100)}%</span></th>
        <td class="num"><span class="matrix-score ${
          marked ? scoreClass(row.score) : 'score-none'}">${
          marked ? row.score : '—'}</span></td>
        <td class="matrix-evidence">${
          row.evidence ? esc(row.evidence) : '<span class="dim">No evidence given</span>'}</td>
      </tr>`;
  }).join('');
  return `
    <div class="drawer-section">
      <h3>Evaluation matrix <span class="dim">· pre-rubric-pack</span></h3>
      <table class="matrix"><tbody>${rows}</tbody></table>
      <p class="matrix-note">Marked against the old five-category matrix, before
        the rubric pack. Re-grade this role to score it against the family grid.</p>
    </div>`;
}

/* Triage, auto-fails and the fraud log: the parts of the pack that sit beside
 * the score rather than inside it. */
function findingsBlock(ev) {
  if (!ev) return '';
  const t = ev.triage;
  const list = (items, key, cls, title) => (items?.length ? `
    <div class="rubric-block ${cls}">
      <h3>${title}</h3>
      <ul>${items.map((f) => `<li><b>${esc(f[key])}</b>${
        f.evidence ? ` — ${esc(f.evidence)}` : ''}</li>`).join('')}</ul>
    </div>` : '');

  /* What the CV did or did not corroborate. Shown only when there was a CV to
     read: "no_cv" is the answer for two candidates in five, and a row saying
     so on 40% of the list would train reviewers to skim past the 1% that says
     something.

     This is the consistency signal and it still carries no points, which is
     worth saying out loud now that the CV carries a scored share somewhere else
     on this page -- the two are separate, and a reviewer who conflates them
     will read "consistent" as the thing that moved the score. */
  const cv = ev.cv_check;
  const scored = Number(ev.cv_weight) > 0;
  const cvRow = (cv && cv.verdict && cv.verdict !== 'no_cv') ? `
    <div class="rubric-block ${cv.verdict === 'contradicted' ? 'rubric-fraud' : ''}">
      <h3>CV consistency check — ${scored
        ? 'no points; separate from the CV score above'
        : 'background only, worth no points'}</h3>
      <ul><li><b>${cv.verdict === 'contradicted'
        ? 'CV contradicts the submission'
        : 'CV consistent with the submission'}</b>${
        cv.note ? ` — ${esc(cv.note)}` : ''}</li></ul>
    </div>` : '';

  /* Auto-fails the grader hedged. Shown, because a reviewer may want to check
     one by hand, but visibly separated from the ones that acted: an auto-fail
     ends a candidacy and a guess must never be read as having done so. */
  const disputedRow = ev.disputed_auto_fails?.length ? `
    <div class="rubric-block">
      <h3>Unproven auto-fail claims — not applied</h3>
      <ul>${ev.disputed_auto_fails.map((f) => `<li><b>${esc(f.rule)}</b>${
        f.evidence ? ` — ${esc(f.evidence)}` : ''}</li>`).join('')}</ul>
      <p class="matrix-note">The grader hedged these, so they were not acted on
        and did not affect the band. Worth checking by hand if one looks real.</p>
    </div>` : '';

  /* The three extracted facts that change no points.
   *
   * Grouped together and labelled as unscored, because that is the single
   * most important thing about them and the easiest for a reader to forget:
   * each one looks like a judgement and none of them moved the number. The
   * compensation note in particular is a policy breach by us, not by the
   * candidate -- every one of these assessments tells them not to state it,
   * so it is surfaced for the reviewer and never held against the person who
   * volunteered it.
   *
   * Rendered only when there is something to say. A candidate who stated an
   * expectation and tripped neither flag shows one line, which is the normal
   * case. */
  const consistency = ev.consistency || {};
  const compPolicy = ev.compensation_policy || {};
  const notes = [];
  if (consistency.raised) {
    notes.push(`<li class="seeded-flag"><b>Video contradicts the written submission</b>${
      consistency.note ? ` — ${esc(consistency.note)}` : ''}
      <i class="dim">Flagged for a reviewer, not averaged into the marks.</i></li>`);
  }
  if (compPolicy.raised) {
    notes.push(`<li class="seeded-flag"><b>Candidate stated current or recent compensation</b>${
      compPolicy.note ? ` — ${esc(compPolicy.note)}` : ''}
      <i class="dim">We ask for expectations only. A policy note, never a mark
      against the candidate.</i></li>`);
  }
  if (ev.salary_expectation) {
    notes.push(`<li>Salary expectation stated: <b>${esc(ev.salary_expectation)}</b>
      <i class="dim">Extracted for the pipeline. Kept out of every score.</i></li>`);
  }
  const notesRow = notes.length ? `
    <div class="rubric-block">
      <h3>Noted, unscored</h3>
      <ul>${notes.join('')}</ul>
    </div>` : '';

  if (!t && !ev.auto_fails?.length && !ev.fraud_tells?.length && !cvRow
      && !disputedRow && !notesRow) return '';

  return `
    <div class="drawer-section">
      ${t ? `
        <h3>Triage <span class="dim">· ${t.passed} of ${t.of} · ${
          esc(t.route_label)}</span></h3>
        <ul class="triage-checks">${(t.checks || []).map((c) => `
          <li class="${c.pass === true ? 'yes' : c.pass === false ? 'no' : 'unknown'}">
            <span class="tick">${c.pass === true ? '✓' : c.pass === false ? '✗' : '?'}</span>
            <span>${esc(c.label)}${c.note ? ` <i class="dim">${esc(c.note)}</i>` : ''}</span>
          </li>`).join('')}
        </ul>` : ''}
      ${list(ev.auto_fails, 'rule', 'rubric-fails-hit',
             'Auto-fails tripped — not scored, whatever the grid totalled')}
      ${list(ev.fraud_tells, 'tell', 'rubric-fraud',
             'Fraud tells — route to the fraud log, not to a score')}
      ${disputedRow}
      ${cvRow}
      ${notesRow}
    </div>`;
}

/* The GIA read: a note for the interviewer, worth nothing on the scoreboard. */
function giaRead(ev) {
  const g = ev?.gia;
  if (!g || (!g.read && !Object.keys(g.scales || {}).length)) return '';
  return `
    <div class="drawer-section">
      <h3>GIA proxy read <span class="dim">· changes no points</span></h3>
      ${g.primary?.length ? `<div class="gia-scales">${
        g.primary.map((s) => `<span class="scale primary">${esc(s)}</span>`).join('')}${
        (g.secondary || []).map((s) => `<span class="scale">${esc(s)}</span>`).join('')}
      </div>` : ''}
      ${g.read ? `<p class="verdict-brief">${esc(g.read)}</p>` : ''}
      ${Object.keys(g.scales || {}).length ? `<dl class="meta-grid">${
        Object.entries(g.scales).map(([k, v]) =>
          `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    </div>`;
}

/* Grade this one candidate, now, regardless of which queue they are in.
 *
 * The role-level "Grade pending" button only reaches the pending queue, so the
 * candidate a reviewer is actually looking at is often the one it will never
 * touch: auto-rejected for a missing artefact, or already scored and worth a
 * re-run after a rubric edit. This is the on-demand door for exactly that.
 *
 * Hidden only when there is genuinely nothing to mark -- no answer text -- or
 * when no evaluator is configured to mark it with. */
function evaluateButton(c) {
  if (!state.evaluatorConfigured) return '';
  if (!(c.submission_markdown || '').trim()) return '';
  const label = c.evaluation ? 'Re-evaluate' : 'Evaluate now';
  return `<button class="btn btn-primary" data-evaluate>${label}</button>`;
}

/* Moving one candidate along the pipeline, from the card you are already
 * reading. This is where a stage is set in practice: the decision is made while
 * looking at the score and the grid that produced it, not from a board.
 *
 * One form serves all three moves rather than three dialogs. The fields are
 * only meaningful to some of them -- a hire does not need an interview time --
 * but sending them together means marking an interviewed candidate hired keeps
 * the date they were seen on, which is the fact you want back later when asking
 * how long the process takes. */
/* Which manager owns this interview, mirroring candidate_mail.resolve_manager()
 * so the field is pre-filled with the same person the send would pick.
 *
 * The server decides for real -- this is only what the box shows before anyone
 * clicks. Where it cannot tell (three managers, an interviewer name matching
 * none of them) it picks nobody and the manager says who they are, which is
 * the honest answer: putting a stranger's calendar in front of a candidate is
 * a mistake they discover in the meeting. */
function managerFor(c, interviewer) {
  const managers = (c.managers || []).filter((m) => m.email);
  if (!managers.length) return null;
  const hint = String(interviewer || c.pipeline?.interviewer || '').trim().toLowerCase();
  if (hint) {
    const exact = managers.find((m) => [m.email, m.name]
      .map((v) => String(v || '').toLowerCase()).includes(hint));
    if (exact) return exact;
    const first = managers.filter((m) => String(m.name || '').toLowerCase()
      .split(' ')[0] === hint.split(' ')[0]);
    if (first.length === 1) return first[0];
  }
  return managers.length === 1 ? managers[0] : null;
}

/* What the candidate has already been told, and when. A rejection sent twice
 * is worse than one sent late, and this is the line that stops it. */
function mailHistory(p) {
  const sends = (p.emails || []).slice().reverse();
  if (!sends.length) return '';
  return `
      <ul class="stage-mails">${sends.map((m) => `
        <li class="${m.ok ? '' : 'is-failed'}">
          <span class="badge ${m.ok ? (STAGE_CLASS[m.stage] || 'badge-queue') : 'badge-stage-rejected'}">${
            m.ok ? esc(STAGE_LABEL[m.stage] || m.stage) : 'Send failed'} email</span>
          <span class="dim">${esc(shortDate(m.at))} · ${esc(m.to || '')}</span>
          ${m.error ? `<span class="warn">${esc(m.error)}</span>` : ''}
        </li>`).join('')}
      </ul>`;
}

function pipelineSection(c) {
  const p = c.pipeline || {};
  const stage = stageOf(c);
  const history = (p.history || []).slice().reverse();
  const managers = (c.managers || []).filter((m) => m.email);
  const owner = managerFor(c);

  const label = stage
    ? `<span class="badge ${STAGE_CLASS[stage]}">${esc(STAGE_LABEL[stage])}</span>`
    : '<span class="dim">Not in the pipeline</span>';

  const note = stage === 'rejected' ? (p.reason || p.note || '') : (p.note || '');

  // The manager picker is only shown where there is a choice to make. One
  // manager on the role is not a decision, and a select with a single option
  // is a click that teaches the reader nothing. It is here for the signature
  // on a rejection now, not for a calendar.
  const managerField = managers.length > 1 ? `
        <label>Signed by
          <select id="stageManager">
            ${managers.map((m) => `<option value="${esc(m.email)}"${
              owner && owner.email === m.email ? ' selected' : ''}>${
              esc(m.name)}</option>`).join('')}
          </select>
        </label>` : '';

  // What the manager did, read-only. The interview belongs to them, so this
  // page reports it rather than offering to change it -- and it separates
  // "they were invited" from "the invitation was actually sent", because a
  // candidate can sit at this stage with an empty inbox after a failed send.
  const invite = (p.emails || []).filter((m) => m.stage === 'interview' && m.ok).pop();
  const inviteBlock = stage === 'interview' ? `
      <div class="stage-readout">
        <div><span class="dim">Invited by</span> ${esc(p.by || p.interviewer || 'a hiring manager')}</div>
        ${p.interview_at
          ? `<div><span class="dim">Suggested time</span> ${esc(fmtWhen(p.interview_at))}</div>` : ''}
        <div><span class="dim">Invitation</span> ${invite
          ? `sent ${esc(shortDate(invite.at))} to ${esc(invite.to || '')}`
          : '<b>not sent</b> — the candidate has no booking link yet'}</div>
      </div>` : '';

  return `
    <div class="drawer-section">
      <h3>Hiring pipeline ${label}</h3>
      <p class="matrix-note">
        Where this candidate is after the assessment. Nothing here re-marks the
        submission — the score, the grid and the status above are left exactly
        as they are, so a hire or a rejection can always be read back against
        what the assessment predicted.
      </p>
      ${inviteBlock}
      <div class="stage-form">
        ${managerField}
        <label class="wide">${stage === 'rejected' ? 'Reason' : 'Note'}
          <span class="field-hint">Internal — never leaves the dashboard</span>
          <input type="text" id="stageNote" value="${esc(note)}"
                 placeholder="Anything the next reader needs">
        </label>
        <label class="wide">Message to the candidate
          <span class="field-hint">Optional — appears in the email they receive</span>
          <input type="text" id="stageEmailNote"
                 placeholder="Really enjoyed your take on the funnel question.">
        </label>
      </div>

      <!-- Moving and telling are two clicks, not one.
           The buttons on the first row change the board and send nothing. The
           second row is the only thing on this page that puts a message in a
           candidate's inbox, and it sits next to Preview so the order of work
           is the obvious one: move, read the message, send it.

           No interview in either row. The server refuses it from here, and a
           button that 403s is worse than no button -- see the note below,
           which says where the invitation is actually written. -->
      <div class="drawer-actions">
        <button class="btn btn-primary" data-stage="hired">Mark hired</button>
        <button class="btn" data-stage="rejected">Mark rejected</button>
        ${stage ? '<button class="btn btn-ghost" data-stage="">Remove from pipeline</button>' : ''}
      </div>

      ${state.mail.interview_locked ? `
        <p class="field-hint stage-locked">
          ${esc(state.mail.interview_locked_reason
                || 'Interviews are invited by the hiring manager, from the review link in their shortlist email.')}
        </p>` : ''}

      <div class="stage-send">
        <p class="field-hint">
          ${state.mail.enabled === false
            ? 'Candidate emails are switched off (PIPELINE_EMAILS_ENABLED=0), so this sends nothing.'
            : state.mail.auto
              ? 'Marking someone rejected already emails them. This is here to read the message, or to send it again after a change.'
              : 'Nothing above emails anyone — the move is recorded and stops there. Preview the message, then send it.'}
          Nothing is ever sent for a hire or a removal.
        </p>
        <div class="drawer-actions">
          <button class="btn btn-ghost" data-mailpreview="rejected">Preview rejection</button>
          <button class="btn" data-mailsend="rejected">Send rejection</button>
        </div>
      </div>
      ${mailHistory(p)}
      ${history.length ? `
        <ul class="stage-history">${history.map((h) => `
          <li>
            <span class="badge ${STAGE_CLASS[h.stage] || 'badge-queue'}">${
              esc(STAGE_LABEL[h.stage] || 'Returned to shortlist')}</span>
            <span class="dim">${esc(shortDate(h.at))}${
              h.interview_at ? ` · interview ${esc(fmtWhen(h.interview_at))}` : ''}${
              h.interviewer ? ` · ${esc(h.interviewer)}` : ''}</span>
            ${h.reason || h.note ? `<span>${esc(h.reason || h.note)}</span>` : ''}
          </li>`).join('')}
        </ul>` : ''}
    </div>`;
}

function drawerContent(c) {
  const ev = c.evaluation;
  const status = c.decision?.status || 'unknown';
  const reason = REASON_LABEL[c.decision?.reason] || c.decision?.reason || '—';
  const link = (url, label) => url
    ? `<a class="link" href="${esc(url)}" target="_blank" rel="noopener">${label}</a>`
    : '<span class="dim">Not submitted</span>';

  const verdict = ev ? `
    <div class="verdict-card${ev.auto_failed ? ' is-autofail' : ''}">
      <div class="verdict-score ${scoreClass(ev.score)}">${fmtScore(ev.score)}${
        provisionalMark(ev)}</div>
      <div>
        <span class="badge ${recClass(recLabel(ev))}">${esc(recLabel(ev))}</span>
        ${ev.triage ? `<span class="badge badge-route">triage ${
          ev.triage.passed}/${ev.triage.of}</span>` : ''}
        ${ev.grid_source === 'derived'
          ? '<span class="badge badge-derived">derived grid</span>' : ''}
        ${withoutMark(ev)}
        <p class="verdict-brief">${esc(ev.brief)}</p>
      </div>
    </div>` : `
    <div class="verdict-card">
      <div class="verdict-score score-none">—</div>
      <div><span class="badge ${STATUS_CLASS[status] || ''}">${
        esc(STATUS_LABEL[status] || status)}</span>
        <p class="verdict-brief">${esc(reason)}</p></div>
    </div>`;

  return `
    ${verdict}
    ${pipelineSection(c)}
    ${gridTable(ev)}
    ${cvTable(ev)}
    ${findingsBlock(ev)}
    ${giaRead(ev)}
    <div class="drawer-section">
      <h3>Details</h3>
      <dl class="meta-grid">
        <dt>Status</dt><dd>${esc(STATUS_LABEL[status] || status)} — ${esc(reason)}</dd>
        ${portalQueue(c) ? `<dt>Portal queue</dt><dd>${esc(portalQueue(c))}${
          c.screener_rating ? ` — rated ${esc(c.screener_rating)}` : ''}</dd>` : ''}
        <dt>Video</dt><dd>${link(c.video_link, 'Open video')}</dd>
        <dt>Resume</dt><dd>${link(c.resume_link, 'Open resume')}</dd>
        <dt>Submitted</dt><dd>${esc(c.submitted_at || '—')}</dd>
        <dt>Assignment</dt><dd>${esc(c.assignment_name || '—')}</dd>
        ${ev ? `<dt>Marked against</dt><dd>${
          ev.grid_unit ? `${esc(ev.grid_unit)} grid` : 'legacy matrix'}${
          // Only the AI Strategist family has more than one tier, so this is
          // absent everywhere else. Where it is present it is the single most
          // important thing in this list: the background row is worth 40 of
          // the 100 and its anchors are the whole difference between the two.
          ev.grid_tier ? ` · <b>${esc(ev.grid_tier)} tier</b>` : ''}${
          ev.grid_version ? ` · <code>${esc(ev.grid_version)}</code>` : ''}${
          ev.pack_version ? ` · pack ${esc(ev.pack_version)}` : ''}${
          ev.grid_source ? ` · ${esc(ev.grid_source)}` : ''}</dd>
        <dt>Model</dt><dd>${esc(ev.model)}${
          ev.answer_truncated ? ' · answer truncated' : ''}</dd>` : ''}
        <dt>On portal</dt><dd>${link(c.admin_url, 'Open in portal')}</dd>
      </dl>
      <div class="drawer-actions">
        ${evaluateButton(c)}
        ${status === 'rejected'
          ? '<button class="btn" data-reconsider>Move to pending</button>'
          : '<button class="btn" data-reject>Move to rejected</button>'}
      </div>
    </div>
    <div class="drawer-section">
      <h3>Submission</h3>
      <div class="answer">${esc(c.submission_markdown || 'No answer text.')}</div>
    </div>`;
}

/* One model call, one candidate. Slow enough (10-30s) that the button has to
 * say so, and the drawer is reopened afterwards rather than patched, so the
 * grid, triage and fraud findings all arrive together from the server. */
async function evaluateOne(submissionId, btn) {
  const previous = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Evaluating…';
  try {
    const result = await api('/api/evaluations/grade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId }),
    });
    toast(result.message);
    await openDrawer(submissionId);       // the new score, in place
    if (state.activeRoleId) await openRole(state.activeRoleId, false);
    await loadRoles();                    // role tallies moved
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
    btn.textContent = previous;
  }
}

async function setDecision(submissionId, status) {
  try {
    const result = await api('/api/evaluations/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submission_id: submissionId, status }),
    });
    toast(result.message);
    closeDrawer();
    await openRole(state.activeRoleId);
    await loadRoles();
  } catch (err) {
    toast(err.message, true);
  }
}

function closeDrawer() { $('drawer').hidden = true; }

/* --- portal sync ------------------------------------------------------ */
