// Activity page: the live SABnzbd queue and recent download history.
//
// Queue refreshes every 5s but only while the tab is visible
// (document.visibilityState === "visible"); history refreshes every 30s and
// also has a manual refresh button. Both tolerate a 503 (no SABnzbd
// configured) by showing an inline link to /settings instead of a toast.

const T = window.AUDIARR_I18N || {};

const QUEUE_INTERVAL_MS = 5000;
const HISTORY_INTERVAL_MS = 30000;

let queueTimer = null;
let historyTimer = null;

// Follows settings.js escapeHtml: escape every value interpolated into innerHTML.
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

// Human-readable byte size. SABnzbd queue fields are already formatted
// strings (e.g. "1.2 GB"); history "size" is likewise a string. Numbers are
// coerced, everything else passes through untouched.
function humanSize(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let size = n;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const rounded = size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1);
  return `${rounded} ${units[unit]}`;
}

// SABnzbd history "completed" is a unix timestamp (seconds).
function humanTime(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isFinite(n) && n > 0) {
    return new Date(n * 1000).toLocaleString();
  }
  return String(value);
}

function queueStatusIcon(status) {
  const s = String(status || "").toLowerCase();
  if (s === "paused") return `<span title="${esc(T.activity_status_paused)}">⏸</span>`;
  if (s === "downloading") return `<span title="${esc(T.activity_status_downloading)}">⤓</span>`;
  return `<span title="${esc(status || "")}">•</span>`;
}

function progressBar(percent) {
  const pct = Math.max(0, Math.min(100, Number(percent) || 0));
  return `
    <div style="background:var(--bg-panel-alt);border:1px solid var(--border-color);border-radius:999px;height:0.6rem;overflow:hidden;min-width:6rem;">
      <div style="background:var(--audiarr-accent);height:100%;width:${pct}%;"></div>
    </div>
    <span class="muted small">${pct.toFixed(0)}%</span>`;
}

function historyStatusBadge(status) {
  const s = String(status || "").toLowerCase();
  let cls = "badge";
  if (s === "completed") cls = "badge badge-completed";
  else if (s === "failed") cls = "badge badge-failed";
  return `<span class="${cls}">${esc(status || "—")}</span>`;
}

// Inline 503 warning: config missing, point the user at /settings.
function renderConfigWarning(container) {
  container.innerHTML = `
    <div class="card danger-card">
      <p>${esc(T.activity_config_missing)}</p>
      <p><a href="/settings">${esc(T.activity_config_link)}</a></p>
    </div>`;
}

async function refreshQueue() {
  const container = document.getElementById("activity-queue");
  try {
    const resp = await fetch("/api/v1/activity/queue");
    if (resp.status === 503) {
      renderConfigWarning(container);
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const slots = data.slots || [];
    console.debug("activity queue: %d slot(s)", slots.length);

    if (!slots.length) {
      container.innerHTML = `<p class="muted">${esc(T.activity_queue_empty)}</p>`;
      return;
    }

    const rows = slots
      .map(
        (s) => `
        <tr>
          <td>${queueStatusIcon(s.status)}</td>
          <td>${esc(s.filename)}</td>
          <td>${esc(s.category || "—")}</td>
          <td>${progressBar(s.progress_percent)}</td>
          <td>${esc(humanSize(s.size_left))}</td>
          <td>${esc(s.time_left || "—")}</td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.activity_col_status)}</th>
            <th>${esc(T.activity_col_filename)}</th>
            <th>${esc(T.activity_col_category)}</th>
            <th>${esc(T.activity_col_progress)}</th>
            <th>${esc(T.activity_col_size_left)}</th>
            <th>${esc(T.activity_col_time_left)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.activity_queue_error)} (${esc(err.message)})</p>`;
  }
}

async function refreshHistory() {
  const container = document.getElementById("activity-history");
  try {
    const resp = await fetch("/api/v1/activity/history?limit=50");
    if (resp.status === 503) {
      renderConfigWarning(container);
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const slots = data.slots || [];
    console.debug("activity history: %d slot(s)", slots.length);

    if (!slots.length) {
      container.innerHTML = `<p class="muted">${esc(T.activity_history_empty)}</p>`;
      return;
    }

    const rows = slots
      .map(
        (s) => `
        <tr>
          <td>${historyStatusBadge(s.status)}</td>
          <td>${esc(s.name)}</td>
          <td>${esc(s.category || "—")}</td>
          <td>${esc(humanSize(s.size))}</td>
          <td>${esc(humanTime(s.completed_at))}</td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.activity_col_status)}</th>
            <th>${esc(T.activity_col_name)}</th>
            <th>${esc(T.activity_col_category)}</th>
            <th>${esc(T.activity_col_size)}</th>
            <th>${esc(T.activity_col_completed_at)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.activity_history_error)} (${esc(err.message)})</p>`;
  }
}

// Segmented control: switches which card is visible. Pure show/hide, no
// extra fetch — both sections already poll independently.
function setActivityTab(tab) {
  const isQueue = tab === "queue";
  document.getElementById("activity-queue-section").hidden = !isQueue;
  document.getElementById("activity-history-section").hidden = isQueue;

  const queueTabBtn = document.getElementById("activity-tab-queue");
  const historyTabBtn = document.getElementById("activity-tab-history");
  queueTabBtn.classList.toggle("active", isQueue);
  queueTabBtn.setAttribute("aria-selected", String(isQueue));
  historyTabBtn.classList.toggle("active", !isQueue);
  historyTabBtn.setAttribute("aria-selected", String(!isQueue));
}

// Only poll the queue while the tab is visible; resume immediately when it
// becomes visible again.
function tickQueue() {
  if (document.visibilityState === "visible") {
    refreshQueue();
  } else {
    console.debug("activity queue: tab hidden, skipping refresh");
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === "visible") {
    refreshQueue();
    refreshHistory();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshQueue();
  refreshHistory();

  queueTimer = setInterval(tickQueue, QUEUE_INTERVAL_MS);
  historyTimer = setInterval(() => {
    if (document.visibilityState === "visible") refreshHistory();
  }, HISTORY_INTERVAL_MS);

  document.addEventListener("visibilitychange", handleVisibilityChange);
  document.getElementById("activity-refresh-top").addEventListener("click", () => {
    refreshQueue();
    refreshHistory();
  });

  document.getElementById("activity-tab-queue").addEventListener("click", () => setActivityTab("queue"));
  document.getElementById("activity-tab-history").addEventListener("click", () => setActivityTab("history"));
});

// Clean up timers if the page is torn down (e.g. bfcache navigation).
window.addEventListener("pagehide", () => {
  if (queueTimer) clearInterval(queueTimer);
  if (historyTimer) clearInterval(historyTimer);
});
