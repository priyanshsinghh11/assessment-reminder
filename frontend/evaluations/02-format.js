/*
 * Turning a stored value into what the reviewer reads: status and reason
 * labels, the score cell, the provisional and partial marks, recommendation
 * wording and the band a total falls in.
 *
 * Pure formatting. Nothing here fetches, and nothing here writes to the page.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

const STATUS_LABEL = {
  scored: 'Scored',
  pending: 'Pending',
  rejected: 'Rejected',
  in_progress: 'Not submitted',
};

const STATUS_CLASS = {
  scored: 'badge-scored',
  pending: 'badge-pending',
  rejected: 'badge-rejected',
  in_progress: 'badge-inprogress',
};

// The portal's own review queue, which is a different thing from our decision
// status above: it says what a human on the portal has done, not what we have.
// Only Pending Review is worth a badge -- "new" is the default queue and would
// mark nearly every row. These candidates were invisible here until the ingest
// started fetching that queue, so the badge is mostly there to say "this one
// is already sitting in someone's review pile" before you act on the score.
const PORTAL_QUEUE_LABEL = { pending: 'In portal review' };

const portalQueue = (c) =>
  PORTAL_QUEUE_LABEL[String(c.review_status || '').trim().toLowerCase()] || '';

// Why a candidate landed in the reject box, in words rather than a DB enum.
const REASON_LABEL = {
  missing_video: 'No video submitted',
  missing_resume: 'No resume submitted',
  missing_video_and_resume: 'No video or resume',
  manual_override: 'Set by hand',
  awaiting_evaluation: 'Awaiting AI evaluation',
  ai_evaluated: 'Evaluated by AI',
  not_submitted: 'Started but never submitted',
};

// Coloured on the pack's bands, not on a separate visual scale, so the colour
// and the word can never disagree: Best 85+, Better 75-84, Good 60-74, Okay
// below 60. Best and Better share the strong colour -- they are one side of the
// advance bar, and a second green would read as a second bar.
function scoreClass(score) {
  if (score == null) return 'score-none';
  if (score >= 75) return 'score-strong';
  if (score >= 60) return 'score-mid';
  return 'score-low';
}

/* Whether the AI stopped part-way through this candidate's rubric.
 *
 * `score_provisional` arrived with the coverage fields; `grid_complete` has
 * been stored since the grids went in, so verdicts marked before either field
 * existed still answer this correctly. */
const isProvisional = (ev) => !!ev
  && (ev.score_provisional === true || ev.grid_complete === false);

/* A partial grid is renormalised to 100 by the scorer, so nothing about the
 * NUMBER says it was built from part of the rubric -- one row marked 5 comes
 * out at exactly 100.0, the same figure a flawless full grid produces. The
 * number therefore never appears anywhere in this dashboard without this mark
 * beside it, and the mark is what a recruiter sorts, filters and re-grades on.
 *
 * Kept deliberately small and unmissable rather than a colour change: the score
 * colours already mean "how good", and overloading them with "how sure" is how
 * a recruiter reads a caveat as a band. */
/* The score column, or its absence.
 *
 * DRAWN FOR A HIRING MANAGER TOO, unless the server says otherwise. The number
 * is safe here in a way it is not in their inbox: the row it sits on opens a
 * drawer holding the grid that produced it, the anchors each mark was given
 * against, the brief and the CV read. A "78" you can open and disagree with is
 * the opposite of a "78" that decides the interview on its own. Turning it off
 * is `MANAGER_DASHBOARD_SCORES=0`, which withholds `evaluation` from the
 * payload as well -- see MANAGER_SUBMISSION_FIELDS.
 *
 * With it off, the column says nothing rather than "—", which would read as a
 * grading failure and send them asking why half the candidates are unmarked.
 * `grading_incomplete` still reaches them and still shows: it is the one fact
 * about the marking they need, because it is why somebody sits where they sit. */
function scoreCell(ev, row) {
  if (!state.scoresVisible) {
    return `<td class="num dim" title="Scores are the recruiting team's. `
      + `Rank and the work itself are what this page shows you.">${
        row?.grading_incomplete ? partialBadge() : '·'}</td>`;
  }
  return `<td class="num"><span class="score-cell ${scoreClass(ev?.score)}">${
    ev ? fmtScore(ev.score) : '—'}</span>${provisionalMark(ev)}</td>`;
}

/* The same mark provisionalMark() draws, for a reader who has no verdict
 * object to derive it from. */
function partialBadge() {
  return `<span class="provisional-mark" title="Our AI grader did not finish `
    + `this candidate's rubric, so their position is not a like-for-like `
    + `comparison. Read their answers before you decide.">partial</span>`;
}

function provisionalMark(ev) {
  if (!isProvisional(ev)) return '';
  const of = ev.grid_of;
  const marked = ev.grid_marked;
  const detail = (typeof marked === 'number' && typeof of === 'number')
    ? `Only ${marked} of ${of} criteria were marked${
        typeof ev.grid_coverage === 'number'
          ? ` — ${Math.round(ev.grid_coverage * 100)}% of the rubric's weight`
          : ''}, and the total you see is those rows scaled up to 100.`
    : 'The AI did not mark every criterion, and the total you see is the '
      + 'marked rows scaled up to 100.';
  return `<span class="provisional-mark"
    title="${esc(detail)} It is not comparable with a fully marked score and is held off shortlists until it is re-graded.">partial</span>`;
}

/* Which required artefacts this candidate never submitted.
 *
 * Written by the evaluator from our own records rather than from the model's
 * reply, so an empty list means "they handed everything in", not "the model
 * did not mention it". Absent entirely on every verdict marked before the
 * field existed, which reads the same way as an empty one and is correct:
 * those candidates were auto-rejected before grading, so none of them can be
 * one of these. */
const gradedWithout = (ev) => (ev && Array.isArray(ev.graded_without))
  ? ev.graded_without.map((f) => f.replace('_link', '')) : [];

/* The counterpart to provisionalMark, and there for the same reason: nothing
 * about the NUMBER says it was produced without a video.
 *
 * These candidates are auto-rejected at ingest and never reach a bulk grading
 * run, so a score on one of them exists only because a reviewer pressed
 * "Evaluate now" on purpose. That is a deliberate act, and the mark is what
 * tells the next person reading the row that it happened -- and that the
 * missing artefact was already paid for in the grid rather than being an
 * oversight they need to chase. */
function withoutMark(ev) {
  const missing = gradedWithout(ev);
  if (!missing.length) return '';
  const names = missing.join(' and ');
  return `<span class="badge badge-without"
    title="No ${esc(names)} was submitted. The written work was graded normally; where this rubric prices a recording, that row was marked at its 1 anchor. The absence never triggered an auto-fail.">no ${esc(names)}</span>`;
}

/* Evaluations recorded under the old vocabulary. The band is a pure function
 * of the score, so relabelling them is faithful rather than a rewrite -- and
 * leaving one "Hold" in a column of Good/Better reads as a different thing
 * having happened to that candidate. Nothing is written back. */
const LEGACY_REC = {
  Advance: 'Better', Hold: 'Good', Reject: 'Okay', 'Auto-fail': 'Not scored',
};

// Strongest first. Drives the Verdict column's sort, so the order is the one
// the words claim rather than the alphabet's.
const REC_RANK = ['Best', 'Better', 'Good', 'Okay', 'Not scored'];

/* The word for a verdict, and the band it belongs to. Old records are mapped
 * by label; anything the pack stops returning falls through unchanged rather
 * than disappearing. */
function recLabel(ev) {
  const raw = String(ev?.recommendation || '').trim();
  if (!raw) return '';
  if (LEGACY_REC[raw]) return LEGACY_REC[raw];
  // A pre-pack "Strong yes" style verdict, or a band this build has not heard
  // of: shown as recorded.
  return raw;
}

/* The bands come with the rubric so the page never states a bar the server
 * disagrees with. This fallback only covers the moment before that response
 * lands, and matches BANDS in rubric_pack.py. */
const FALLBACK_BANDS = [
  { key: 'best', label: 'Best', min: 85 },
  { key: 'better', label: 'Better', min: 75 },
  { key: 'good', label: 'Good', min: 60 },
  { key: 'okay', label: 'Okay', min: 0 },
];

const bandList = () => (state.rubric?.architecture?.bands?.length
  ? state.rubric.architecture.bands : FALLBACK_BANDS);

/* "85+", "75–84", "below 60" — read off the neighbouring cuts, so adding a band
 * to the pack does not need a second edit here. Bands are ordered high to low. */
function bandSpan(list, i) {
  const above = list[i - 1];
  if (!above) return `${list[i].min}+`;
  return list[i].min <= 0 ? `below ${above.min}` : `${list[i].min}–${above.min - 1}`;
}

/* The band a total landed in, as the reviewer's own words plus the numbers
 * that put it there: "Better 75–84". */
function bandRange(score) {
  const list = bandList();
  const i = list.findIndex((b) => score >= b.min);
  const at = i === -1 ? list.length - 1 : i;
  return `${list[at].label} ${bandSpan(list, at)}`;
}

// Totals land on a tenth (score x weight / 5 rarely comes out whole), and a
// trailing ".0" in a dense table is noise.
const fmtScore = (score) => (typeof score === 'number'
  ? (Number.isInteger(score) ? String(score) : score.toFixed(1)) : '—');

const recClass = (rec) => 'badge-' + String(rec || '').toLowerCase().replace(/\s+/g, '-');

/* --- roles ------------------------------------------------------------ */
