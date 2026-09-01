/*
 * The role cards and the counters above them -- the first thing on screen.
 *
 * Separate from 03-account-view.js, which loads the roles, because this is
 * what draws them; the accounts panel sits between the two in load order and
 * both halves have to keep their place.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

function renderStats() {
  const t = { total: 0, scored: 0, pending: 0, rejected: 0, in_progress: 0 };
  for (const role of state.roles) {
    for (const key of Object.keys(t)) t[key] += role.counts[key] || 0;
  }
  // The pipeline totals sit in the same row rather than a second one: the
  // funnel is one story, and a candidate's whole journey — submitted, scored,
  // seen, hired — should be readable left to right without scrolling.
  const p = state.pipeline.counts;
  // Labels are short enough that all eight sit on one line, because read as
  // one line they are the funnel. The long version is the tooltip.
  const cards = [
    ['Submitted', t.scored + t.pending + t.rejected, true, 'Assessments actually handed in'],
    ['Scored', t.scored, false, 'Marked against the grid'],
    ['Pending', t.pending, false, 'Submitted, waiting on AI evaluation'],
    ['Missing artefact', t.rejected, false, 'Rejected for arriving without a video or a resume'],
    ['Not submitted', t.in_progress, false, 'Started but never handed in'],
    ['Interview', p.interview || 0, false, 'Booked in for an interview'],
    ['Hired', p.hired || 0, true, 'Offers accepted'],
    ['Rejected', p.rejected || 0, false, 'Turned down after being seen'],
  ];
  $('stats').innerHTML = cards.map(([label, value, accent, title]) => `
    <div class="stat${accent && value ? ' is-accent' : ''}" title="${esc(title)}">
      <div class="stat-value">${value.toLocaleString()}</div>
      <div class="stat-label">${esc(label)}</div>
    </div>`).join('');
}

/* A card is a role, or one posting's half of one.
 *
 * Two cards can share a job id -- the AI Strategist pair sits one assignment
 * at two seniorities -- so anything that has to find "the card that is open"
 * matches on both. Roles with a single grid carry no tier and both sides of
 * the comparison are null, which is why this works unchanged for the other
 * twenty-odd seats. */
const roleCard = (id, tier = null) => state.roles.find(
  (r) => r.id === id && (r.tier || null) === (tier || null));

const activeCard = () => roleCard(state.activeRoleId, state.activeTier);

// `?tier=` for a GET, or nothing at all. Kept as one function so a route that
// forgets it is a bug in one place rather than a card quietly showing the
// other posting's candidates.
const tierParam = (prefix = '?') =>
  (state.activeTier ? `${prefix}tier=${encodeURIComponent(state.activeTier)}` : '');

function visibleRoles() {
  const term = $('roleSearch').value.trim().toLowerCase();
  const filter = $('roleFilter').value;
  return state.roles.filter((role) => {
    if (filter === 'active' && role.counts.total === 0) return false;
    if (filter === 'published' && !role.published) return false;
    if (term && !`${role.title} ${role.slug}`.toLowerCase().includes(term)) return false;
    return true;
  });
}

function renderRoles() {
  const roles = visibleRoles();
  // applyAccountView() owns this line when a manager owns no roles at all --
  // "no roles match these filters" would be a lie about a filter they never
  // touched.
  const stranded = !state.isAdmin && state.roles.length === 0;
  $('roleEmpty').hidden = !stranded && roles.length > 0;

  $('roleGrid').innerHTML = roles.map((role) => {
    const c = role.counts;
    const total = c.total || 0;
    // Widths as percentages of the role's own total, so every bar fills.
    const seg = (n, cls) => (n > 0
      ? `<span class="${cls}" style="width:${(n / total * 100).toFixed(2)}%"></span>` : '');
    const legend = [
      ['scored', c.scored, 'Scored'],
      ['pending', c.pending, 'Pending'],
      ['rejected', c.rejected, 'Rejected'],
      ['in_progress', c.in_progress, 'Not submitted'],
    ].filter(([, n]) => n > 0)
     .map(([key, n, label]) =>
       `<span><i class="seg-${key}"></i>${n.toLocaleString()} ${esc(label.toLowerCase())}</span>`)
     .join('');

    // Where this role's people got to after the score. Counted outside the bar
    // rather than inside it: an interviewee is still a scored submission, and
    // a bar whose segments stopped summing to the total would read as a bug.
    const stages = role.pipeline || {};
    const chips = [['interview', 'booked'], ['hired', 'hired'], ['rejected', 'rejected']]
      .filter(([key]) => stages[key] > 0)
      .map(([key, label]) =>
        `<span class="stage-chip stage-${key}">${stages[key]} ${label}</span>`)
      .join('');

    // Who owns the seat. Shown on the card rather than only inside the role,
    // because "which of these has nobody to send to" is a question about the
    // whole grid. A role with submissions and no manager says so in as many
    // words -- it can be fully graded and still be a dead end.
    const managers = role.managers || [];
    const owner = managers.length
      ? `<span class="stage-chip">${esc(managers[0].name)}${
          managers.length > 1 ? ` +${managers.length - 1}` : ''}</span>`
      : (total ? '<span class="stage-chip stage-rejected">no manager</span>' : '');
    const sent = role.shortlist_last
      ? `<span class="stage-chip stage-hired">sent ${esc(shortDate(role.shortlist_last.at))}</span>`
      : '';
    const ownerRow = owner + sent;

    // A tiered card names the posting, so the slug line has to say which
    // assignment it belongs to or two cards read as two unrelated seats. The
    // unresolved count sits here rather than inside the role, because it is
    // the caveat on the number above it: this many of these people are on this
    // card by fallback, not because anyone matched them to this posting.
    const isActive = role.id === state.activeRoleId
      && (role.tier || null) === (state.activeTier || null);
    const tierBits = role.tier
      ? ` · <b>${esc(role.tier)} tier</b>${
          role.unresolved ? ` · ${role.unresolved} unresolved` : ''}`
      : '';

    return `
      <button class="role-card${isActive ? ' is-active' : ''}"
              data-role="${role.id}" data-tier="${esc(role.tier || '')}" type="button">
        <div class="role-card-head">
          <div>
            <div class="role-name">${esc(role.title)}</div>
            <div class="role-sub">${esc(role.slug)}${role.published ? '' : ' · unpublished'}${
              role.rubric_source === 'pack' ? ' · <b>pack grid</b>'
              : role.rubric_source === 'derived' ? ' · derived grid'
              : ' · <i>no grid</i>'}${tierBits}</div>
          </div>
          <div class="role-total${total ? '' : ' is-zero'}">${total.toLocaleString()}</div>
        </div>
        ${total ? `<div class="role-bar">
          ${seg(c.scored, 'seg-scored')}${seg(c.pending, 'seg-pending')}
          ${seg(c.rejected, 'seg-rejected')}${seg(c.in_progress, 'seg-in_progress')}
        </div>
        <div class="role-legend">${legend}</div>`
        : '<div class="role-legend"><span>No submissions yet</span></div>'}
        ${chips ? `<div class="role-stages">${chips}</div>` : ''}
        ${ownerRow ? `<div class="role-stages">${ownerRow}</div>` : ''}
      </button>`;
  }).join('');

  for (const card of $('roleGrid').querySelectorAll('.role-card')) {
    card.addEventListener('click', () => openRole(
      Number(card.dataset.role), true, null, card.dataset.tier || null));
  }
}

/* --- rejected candidates, in two columns -------------------------------
 *
 * Left: who the assessment rejected and who has not heard about it.
 * Right: who has already had their rejection email.
 *
 * WHY IT IS TWO LISTS. They are the same list exactly once -- the first time.
 * After that they diverge, and a single list becomes actively dangerous:
 * twenty new people land beside two hundred who were mailed last month, "select
 * all" takes all two hundred and twenty, and two hundred people get a second
 * rejection out of a list that looked correct. There is no undo for that.
 *
 * Both columns come out of ONE request. /api/evaluations/rejected marks each
 * row `already_told` from the rejection ledger, and the split here is on that
 * flag -- so the two columns cannot disagree about where somebody is.
 *
 * Moving a candidate across sends nothing and un-sends nothing. Rightwards it
 * records "I have already written to this person" so the left list stops
 * offering them; leftwards it takes that back. The mail itself is still sent
 * from wherever you send it.
 */

// Ticked rows, per column. Emails rather than row indexes, so a tick survives
// a re-render and a change of role filter.