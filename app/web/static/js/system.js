// System/Status page: app info, health, and settings-derived maintenance
// state (updates/backup/logging), similar in spirit to Sonarr/Radarr's
// System -> Status page. Updates (issue #33) are display-only: the "Check
// for updates" button runs a single GitHub releases lookup and shows the
// result; Audiarr never auto-updates.

const T = window.AUDIARR_I18N || {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function boolLabel(value) {
  return value ? T.settings_enabled_label : T.status_not_configured;
}

async function refreshHealth() {
  const el = document.getElementById("system-health-status");
  try {
    const resp = await fetch("/health");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const ok = data.status === "ok";
    el.textContent = ok ? T.status_ok : String(data.status || "—");
    el.classList.toggle("muted", !ok);
  } catch (err) {
    el.textContent = `${T.activity_queue_error} (${esc(err.message)})`;
  }
}

async function refreshStatus() {
  try {
    const resp = await fetch("/api/v1/system/status");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    console.debug("system status: %o", data);

    document.getElementById("system-app-name").textContent = data.appName || "—";
    document.getElementById("system-version").textContent = data.version || "—";
    document.getElementById("system-python-version").textContent = data.pythonVersion || "—";
    document.getElementById("system-os-name").textContent = data.osName || "—";

    renderUpdates(data.updates || {});

    const backup = data.backup || {};
    document.getElementById("system-backup-folder").textContent = backup.folder || "—";
    document.getElementById("system-backup-interval").textContent =
      backup.intervalHours != null ? String(backup.intervalHours) : "—";
    document.getElementById("system-backup-retention").textContent =
      backup.retentionCopies != null ? String(backup.retentionCopies) : "—";

    const logging = data.logging || {};
    document.getElementById("system-logging-level").textContent = logging.level || "—";
    document.getElementById("system-logging-retention").textContent =
      logging.retentionDays != null ? String(logging.retentionDays) : "—";
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.settings_load_error} (${err.message})`);
  }
}

// Render the updates dict shared by GET /api/v1/system/status ("updates")
// and POST /api/v1/system/update-check (same camelCase shape).
function renderUpdates(updates) {
  document.getElementById("system-updates-branch").textContent = updates.branch || "—";
  document.getElementById("system-updates-automatic").textContent = boolLabel(updates.automatic);
  document.getElementById("system-updates-check-enabled").textContent = boolLabel(updates.checkEnabled);
  document.getElementById("system-updates-latest-version").textContent = updates.latestVersion || "—";
  document.getElementById("system-updates-last-checked").textContent = updates.lastCheckedAt || "—";
  document.getElementById("system-updates-last-error").textContent = updates.lastError || "—";

  const btn = document.getElementById("system-update-check-btn");
  if (btn) btn.disabled = updates.checkEnabled === false;

  const banner = document.getElementById("system-update-banner");
  if (!banner) return;
  if (updates.updateAvailable && updates.latestUrl) {
    banner.hidden = false;
    banner.innerHTML = "";
    const text = document.createElement("span");
    text.textContent = `${T.system_update_available} (${esc(updates.latestVersion || "")}) — `;
    banner.appendChild(text);
    const link = document.createElement("a");
    link.href = updates.latestUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = updates.latestName || updates.latestVersion || T.system_update_release_notes;
    banner.appendChild(link);
  } else {
    banner.hidden = true;
  }
}

async function runUpdateCheck() {
  const btn = document.getElementById("system-update-check-btn");
  const msg = document.getElementById("system-update-check-msg");
  if (!btn) return;
  btn.disabled = true;
  const restoreLabel = btn.textContent;
  btn.textContent = T.system_update_checking;
  if (msg) {
    msg.hidden = false;
    msg.textContent = T.system_update_checking;
  }
  try {
    const resp = await fetch("/api/v1/system/update-check", { method: "POST" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderUpdates(data);
    if (msg) {
      msg.textContent = data.enabled
        ? data.lastError
          ? `${T.system_update_check_error} (${esc(data.lastError)})`
          : T.system_update_check_done
        : T.system_update_check_disabled;
    }
  } catch (err) {
    if (msg) msg.textContent = `${T.system_update_check_error} (${esc(err.message)})`;
    btn.disabled = false;
  } finally {
    btn.textContent = restoreLabel;
  }
}

// -- Status/Tasks/Events/Logs tabs (issue #49, #73) ------------------------------
//
// Client-side only, ARIA tablist pattern with arrow-key navigation, active
// tab persisted via ?tab= so it survives reloads and is directly hittable
// by browser smoke (e.g. /system/status?tab=events).

const SYSTEM_TAB_IDS = ["status", "tasks", "events", "logs"];

function tabFromUrl() {
  const tab = new URLSearchParams(window.location.search).get("tab");
  return SYSTEM_TAB_IDS.includes(tab) ? tab : "status";
}

function currentSystemTab() {
  return (
    SYSTEM_TAB_IDS.find((id) => {
      const panel = document.getElementById(`system-panel-${id}`);
      return panel && !panel.hidden;
    }) || "status"
  );
}

function setSystemTab(tab, { updateUrl = true } = {}) {
  const target = SYSTEM_TAB_IDS.includes(tab) ? tab : "status";

  SYSTEM_TAB_IDS.forEach((id) => {
    const isActive = id === target;
    const btn = document.getElementById(`system-tab-${id}`);
    const panel = document.getElementById(`system-panel-${id}`);
    if (btn) {
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
      btn.tabIndex = isActive ? 0 : -1;
    }
    if (panel) panel.hidden = !isActive;
  });

  if (updateUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", target);
    window.history.replaceState(null, "", url);
  }

  if (target === "tasks") refreshTasks();
  if (target === "events") refreshEvents();
  if (target === "logs") refreshLogs();
}

function setupSystemTabs() {
  const buttons = SYSTEM_TAB_IDS.map((id) => document.getElementById(`system-tab-${id}`)).filter(Boolean);
  if (!buttons.length) return;

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => setSystemTab(btn.dataset.systemTab));
    btn.addEventListener("keydown", (event) => {
      const currentIndex = SYSTEM_TAB_IDS.indexOf(btn.dataset.systemTab);
      let nextIndex = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextIndex = (currentIndex + 1) % SYSTEM_TAB_IDS.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextIndex = (currentIndex - 1 + SYSTEM_TAB_IDS.length) % SYSTEM_TAB_IDS.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = SYSTEM_TAB_IDS.length - 1;
      }
      if (nextIndex === null) return;
      event.preventDefault();
      const nextId = SYSTEM_TAB_IDS[nextIndex];
      setSystemTab(nextId);
      const nextBtn = document.getElementById(`system-tab-${nextId}`);
      if (nextBtn) nextBtn.focus();
    });
  });

  setSystemTab(tabFromUrl(), { updateUrl: false });
}

// -- Tasks tab: schedule overview for the five background schedulers (issues
// #49, #73). Interval/historical last-run come from GET /api/v1/settings and
// GET /api/v1/system/backup; live state (status, next run, last completed run)
// comes from GET /api/v1/system/tasks, which reports what the running process
// actually scheduled. Next run is shown only when the process reports one --
// never guessed from settings -- and runtime timestamps reset on restart.
// The only manual action is "Backup now" (existing POST /api/v1/system/backup);
// imports, SAB grabs and wanted searches are never triggered from the UI.

function formatIntervalMinutes(minutes) {
  return minutes > 0 ? `${minutes} ${T.tasks_unit_minutes || "min"}` : T.tasks_interval_disabled || "—";
}

function formatIntervalHours(hours) {
  return hours > 0 ? `${hours} ${T.tasks_unit_hours || "h"}` : T.tasks_interval_disabled || "—";
}

// Persisted timestamps are "YYYY-MM-DD HH:MM:SS" (UTC, no zone); the API and
// backup manifests use ISO-8601 with an offset. Parse both to a Date.
function parseUtcTimestamp(value) {
  if (!value) return null;
  let text = String(value).trim().replace(" ", "T");
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatUtcTimestamp(date) {
  return date ? date.toISOString().slice(0, 19).replace("T", " ") : "—";
}

// Latest valid timestamp among live and historical candidates.
function latestTimestamp(...values) {
  const dates = values.map(parseUtcTimestamp).filter(Boolean);
  if (!dates.length) return null;
  return new Date(Math.max(...dates.map((d) => d.getTime())));
}

// Live intervals are reported in minutes (backups: hours * 60); show whole
// hours as hours so the value matches the configured interval.
function formatLiveInterval(minutes) {
  return minutes >= 60 && minutes % 60 === 0
    ? formatIntervalHours(minutes / 60)
    : formatIntervalMinutes(minutes);
}

function taskStatusLabel(state) {
  switch (state) {
    case "scheduled":
      return T.tasks_status_scheduled || "Scheduled";
    case "running":
      return T.tasks_status_running || "Running";
    case "stopped":
      return T.tasks_status_stopped || "Stopped";
    default:
      return T.tasks_status_not_started || "Not running";
  }
}

function setTasksMessage(text) {
  const msg = document.getElementById("system-tasks-msg");
  if (!msg) return;
  msg.hidden = !text;
  msg.textContent = text || "";
}

async function refreshTasks() {
  const tbody = document.getElementById("system-tasks-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="muted table-loading-row">${esc(T.system_tasks_loading)}</td></tr>`;
  try {
    const [settingsResp, backupResp, tasksResp] = await Promise.all([
      fetch("/api/v1/settings"),
      fetch("/api/v1/system/backup"),
      fetch("/api/v1/system/tasks"),
    ]);
    if (!settingsResp.ok) throw new Error(`HTTP ${settingsResp.status}`);
    if (!tasksResp.ok) throw new Error(`HTTP ${tasksResp.status}`);
    const s = await settingsResp.json();
    const backupData = backupResp.ok ? await backupResp.json() : { backups: [] };
    const lastBackup = (backupData.backups || [])[0];
    const live = {};
    ((await tasksResp.json()).tasks || []).forEach((task) => {
      live[task.id] = task;
    });

    // Configured interval and persisted last-run per task; the live state
    // is merged in below.
    const rows = [
      {
        id: "import_scan",
        name: T.tasks_task_import_scan || "Import scan",
        interval: formatIntervalMinutes(s.media_management.import_scan_interval_minutes),
        historical: s.media_management.last_scheduled_scan_at,
      },
      {
        id: "sab_auto_import",
        name: T.tasks_task_sab_auto_import || "SAB auto-import",
        interval: s.media_management.sab_auto_import_enabled
          ? formatIntervalMinutes(s.media_management.sab_auto_import_interval_minutes)
          : T.tasks_interval_disabled || "—",
        historical: null,
      },
      {
        id: "metadata_refresh",
        name: T.tasks_task_metadata_backfill || "Metadata backfill",
        interval: formatIntervalMinutes(s.metadata.refresh_interval_minutes),
        historical: s.metadata.last_scheduled_refresh_at,
      },
      {
        id: "wanted_search",
        name: T.tasks_task_wanted_search || "Wanted search",
        interval: formatIntervalMinutes(s.wanted.search_interval_minutes),
        historical: s.wanted.last_scheduled_search_at,
      },
      {
        id: "backup",
        name: T.tasks_task_backups || "Backups",
        interval: formatIntervalHours(s.backup.interval_hours),
        historical: lastBackup ? lastBackup.created_at : null,
      },
    ];

    tbody.innerHTML = rows
      .map((r) => {
        const task = live[r.id] || { state: "not_started" };
        const interval = task.intervalMinutes > 0 ? formatLiveInterval(task.intervalMinutes) : r.interval;
        const lastRun = formatUtcTimestamp(latestTimestamp(task.lastRunAt, r.historical));
        const nextRun = formatUtcTimestamp(parseUtcTimestamp(task.nextRunAt));
        const action = task.manualTrigger
          ? `<button type="button" class="btn btn-secondary" data-task-action="backup-now">${esc(T.tasks_backup_now)}</button>`
          : "—";
        return `
        <tr data-task-id="${esc(r.id)}">
          <td>${esc(r.name)}</td>
          <td>${esc(interval)}</td>
          <td class="system-task-time">${esc(lastRun)}</td>
          <td class="system-task-time">${esc(nextRun)}</td>
          <td>${esc(taskStatusLabel(task.state))}</td>
          <td>${action}</td>
        </tr>`;
      })
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(T.settings_load_error)} (${esc(err.message)})</td></tr>`;
  }
}

// "Backup now": the one safe manual task action. The button is re-rendered by
// refreshTasks(), so the click is handled via delegation on the table body.
async function runBackupNow(btn) {
  btn.disabled = true;
  setTasksMessage(T.tasks_backup_running);
  try {
    const resp = await fetch("/api/v1/system/backup", { method: "POST" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    setTasksMessage(T.tasks_backup_done);
    if (window.AudiarrToast) window.AudiarrToast.success(T.tasks_backup_done);
  } catch (err) {
    const text = `${T.tasks_backup_error} (${err.message})`;
    setTasksMessage(text);
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  } finally {
    btn.disabled = false;
    // Re-render the rows (fresh button) and the Status-tab backup info.
    refreshTasks();
    refreshStatus();
  }
}

function setupTaskActions() {
  const tbody = document.getElementById("system-tasks-tbody");
  if (!tbody) return;
  tbody.addEventListener("click", (event) => {
    const btn = event.target.closest('[data-task-action="backup-now"]');
    if (btn && !btn.disabled) runBackupNow(btn);
  });
}

// -- Events tab: read-only import-jobs audit trail (existing
// GET /api/v1/import/jobs, already ordered newest first).

function eventStatusBadge(status) {
  const s = String(status || "").toLowerCase();
  let cls = "badge";
  if (s === "completed") cls = "badge badge-completed";
  else if (s === "failed") cls = "badge badge-failed";
  return `<span class="${cls}">${esc(status || "—")}</span>`;
}

async function refreshEvents() {
  const tbody = document.getElementById("system-events-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="4" class="muted table-loading-row">${esc(T.system_events_loading)}</td></tr>`;
  try {
    const resp = await fetch("/api/v1/import/jobs");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const jobs = await resp.json();
    console.debug("system events: %d job(s)", jobs.length);

    if (!jobs.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="muted">${esc(T.events_empty || "No import events yet")}</td></tr>`;
      return;
    }

    // matched title isn't tracked on import_jobs rows today -- render "—"
    // rather than inventing data (see docs/design/starr-ui-parity.md #49).
    tbody.innerHTML = jobs
      .map(
        (job) => `
        <tr>
          <td>${esc(job.created_at)}</td>
          <td>${esc(job.source_path)}</td>
          <td>${eventStatusBadge(job.status)}</td>
          <td class="muted">—</td>
        </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">${esc(T.settings_load_error)} (${esc(err.message)})</td></tr>`;
  }
}

// -- Logs tab: read-only, newest-first view of the in-memory application log
// buffer (GET /api/v1/system/logs). Messages are already redacted server-side;
// every field is still HTML-escaped before rendering.

const LOGS_LIMIT = 200;

function logLevelBadge(level) {
  const l = String(level || "").toUpperCase();
  let cls = "badge";
  if (l === "ERROR" || l === "CRITICAL") cls = "badge badge-failed";
  else if (l === "WARNING") cls = "badge badge-pending";
  return `<span class="${cls}">${esc(level || "—")}</span>`;
}

async function refreshLogs() {
  const tbody = document.getElementById("system-logs-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="4" class="muted table-loading-row">${esc(T.system_logs_loading)}</td></tr>`;
  try {
    const resp = await fetch(`/api/v1/system/logs?limit=${LOGS_LIMIT}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const records = data.records || [];
    console.debug("system logs: %d record(s)", records.length);

    if (!records.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="muted">${esc(T.logs_empty || "No log records captured yet.")}</td></tr>`;
      return;
    }

    tbody.innerHTML = records
      .map(
        (rec) => `
        <tr>
          <td>${esc(rec.timestamp)}</td>
          <td>${logLevelBadge(rec.level)}</td>
          <td>${esc(rec.logger)}</td>
          <td class="system-log-message">${esc(rec.message)}</td>
        </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">${esc(T.logs_load_error)} (${esc(err.message)})</td></tr>`;
  }
}

function refreshAll() {
  refreshHealth();
  refreshStatus();
  const tab = currentSystemTab();
  if (tab === "tasks") refreshTasks();
  if (tab === "events") refreshEvents();
  if (tab === "logs") refreshLogs();
}

document.addEventListener("DOMContentLoaded", () => {
  setupSystemTabs();
  setupTaskActions();
  refreshAll();
  document.getElementById("system-refresh-top").addEventListener("click", refreshAll);
  const checkBtn = document.getElementById("system-update-check-btn");
  if (checkBtn) checkBtn.addEventListener("click", runUpdateCheck);
  const eventsRefreshBtn = document.getElementById("system-events-refresh-btn");
  if (eventsRefreshBtn) eventsRefreshBtn.addEventListener("click", refreshEvents);
  const logsRefreshBtn = document.getElementById("system-logs-refresh-btn");
  if (logsRefreshBtn) logsRefreshBtn.addEventListener("click", refreshLogs);
});
