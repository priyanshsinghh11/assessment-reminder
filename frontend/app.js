/*
 * Assessment Reminders — dashboard
 *
 * Talks to server.py (see frontend/README.md for the contract):
 *   GET  /api/state          everything needed to render
 *   GET  /api/logs?limit=200 tail of logs/reminder.log
 *   POST /api/run            {mode, limit, emails}
 *                            mode=live answers 202 {job} and the page polls
 *   GET  /api/run/status/:id how a background send is going
 *
 * If the API is unreachable it falls back to MOCK so the page still renders.
 *
 * Opening this page scrapes nothing. It draws the last scan the server holds,
 * however old, and only "Sync portal" goes out to the portal and Workable.
 */

const API = "";  // same origin; set to e.g. "http://localhost:5000" if split

const state = {
  config: {
    reminder_after_business_days: 3,
    reminder_until_business_days: 7,
    max_reminders_per_candidate: 2,
    days_between_reminders: 2,
  },
  jobs: [],
  candidates: [],
  selected: new Set(),
  sort: { key: "business_days_elapsed", dir: "desc" },
  filters: { q: "", job: "", status: "" },
  busy: false,
  mock: false,
  scanned: true,   // false until the server has a scan to show
  stale: false,
};

// ---------------------------------------------------------------------------
// Status derivation — mirrors the rules in reminder.py / config.py
// ---------------------------------------------------------------------------

function deriveStatus(c) {
  if (c.portal_status === "submitted") return "submitted";
  if (c.portal_status === "in_progress") return "started";
  if (c.reminders_sent >= state.config.max_reminders_per_candidate) return "maxed";
  return c.reminders_sent > 0 ? "reminded" : "eligible";
}

const STATUS_LABEL = {
  eligible: "Eligible",
  reminded: "Reminded",
  maxed: "Max reminders",
  started: "Started",
  submitted: "Submitted",
};

// A candidate can actually be sent to only if they have not started and have
// not used up their reminders. Everything else is display-only.
const isSendable = (c) => c.status === "eligible" || c.status === "reminded";

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

// `force` is the Sync portal click: it is the only thing that makes the server
// go out to the portal and Workable. Without it the server replies out of the
// scan it already holds -- or an empty table if nobody has synced yet -- so
// loading this page costs nothing.
async function loadState({ quiet = false, force = false } = {}) {
  if (!quiet) setBusy(true, force ? "Syncing…" : "Loading…");
  try {
    const res = await fetch(`${API}/api/state${force ? "?refresh=1" : ""}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    apply(data);
    state.mock = false;
  } catch (err) {
    if (!state.candidates.length) {
      apply(MOCK);
      state.mock = true;
    }
    toast(`Could not load: ${err.message}`);
  } finally {
    setBusy(false);
  }
  render();
}

function apply(data) {
  state.config = { ...state.config, ...(data.config || {}) };
  state.scanned = data.scanned !== false;
  state.stale = Boolean(data.stale);
  state.jobs = data.jobs || [];
  state.candidates = (data.candidates || []).map((c) => {
    const withStatus = { ...c };
    withStatus.status = deriveStatus(withStatus);
    return withStatus;
  });
  state.lastRun = data.last_run || null;
  state.portal = data.portal || null;

  // Drop selections for candidates that no longer qualify.
  const live = new Set(state.candidates.filter(isSendable).map((c) => c.email));
  state.selected = new Set([...state.selected].filter((e) => live.has(e)));
}

// Apply what a live send just changed, without asking the server to rescan.
// A send can only ever move a candidate along the reminder count -- it cannot
// change who is in the window or who has started -- so this is the whole diff.
function markReminded(recorded) {
  if (!recorded.length) return;
  const now = new Date().toISOString();

  for (const c of state.candidates) {
    // Matched on the pair state_key() is built from: one person can hold a row
    // per posting, but only the rows sharing the assignment were reminded.
    const hit = recorded.some(
      (r) => r.email === c.email && r.portal_job_id === c.portal_job_id
    );
    if (!hit) continue;

    c.reminders_sent += 1;
    c.last_reminder_at = now;
    c.status = deriveStatus(c);
    state.selected.delete(c.email);
  }
}

async function loadLogs() {
  const el = document.getElementById("log");
  try {
    const res = await fetch(`${API}/api/logs?limit=200`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    el.textContent = data.lines.length ? data.lines.join("\n") : "(log is empty)";
  } catch {
    el.textContent = state.mock ? MOCK_LOG.join("\n") : "Could not load logs.";
  }
  el.scrollTop = el.scrollHeight;
}

async function runMode(mode, { emails = null, limit = null, confirmMsg = null } = {}) {
  if (state.busy) return;
  if (confirmMsg && !confirm(confirmMsg)) return;

  const busyLabel =
    { preview: "Printing…", "dry-run": "Dry run…", live: "Sending…" }[mode] || "Working…";
  setBusy(true, busyLabel);

  try {
    const res = await fetch(`${API}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, emails, limit }),
    });
    let data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);

    // A live send comes back 202 with a job id rather than a result: the batch
    // is minutes of Brevo calls, and holding the request open for it meant any
    // proxy in between timed out and showed a failure while the emails kept
    // going. Everything else still answers in one round trip.
    if (res.status === 202 && data.job) {
      toast(data.message || "Sending…");
      data = await waitForRun(data.job);
    }

    toast(data.message || "Done");
    // No reload here. Reloading meant a second full scan on top of the one the
    // run itself did, for a table whose only change we already know about:
    // the rows that were just sent to. Preview and dry-run record nothing, so
    // they change nothing at all. `state` only comes back when the server had
    // to rescan, which is the one case where this page is behind it.
    if (data.state) apply(data.state);
    markReminded(data.recorded || []);
    render();
    await loadLogs();
  } catch (err) {
    toast(`Failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

/**
 * Poll a background send until it finishes, and resolve to its result.
 *
 * Throws on a failed run so the caller's existing catch reports it the same
 * way a synchronous failure was always reported.
 *
 * Every third poll refreshes the log pane, so a long send shows progress
 * rather than a spinner and a promise that something is happening.
 */
async function waitForRun(jobId) {
  const INTERVAL_MS = 2000;
  let ticks = 0;

  for (;;) {
    await new Promise((r) => setTimeout(r, INTERVAL_MS));

    let job;
    try {
      const res = await fetch(`${API}/api/run/status/${encodeURIComponent(jobId)}`);
      job = await res.json();
      if (!res.ok) throw new Error(job.error || res.status);
    } catch (err) {
      // A dropped poll is not a failed send -- the run is on the server, not
      // in this tab. Keep asking; a genuinely gone job answers 404 above and
      // throws out of here with a message that says so.
      continue;
    }

    if (job.state === "running") {
      if (++ticks % 3 === 0) await loadLogs();
      continue;
    }
    if (job.state === "failed") throw new Error(job.error || "The send stopped early.");
    return job;
  }
}

function setBusy(busy, label) {
  state.busy = busy;
  document.querySelectorAll("button.btn").forEach((b) => (b.disabled = busy));
  const sync = document.getElementById("syncBtn");
  sync.textContent = busy ? label || "Working…" : "Sync portal";
  if (!busy) renderSendBar();
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render() {
  renderMeta();
  renderStats();
  renderJobFilter();
  renderTable();
  renderSendBar();
}

function renderMeta() {
  const cfg = state.config;
  const bits = [];
  // Nothing scans on its own any more, so how old this picture is matters:
  // say it plainly rather than letting a day-old table look current.
  if (!state.scanned) {
    bits.push("Not synced yet — click Sync portal");
  } else {
    bits.push(
      `Synced ${fmtWhen(state.lastRun)}` + (state.stale ? " (out of date)" : "")
    );
  }
  bits.push(
    `window ${cfg.reminder_after_business_days}–${cfg.reminder_until_business_days} business days`
  );
  bits.push(`max ${cfg.max_reminders_per_candidate} reminders`);
  if (state.portal) bits.push(`${state.portal.total} in portal`);
  if (state.mock) bits.push("API unreachable — showing sample data");
  document.getElementById("runMeta").textContent = bits.join(" · ");
}

function renderStats() {
  const c = state.candidates;
  const count = (s) => c.filter((x) => x.status === s).length;
  const cards = [
    { label: "In window", value: c.length },
    { label: "Eligible now", value: count("eligible"), cls: "is-accent" },
    { label: "Reminded", value: count("reminded") },
    { label: "Max reminders", value: count("maxed") },
    { label: "Started", value: count("started") },
    { label: "Submitted", value: count("submitted") },
  ];
  document.getElementById("stats").innerHTML = cards
    .map(
      (s) => `<div class="stat ${s.cls || ""}">
        <div class="stat-label">${s.label}</div>
        <div class="stat-value">${s.value}</div>
      </div>`
    )
    .join("");
}

function renderJobFilter() {
  const sel = document.getElementById("jobFilter");
  if (sel.options.length > 1) return;
  for (const j of state.jobs) {
    const opt = document.createElement("option");
    opt.value = j.shortcode;
    opt.textContent = j.label;
    sel.appendChild(opt);
  }
}

function visibleRows() {
  const { q, job, status } = state.filters;
  const needle = q.trim().toLowerCase();

  const rows = state.candidates.filter((c) => {
    if (job && c.job_shortcode !== job) return false;
    if (status && c.status !== status) return false;
    if (needle && !`${c.name} ${c.email}`.toLowerCase().includes(needle)) return false;
    return true;
  });

  const { key, dir } = state.sort;
  const mul = dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    const av = a[key] ?? "";
    const bv = b[key] ?? "";
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * mul;
    return String(av).localeCompare(String(bv)) * mul;
  });
  return rows;
}

function renderTable() {
  const rows = visibleRows();
  const max = state.config.max_reminders_per_candidate;

  document.getElementById("tbody").innerHTML = rows
    .map((c) => {
      const sendable = isSendable(c);
      const checked = state.selected.has(c.email) ? " checked" : "";
      return `<tr class="${sendable ? "" : "row-inert"}">
        <td class="shrink">${
          sendable
            ? `<input type="checkbox" class="rowsel" data-email="${esc(c.email)}"${checked}>`
            : ""
        }</td>
        <td>
          <div class="cand-name">${esc(c.name)}</div>
          <div class="cand-email">${esc(c.email)}</div>
        </td>
        <td class="dim">${esc(c.job_title || c.job_shortcode)}</td>
        <td class="dim nowrap">${esc(c.stage || "—")}</td>
        <td class="nowrap dim">${fmtDate(c.applied_at)}</td>
        <td class="num">${c.business_days_elapsed}</td>
        <td class="dim">${c.portal_status ? esc(c.portal_status.replace("_", " ")) : "—"}</td>
        <td>${pips(c.reminders_sent, max)}</td>
        <td><span class="badge badge-${c.status}">${STATUS_LABEL[c.status]}</span></td>
      </tr>`;
    })
    .join("");

  document.getElementById("empty").hidden = rows.length > 0;

  document.querySelectorAll("th[data-sort]").forEach((th) => {
    if (th.dataset.sort === state.sort.key) {
      th.setAttribute("aria-sort", state.sort.dir === "asc" ? "ascending" : "descending");
    } else {
      th.removeAttribute("aria-sort");
    }
  });

  const visibleSendable = rows.filter(isSendable);
  const all = document.getElementById("selectAll");
  all.checked =
    visibleSendable.length > 0 &&
    visibleSendable.every((c) => state.selected.has(c.email));
}

const isTestMode = () => document.getElementById("testMode").checked;

function renderSendBar() {
  const n = state.selected.size;
  const eligible = state.candidates.filter(isSendable).length;
  const test = isTestMode();

  document.getElementById("selCount").textContent = n
    ? `${n} selected`
    : `${eligible} candidate${eligible === 1 ? "" : "s"} can be sent to`;

  const selBtn = document.getElementById("sendSelBtn");
  const allBtn = document.getElementById("sendAllBtn");

  selBtn.textContent = test
    ? n ? `Print ${n} to terminal` : "Print to terminal"
    : n ? `Send to ${n} selected` : "Send to selected";
  allBtn.textContent = test ? "Print all eligible" : "Send to all eligible";

  // In test mode the primary button is not doing anything irreversible, so it
  // should not look like the loudest thing on the page.
  selBtn.classList.toggle("btn-primary", !test);

  selBtn.disabled = state.busy || n === 0;
  allBtn.disabled = state.busy || eligible === 0;
}

function pips(sent, max) {
  let out = `<span class="pips" title="${sent} of ${max} reminders sent">`;
  for (let i = 0; i < max; i++) out += `<span class="pip${i < sent ? " on" : ""}"></span>`;
  return out + "</span>";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
  );
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fmtWhen(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return fmtDate(iso);
}

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 5000);
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

function readLimit() {
  const v = parseInt(document.getElementById("limitInput").value, 10);
  return Number.isFinite(v) && v > 0 ? v : null;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

// The one control that goes out to the portal. Everything else on this page
// works from what this click brought back.
document.getElementById("syncBtn")
  .addEventListener("click", () => loadState({ force: true }).then(loadLogs));

document.querySelector('[data-mode="dry-run"]').addEventListener("click", () =>
  runMode("dry-run", { limit: readLimit() })
);

document.getElementById("testMode").addEventListener("change", renderSendBar);

document.getElementById("sendAllBtn").addEventListener("click", () => {
  const limit = readLimit();
  const eligible = state.candidates.filter(isSendable).length;
  const n = limit ? Math.min(limit, eligible) : eligible;

  if (isTestMode()) {
    runMode("preview", { limit });
    return;
  }
  runMode("live", {
    limit,
    confirmMsg:
      `Send real reminder emails to up to ${n} candidate(s)?\n\n` +
      `This cannot be undone.`,
  });
});

document.getElementById("sendSelBtn").addEventListener("click", () => {
  const emails = [...state.selected];

  if (isTestMode()) {
    // No confirm and no limit: nothing leaves the machine, and the cap would
    // only truncate the preview you asked to see.
    runMode("preview", { emails });
    return;
  }
  runMode("live", {
    emails,
    confirmMsg:
      `Send real reminder emails to ${emails.length} selected candidate(s)?\n\n` +
      `This cannot be undone.`,
  });
});

document.getElementById("tbody").addEventListener("change", (e) => {
  const box = e.target.closest(".rowsel");
  if (!box) return;
  if (box.checked) state.selected.add(box.dataset.email);
  else state.selected.delete(box.dataset.email);
  renderSendBar();
  renderTable();
});

document.getElementById("selectAll").addEventListener("change", (e) => {
  const visibleSendable = visibleRows().filter(isSendable);
  for (const c of visibleSendable) {
    if (e.target.checked) state.selected.add(c.email);
    else state.selected.delete(c.email);
  }
  renderTable();
  renderSendBar();
});

document.getElementById("search").addEventListener(
  "input",
  debounce((e) => {
    state.filters.q = e.target.value;
    renderTable();
  }, 150)
);

document.getElementById("jobFilter").addEventListener("change", (e) => {
  state.filters.job = e.target.value;
  renderTable();
});

document.getElementById("statusFilter").addEventListener("change", (e) => {
  state.filters.status = e.target.value;
  renderTable();
});

document.querySelectorAll("th[data-sort]").forEach((th) =>
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) {
      state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
    } else {
      state.sort = { key, dir: "asc" };
    }
    renderTable();
  })
);

document.getElementById("refreshLog").addEventListener("click", loadLogs);

// ---------------------------------------------------------------------------
// Mock data — used only when /api/state is unreachable
// ---------------------------------------------------------------------------

const MOCK = {
  last_run: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
  config: {
    reminder_after_business_days: 3,
    reminder_until_business_days: 7,
    max_reminders_per_candidate: 2,
    days_between_reminders: 2,
  },
  portal: { total: 727, submitted: 434, in_progress: 293 },
  jobs: [{ shortcode: "0C6BA6AAA9", label: "Full Stack Developer" }],
  candidates: [
    {
      candidate_id: "c1", name: "Ananya Rao", email: "ananya.rao@example.com",
      job_shortcode: "0C6BA6AAA9", job_title: "Full Stack Developer",
      stage: "Applied", applied_at: "2026-07-29T09:12:00+00:00",
      business_days_elapsed: 7, portal_status: null, reminders_sent: 0,
      last_reminder_at: null, assessment_url: "https://example.com/a",
    },
    {
      candidate_id: "c2", name: "Marcus Bell", email: "marcus.bell@example.com",
      job_shortcode: "0C6BA6AAA9", job_title: "Full Stack Developer",
      stage: "Assessment", applied_at: "2026-07-31T14:40:00+00:00",
      business_days_elapsed: 5, portal_status: null, reminders_sent: 1,
      last_reminder_at: "2026-08-04T09:00:00+00:00",
      assessment_url: "https://example.com/a",
    },
    {
      candidate_id: "c3", name: "Sara Okafor", email: "sara.okafor@example.com",
      job_shortcode: "0C6BA6AAA9", job_title: "Full Stack Developer",
      stage: "Applied", applied_at: "2026-07-30T10:00:00+00:00",
      business_days_elapsed: 6, portal_status: "in_progress", reminders_sent: 0,
      last_reminder_at: null, assessment_url: "https://example.com/a",
    },
    {
      candidate_id: "c4", name: "Daniel Kim", email: "daniel.kim@example.com",
      job_shortcode: "0C6BA6AAA9", job_title: "Full Stack Developer",
      stage: "Assessment", applied_at: "2026-07-29T16:20:00+00:00",
      business_days_elapsed: 7, portal_status: "submitted", reminders_sent: 1,
      last_reminder_at: "2026-08-03T09:00:00+00:00",
      assessment_url: "https://example.com/a",
    },
  ],
};

const MOCK_LOG = [
  "2026-08-07 09:00:01 [INFO] reminder: --- Downloading assessment portal records ---",
  "2026-08-07 09:00:05 [INFO] portal_scraper: Portal: 3902 submission records downloaded.",
  "2026-08-07 09:00:05 [INFO] reminder: --- Job: 0C6BA6AAA9 (Full Stack Developer) ---",
  "2026-08-07 09:00:14 [INFO] workable_scanner: In window: 242  (skipped: 0 wrong stage, 147 outside 3-7 business days)",
  "2026-08-07 09:00:14 [INFO] reminder: In window: 242  |  already started: 3  |  eligible: 239",
];

// ---------------------------------------------------------------------------

loadState().then(loadLogs);
