// Dashboard health banner (issue #49): a compact summary of the same
// health data /system/status already exposes, surfaced on the app-home
// page instead of only on the System/Status page — Starr-style persistent
// health visibility without a second backend call.

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

function issueLabel(issue) {
  const key = issue.issue === "read_only" ? "dashboard_health_issue_read_only" : "dashboard_health_issue_missing";
  const template = T[key] || "{path}";
  return template.replace("{path}", issue.path);
}

async function refreshDashboardHealth() {
  const banner = document.getElementById("dashboard-health-banner");
  const text = document.getElementById("dashboard-health-banner-text");
  const action = document.getElementById("dashboard-health-banner-action");
  if (!banner || !text) return;

  try {
    const resp = await fetch("/api/v1/system/status");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const health = data.health || { ok: true, rootFolderIssues: [] };

    banner.hidden = false;
    if (health.ok) {
      banner.classList.remove("health-banner-warn");
      banner.classList.add("muted");
      text.textContent = T.dashboard_health_ok || "All systems healthy";
      if (action) action.hidden = true;
    } else {
      const issues = (health.rootFolderIssues || []).map(issueLabel);
      banner.classList.remove("muted");
      banner.classList.add("health-banner-warn");
      text.innerHTML = `${esc(T.dashboard_health_warn || "Health issues detected")}: ${issues.map(esc).join(", ")}`;
      if (action) action.hidden = false;
    }
  } catch (err) {
    // Non-fatal: the banner just stays hidden if the status call fails.
    console.debug("dashboard health: %s", err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshDashboardHealth();
  const refreshBtn = document.getElementById("dashboard-refresh-top");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshDashboardHealth);
});
