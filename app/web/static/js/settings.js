// Settings page: Servarr-style tabbed sections backed by the single settings
// document. Save flow is GET the full document, merge the edited fields, then
// PUT the whole document back (the settings API replaces, it does not patch).
//
// Secrets (webhook / SABnzbd / Prowlarr API keys, the auth password) are
// never rendered back into the page: a stored value only shows as a masked
// placeholder (or, for the password, stays blank), and an empty submit
// keeps the current value. The auth API key is the one exception: the
// settings API only excludes password_hash, so GET returns it in plain
// text and it is shown read-only for copying.

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

async function loginAfterAuthChange(username, password) {
  if (!username || !password) return;
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) throw new Error(`login HTTP ${resp.status}`);
}

// Return the first SABnzbd download client, or a fresh default (not yet
// attached to the document).
function readSab(s) {
  return (
    (s.download_clients || []).find((c) => c.type === "sabnzbd") || {
      name: "SABnzbd",
      type: "sabnzbd",
      url: "",
      api_key: "",
      category: "audiobooks",
      enabled: false,
    }
  );
}

// Return the first Prowlarr indexer, or a fresh default.
function readProwlarr(s) {
  return (
    (s.indexers || []).find((i) => i.type === "prowlarr") || {
      name: "Prowlarr",
      type: "prowlarr",
      url: "",
      api_key: "",
      enabled: false,
    }
  );
}

// Find-or-create the SABnzbd entry inside the document being saved.
function mergeSab(doc) {
  doc.download_clients = doc.download_clients || [];
  let sab = doc.download_clients.find((c) => c.type === "sabnzbd");
  if (!sab) {
    sab = { name: "SABnzbd", type: "sabnzbd", category: "audiobooks", enabled: false };
    doc.download_clients.push(sab);
  }
  return sab;
}

function mergeProwlarr(doc) {
  doc.indexers = doc.indexers || [];
  let idx = doc.indexers.find((i) => i.type === "prowlarr");
  if (!idx) {
    idx = { name: "Prowlarr", type: "prowlarr", enabled: false };
    doc.indexers.push(idx);
  }
  return idx;
}

function populate(s) {
  document.getElementById("host-port").textContent = s.host.port ?? "—";
  document.getElementById("security-method").value = s.auth.method || "none";
  document.getElementById("security-username").value = s.auth.username || "";
  document.getElementById("security-api-key").value = s.auth.api_key || "";
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

  const sab = readSab(s);
  document.getElementById("sab-enabled").checked = Boolean(sab.enabled);
  document.getElementById("sab-name").value = sab.name || "SABnzbd";
  document.getElementById("sab-url").value = sab.url || "";
  document.getElementById("sab-category").value = sab.category || "audiobooks";
  document.getElementById("sab-api-key").placeholder = sab.api_key ? MASK : "";

  const prowlarr = readProwlarr(s);
  document.getElementById("prowlarr-enabled").checked = Boolean(prowlarr.enabled);
  document.getElementById("prowlarr-name").value = prowlarr.name || "Prowlarr";
  document.getElementById("prowlarr-url").value = prowlarr.url || "";
  document.getElementById("prowlarr-api-key").placeholder = prowlarr.api_key ? MASK : "";
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
    // doc.auth.api_key is already present from the GET above; pass it
    // through unchanged unless regenerateApiKey() rewrote it in place.
    doc.auth.method = document.getElementById("security-method").value;
    doc.auth.username = document.getElementById("security-username").value.trim();
    const password = document.getElementById("security-password").value;
    doc.auth.password = password || ""; // empty means keep the stored password
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

    const sab = mergeSab(doc);
    sab.enabled = document.getElementById("sab-enabled").checked;
    sab.name = document.getElementById("sab-name").value.trim() || "SABnzbd";
    sab.url = document.getElementById("sab-url").value.trim();
    sab.category =
      document.getElementById("sab-category").value.trim() || "audiobooks";
    const sabKey = document.getElementById("sab-api-key").value;
    if (sabKey) sab.api_key = sabKey; // empty means keep stored key

    const prowlarr = mergeProwlarr(doc);
    prowlarr.enabled = document.getElementById("prowlarr-enabled").checked;
    prowlarr.name =
      document.getElementById("prowlarr-name").value.trim() || "Prowlarr";
    prowlarr.url = document.getElementById("prowlarr-url").value.trim();
    const prowlarrKey = document.getElementById("prowlarr-api-key").value;
    if (prowlarrKey) prowlarr.api_key = prowlarrKey;

    await putSettings(doc);
    if (doc.auth.method === "forms" && password) {
      await loginAfterAuthChange(doc.auth.username, password);
    }
    for (const id of [
      "conversion-webhook-key",
      "sab-api-key",
      "prowlarr-api-key",
      "security-password",
    ]) {
      document.getElementById(id).value = "";
    }
    populate(await getSettings());
    msg.textContent = "";
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_save_success);
  } catch (err) {
    const text = `${T.settings_save_error} (${err.message})`;
    msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

// Fire a connection test against one of the /api/v1/connections/.../test
// endpoints using the current (unsaved) form values.
async function testConnection(endpoint, urlId, keyId, msgId) {
  const msg = document.getElementById(msgId);
  msg.textContent = T.settings_testing;
  try {
    const body = { url: document.getElementById(urlId).value.trim() };
    const key = document.getElementById(keyId).value;
    if (key) body.api_key = key;
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      msg.textContent = `${T.settings_test_error} (${data.detail || `HTTP ${resp.status}`})`;
      return;
    }
    msg.textContent = `${data.ok ? "✓" : "✗"} ${data.message || ""}`.trim();
  } catch (err) {
    msg.textContent = `${T.settings_test_error} (${err.message})`;
  }
}

// Copy the current API key to the clipboard.
async function copyApiKey() {
  const key = document.getElementById("security-api-key").value;
  try {
    await navigator.clipboard.writeText(key);
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_security_api_key_copy_success);
  } catch (err) {
    if (window.AudiarrToast)
      window.AudiarrToast.error(`${T.settings_save_error} (${err.message})`);
  }
}

// Generate a fresh API key (invalidates sessions + API clients), persist it
// immediately via the normal GET/merge/PUT save path. There is no dedicated
// regenerate endpoint; the settings PUT path persists api_key fine.
async function regenerateApiKey() {
  if (!window.confirm(T.settings_security_api_key_regenerate_confirm)) return;
  const msg = document.getElementById("settings-msg");
  try {
    const doc = await getSettings();
    const newKey = crypto.randomUUID().replace(/-/g, "");
    doc.auth.api_key = newKey;
    await putSettings(doc);
    document.getElementById("security-api-key").value = newKey;
    if (window.AudiarrToast)
      window.AudiarrToast.success(T.settings_security_api_key_regenerated);
  } catch (err) {
    const text = `${T.settings_save_error} (${err.message})`;
    msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
  document.getElementById("sab-test-btn").addEventListener("click", () =>
    testConnection(
      "/api/v1/connections/sabnzbd/test",
      "sab-url",
      "sab-api-key",
      "sab-msg"
    )
  );
  document.getElementById("prowlarr-test-btn").addEventListener("click", () =>
    testConnection(
      "/api/v1/connections/prowlarr/test",
      "prowlarr-url",
      "prowlarr-api-key",
      "prowlarr-msg"
    )
  );
  document
    .getElementById("security-api-key-copy-btn")
    .addEventListener("click", copyApiKey);
  document
    .getElementById("security-api-key-regen-btn")
    .addEventListener("click", regenerateApiKey);
});
