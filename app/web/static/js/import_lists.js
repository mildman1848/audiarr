// Settings > Import Lists (issue #72): generic Starr-style status table over
// GET /api/v1/import-lists, with a per-row Sync action against
// POST /api/v1/import-lists/{id}/sync. Read-only page -- enabling a source
// or editing its URL/token still happens on the Connections page; this is
// purely a status view, so it does not load settings.js.

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

function statusBadge(status) {
  let cls = "badge";
  if (status === "ok") cls = "badge badge-success";
  else if (status === "error") cls = "badge badge-error";
  const label = T[`import_lists_status_${status}`] || status || "—";
  return `<span class="${cls}">${esc(label)}</span>`;
}

function lastResultLabel(source) {
  if (!source.last_sync_at) return "—";
  return (T.import_lists_last_result || "{created} added, {skipped} skipped")
    .replace("{created}", source.last_created)
    .replace("{skipped}", source.last_skipped);
}

async function syncSource(sourceId, button) {
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = T.import_lists_syncing || "Syncing…";
  try {
    const resp = await fetch(`/api/v1/import-lists/${encodeURIComponent(sourceId)}/sync`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const text = `${T.import_lists_sync_error || "Sync failed"} (${data.detail || `HTTP ${resp.status}`})`;
      if (window.AudiarrToast) window.AudiarrToast.error(text);
      return;
    }
    const text = (T.import_lists_last_result || "{created} added, {skipped} skipped")
      .replace("{created}", data.created)
      .replace("{skipped}", data.skipped_existing + data.skipped_no_id);
    if (window.AudiarrToast) window.AudiarrToast.success(text);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.import_lists_sync_error || "Sync failed"} (${err.message})`);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
    loadImportLists();
  }
}

async function loadImportLists() {
  const tbody = document.getElementById("import-lists-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="muted table-loading-row">${esc(T.import_lists_loading)}</td></tr>`;
  try {
    const resp = await fetch("/api/v1/import-lists");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const sources = await resp.json();
    if (!sources.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(T.import_lists_empty || "No import-list sources configured")}</td></tr>`;
      return;
    }
    tbody.innerHTML = sources
      .map(
        (source) => `
      <tr data-source-id="${esc(source.id)}">
        <td>${esc(source.name)}</td>
        <td>${source.enabled ? esc(T.settings_enabled_label) : esc(T.status_not_configured)}</td>
        <td>${statusBadge(source.status)}</td>
        <td>${esc(source.last_sync_at) || "—"}</td>
        <td>${esc(lastResultLabel(source))}${source.last_error ? ` <span class="muted small">(${esc(source.last_error)})</span>` : ""}</td>
        <td><button type="button" class="btn btn-secondary" data-sync-source="${esc(source.id)}">${esc(T.import_lists_sync)}</button></td>
      </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(T.import_lists_load_error)} (${esc(err.message)})</td></tr>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadImportLists();
  const tbody = document.getElementById("import-lists-tbody");
  if (tbody) {
    tbody.addEventListener("click", (e) => {
      const sourceId = e.target.dataset.syncSource;
      if (sourceId) syncSource(sourceId, e.target);
    });
  }
});
