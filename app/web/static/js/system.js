// System/Status page: app info, health, and settings-derived maintenance
// state (updates/backup/logging), similar in spirit to Sonarr/Radarr's
// System -> Status page. Read-only — no backup/update actions are wired up.

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

    const updates = data.updates || {};
    document.getElementById("system-updates-branch").textContent = updates.branch || "—";
    document.getElementById("system-updates-automatic").textContent = boolLabel(updates.automatic);

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

function refreshAll() {
  refreshHealth();
  refreshStatus();
}

document.addEventListener("DOMContentLoaded", () => {
  refreshAll();
  document.getElementById("system-refresh-top").addEventListener("click", refreshAll);
});
