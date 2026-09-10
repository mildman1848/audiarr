// Settings page: Servarr-style tabbed sections backed by the single settings
// document. Save flow is GET the full document, merge the edited fields, then
// PUT the whole document back (the settings API replaces, it does not patch).
//
// The webhook API key is never rendered back into the page: a stored key only
// shows as a masked placeholder, and an empty submit keeps the current value.

const T = window.AUDIARR_I18N || {};
const MASK = "•••••";

async function getSettings() {
  const resp = await fetch("/api/v1/settings");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function putSettings(doc) {
  const resp = await fetch("/api/v1/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(doc),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function populate(s) {
  document.getElementById("host-port").textContent = s.host.port ?? "—";
  document.getElementById("ui-language").value = s.ui.language || "en";
  document.getElementById("metadata-locale").value = s.metadata.audible_locale || "us";
  document.getElementById("provider-order").textContent =
    (s.metadata.provider_order || []).join(" → ") || "—";
  document.getElementById("root-folder-count").textContent =
    (s.root_folders || []).length;
  document.getElementById("conversion-backend").value =
    s.conversion.backend || "disabled";
  document.getElementById("conversion-delete-originals").checked =
    Boolean(s.conversion.delete_originals);
  document.getElementById("conversion-job-timeout").value =
    s.conversion.job_timeout_hours ?? 6;
  document.getElementById("conversion-webhook-key").placeholder =
    s.conversion.webhook_api_key ? MASK : "";
}

async function loadSettings() {
  try {
    populate(await getSettings());
  } catch (err) {
    document.getElementById("settings-msg").textContent =
      `${T.settings_load_error} (${err.message})`;
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const msg = document.getElementById("settings-msg");
  msg.textContent = T.settings_saving;
  try {
    const doc = await getSettings();
    doc.ui.language = document.getElementById("ui-language").value;
    doc.metadata.audible_locale = document.getElementById("metadata-locale").value;
    doc.conversion.backend = document.getElementById("conversion-backend").value;
    doc.conversion.delete_originals = document.getElementById(
      "conversion-delete-originals"
    ).checked;
    const timeout = Number(document.getElementById("conversion-job-timeout").value);
    if (Number.isFinite(timeout) && timeout > 0) {
      doc.conversion.job_timeout_hours = timeout;
    }
    const key = document.getElementById("conversion-webhook-key").value;
    if (key) doc.conversion.webhook_api_key = key; // empty means keep stored value
    await putSettings(doc);
    document.getElementById("conversion-webhook-key").value = "";
    populate(await getSettings());
    msg.textContent = T.settings_save_success;
  } catch (err) {
    msg.textContent = `${T.settings_save_error} (${err.message})`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
});
