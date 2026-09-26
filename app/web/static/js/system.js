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

function refreshAll() {
  refreshHealth();
  refreshStatus();
}

document.addEventListener("DOMContentLoaded", () => {
  refreshAll();
  document.getElementById("system-refresh-top").addEventListener("click", refreshAll);
  const checkBtn = document.getElementById("system-update-check-btn");
  if (checkBtn) checkBtn.addEventListener("click", runUpdateCheck);
});
